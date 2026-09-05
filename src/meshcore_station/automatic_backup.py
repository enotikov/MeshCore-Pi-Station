from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .database import Database

MAGIC = b"MCPSA1"


class AutomaticBackupManager:
    def __init__(self, database: Database, data_dir: Path):
        self.database = database
        self.directory = data_dir / "automatic-backups"
        self.key_path = data_dir / "automatic-backup.key"
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self.last_error = ""

    def policy(self) -> dict[str, Any]:
        saved = self.database.get_settings()
        return {
            "enabled": bool(saved.get("auto_backup_enabled", False)),
            "interval_hours": int(saved.get("auto_backup_interval_hours", 24)),
            "retain_count": int(saved.get("auto_backup_retain_count", 7)),
        }

    def set_policy(self, enabled: bool, interval_hours: int, retain_count: int) -> dict[str, Any]:
        for key, value in {
            "auto_backup_enabled": enabled,
            "auto_backup_interval_hours": interval_hours,
            "auto_backup_retain_count": retain_count,
        }.items():
            self.database.set_setting(key, value)
        self._prune(retain_count)
        return self.status()

    def _key(self) -> bytes:
        if self.key_path.is_file():
            key = self.key_path.read_bytes()
            if len(key) != 32:
                raise ValueError("Некорректный ключ автоматических копий")
            return key
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        key = AESGCM.generate_key(bit_length=256)
        with os.fdopen(descriptor, "wb") as output:
            output.write(key)
        return key

    def _ensure_directory(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass

    def create(self) -> dict[str, Any]:
        self._ensure_directory()
        timestamp = int(time.time())
        name = time.strftime("station-%Y%m%d-%H%M%S", time.localtime(timestamp)) + f"-{time.time_ns() % 1_000_000_000:09d}.mcpsa"
        target = self.directory / name
        nonce = os.urandom(12)
        plaintext = json.dumps(self.database.export_data(), ensure_ascii=False).encode("utf-8")
        encrypted = AESGCM(self._key()).encrypt(nonce, plaintext, MAGIC)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(MAGIC + nonce + encrypted)
        self.database.set_setting("auto_backup_last_at", timestamp)
        self.database.set_setting("auto_backup_last_error", "")
        self.last_error = ""
        self._prune(self.policy()["retain_count"])
        return self._item(target)

    def decode(self, name: str) -> dict[str, Any]:
        path = self.path(name)
        payload = path.read_bytes()
        if not payload.startswith(MAGIC) or len(payload) < len(MAGIC) + 29:
            raise ValueError("Некорректная автоматическая копия")
        plaintext = AESGCM(self._key()).decrypt(
            payload[len(MAGIC):len(MAGIC) + 12], payload[len(MAGIC) + 12:], MAGIC
        )
        return json.loads(plaintext)

    def path(self, name: str) -> Path:
        if Path(name).name != name or not name.endswith(".mcpsa"):
            raise ValueError("Некорректное имя копии")
        path = self.directory / name
        if not path.is_file():
            raise FileNotFoundError(name)
        return path

    def list(self) -> list[dict[str, Any]]:
        if not self.directory.is_dir():
            return []
        return [self._item(path) for path in sorted(self.directory.glob("station-*.mcpsa"), reverse=True)]

    @staticmethod
    def _item(path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {"name": path.name, "created_at": int(stat.st_mtime), "size": stat.st_size}

    def _prune(self, retain_count: int) -> None:
        for item in self.list()[retain_count:]:
            self.path(item["name"]).unlink()

    def status(self) -> dict[str, Any]:
        policy, items = self.policy(), self.list()
        last_at = int(self.database.get_settings().get("auto_backup_last_at", 0)) or None
        next_at = last_at + policy["interval_hours"] * 3600 if policy["enabled"] and last_at else None
        return {**policy, "last_at": last_at, "next_at": next_at,
                "last_error": self.last_error or self.database.get_settings().get("auto_backup_last_error", ""),
                "backups": items}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="meshcore-automatic-backup")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def run_now(self) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self.create)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            policy = self.policy()
            last = int(self.database.get_settings().get("auto_backup_last_at", 0))
            if policy["enabled"] and time.time() >= last + policy["interval_hours"] * 3600:
                try:
                    await self.run_now()
                except Exception as exc:
                    self.last_error = str(exc)
                    self.database.set_setting("auto_backup_last_error", self.last_error)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60)
            except TimeoutError:
                pass
