from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _bluetooth_devices() -> list[dict[str, str]]:
    if not shutil.which("bluetoothctl"):
        return []
    try:
        result = subprocess.run(
            ["bluetoothctl", "devices"], capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    devices = []
    for line in result.stdout.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) == 3 and parts[0] == "Device":
            devices.append({"address": parts[1], "name": parts[2]})
    return devices


def _throttling() -> dict[str, Any]:
    tool = shutil.which("vcgencmd")
    if not tool:
        return {"available": False, "raw": None, "flags": []}
    try:
        result = subprocess.run([tool, "get_throttled"], capture_output=True, text=True, timeout=3, check=False)
        raw = int(result.stdout.strip().split("=", 1)[-1], 16)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"available": True, "raw": None, "flags": ["read_error"]}
    bits = {
        0: "undervoltage_now", 1: "frequency_capped_now", 2: "throttled_now", 3: "soft_temp_limit_now",
        16: "undervoltage_occurred", 17: "frequency_capped_occurred",
        18: "throttling_occurred", 19: "soft_temp_limit_occurred",
    }
    return {"available": True, "raw": f"0x{raw:x}", "flags": [name for bit, name in bits.items() if raw & (1 << bit)]}


def _memory() -> dict[str, int | None]:
    values: dict[str, int] = {}
    text = _read_text(Path("/proc/meminfo"))
    if text:
        for line in text.splitlines():
            name, _, value = line.partition(":")
            try:
                values[name] = int(value.strip().split()[0]) * 1024
            except (ValueError, IndexError):
                continue
    total, available = values.get("MemTotal"), values.get("MemAvailable")
    return {"total": total, "available": available, "used": total - available if total and available else None}


def system_diagnostics(data_dir: Path, serial_ports: list[str]) -> dict[str, Any]:
    usage = shutil.disk_usage(data_dir)
    temperature = _read_text(Path("/sys/class/thermal/thermal_zone0/temp"))
    try:
        temperature_c = round(int(temperature) / 1000, 1) if temperature else None
    except ValueError:
        temperature_c = None
    uptime = _read_text(Path("/proc/uptime"))
    try:
        uptime_seconds = int(float(uptime.split()[0])) if uptime else None
    except (ValueError, IndexError):
        uptime_seconds = None
    try:
        load = [round(value, 2) for value in os.getloadavg()]
    except (AttributeError, OSError):
        load = []
    throttling = _throttling()
    warnings = list(throttling["flags"])
    if temperature_c is not None and temperature_c >= 75:
        warnings.append("temperature_high")
    if usage.free < 512 * 1024 * 1024:
        warnings.append("disk_space_low")
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "hostname": platform.node(),
        "uptime_seconds": uptime_seconds,
        "load_average": load,
        "temperature_c": temperature_c,
        "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
        "memory": _memory(),
        "throttling": throttling,
        "warnings": warnings,
        "database_bytes": (data_dir / "station.db").stat().st_size
        if (data_dir / "station.db").exists() else 0,
        "serial_ports": serial_ports,
        "bluetooth_available": bool(shutil.which("bluetoothctl")),
        "bluetooth_devices": _bluetooth_devices(),
        "checked_at": int(time.time()),
    }
