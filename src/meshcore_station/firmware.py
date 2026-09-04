from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .service import StationService
from .transports.meshcore_serial import resolve_serial_port

StatusCallback = Callable[[dict[str, Any]], Awaitable[None]]
_PROGRESS_RE = re.compile(rb"\((\d+(?:\.\d+)?)\s*%\)")


def build_esptool_commands(port: str, image: Path, mode: str, baud: int) -> list[list[str]]:
    if mode not in {"update", "full"}:
        raise ValueError("Режим прошивки должен быть update или full")
    common = [
        sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", port,
        "--baud", str(baud), "--before", "default_reset", "--after", "hard_reset",
    ]
    commands: list[list[str]] = []
    if mode == "full":
        commands.append([*common, "erase_flash"])
    address = "0x0" if mode == "full" else "0x10000"
    commands.append([
        *common, "write_flash", "--compress", "--flash_size", "keep",
        "--flash_mode", "keep", "--flash_freq", "keep", address, str(image),
    ])
    return commands


class FirmwareFlasher:
    def __init__(
        self,
        station: StationService,
        data_dir: Path,
        configured_port: str,
        baud: int = 460800,
        callback: StatusCallback | None = None,
    ):
        self.station = station
        self.data_dir = data_dir
        self.configured_port = configured_port
        self.baud = baud
        self.callback = callback
        self._task: asyncio.Task[None] | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._closing = False
        self._status: dict[str, Any] = {
            "busy": False, "stage": "idle", "progress": 0, "message": "",
        }

    @property
    def status(self) -> dict[str, Any]:
        return dict(self._status)

    async def _set_status(self, **values: Any) -> None:
        self._status.update(values)
        if self.callback:
            await self.callback(self.status)

    def start(self, image: Path, filename: str, mode: str, port: str) -> None:
        if self._task and not self._task.done():
            raise RuntimeError("Прошивка уже выполняется")
        self._status = {
            "busy": True, "stage": "queued", "progress": 1,
            "message": "Подготовка к прошивке", "filename": filename,
            "mode": mode, "port": port, "started_at": int(time.time()),
        }
        self._task = asyncio.create_task(
            self._flash(image, mode, port), name="meshcore-firmware-flash"
        )

    async def _flash(self, image: Path, mode: str, configured_port: str) -> None:
        try:
            await self._set_status(stage="disconnecting", progress=3, message="Отключение Companion")
            await self.station.pause_radio("firmware")
            port = resolve_serial_port(configured_port)
            self._status["port"] = port
            commands = build_esptool_commands(port, image, mode, self.baud)
            for index, command in enumerate(commands):
                is_erase = mode == "full" and index == 0
                await self._set_status(
                    stage="erasing" if is_erase else "writing",
                    progress=7 if is_erase else (18 if mode == "full" else 7),
                    message="Очистка flash-памяти" if is_erase else "Запись прошивки",
                )
                await self._run(command, is_erase, mode)
            await self._set_status(
                busy=False, stage="complete", progress=100,
                message="Прошивка завершена, Companion перезапускается",
                completed_at=int(time.time()),
            )
        except asyncio.CancelledError:
            await self._set_status(busy=False, stage="cancelled", message="Прошивка прервана")
            raise
        except Exception as exc:
            await self._set_status(
                busy=False, stage="failed", message=str(exc), completed_at=int(time.time())
            )
        finally:
            self._process = None
            image.unlink(missing_ok=True)
            if not self._closing:
                await self.station.resume_radio()

    async def _run(self, command: list[str], is_erase: bool, mode: str) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output = bytearray()
        assert self._process.stdout
        while chunk := await self._process.stdout.read(256):
            output.extend(chunk)
            if len(output) > 16_384:
                del output[:-16_384]
            matches = _PROGRESS_RE.findall(output[-512:])
            if matches and not is_erase:
                raw = min(100.0, float(matches[-1]))
                start = 18 if mode == "full" else 7
                await self._set_status(progress=round(start + raw * (95 - start) / 100))
        return_code = await self._process.wait()
        if return_code:
            detail = output.decode("utf-8", errors="replace").strip().splitlines()
            message = "\n".join(detail[-6:]) if detail else f"esptool завершился с кодом {return_code}"
            raise RuntimeError(message)

    async def close(self) -> None:
        self._closing = True
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
