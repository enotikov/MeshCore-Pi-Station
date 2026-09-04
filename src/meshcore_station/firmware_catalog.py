from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MAX_FIRMWARE_BYTES = 16 * 1024 * 1024


def _https_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Каталог и прошивки должны использовать HTTPS")
    return value


def load_catalog(url: str) -> list[dict[str, Any]]:
    if not url:
        return []
    if url == "builtin":
        payload = Path(__file__).with_name("firmware-catalog.json").read_bytes()
    else:
        request = urllib.request.Request(_https_url(url), headers={"User-Agent": "MeshCore-Pi-Station/0.6"})
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read(512 * 1024 + 1)
    if len(payload) > 512 * 1024:
        raise ValueError("Каталог прошивок слишком большой")
    document = json.loads(payload)
    entries = document.get("firmware", []) if isinstance(document, dict) else []
    result = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("id", ""))
        sha256 = str(item.get("sha256", "")).lower()
        board = str(item.get("board", ""))
        mode = str(item.get("mode", "update"))
        if not identifier or len(sha256) != 64 or board != "heltec-v4" or mode not in {"update", "full"}:
            continue
        int(sha256, 16)
        result.append({
            "id": identifier,
            "name": str(item.get("name") or identifier),
            "version": str(item.get("version") or ""),
            "board": board,
            "mode": mode,
            "url": _https_url(str(item.get("url", ""))),
            "sha256": sha256,
            "filename": Path(urllib.parse.urlparse(str(item["url"])).path).name or f"{identifier}.bin",
        })
    return result


def download_verified(entry: dict[str, Any], destination: Path) -> None:
    digest = hashlib.sha256()
    size = 0
    request = urllib.request.Request(entry["url"], headers={"User-Agent": "MeshCore-Pi-Station/0.6"})
    with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as output:
        while chunk := response.read(64 * 1024):
            size += len(chunk)
            if size > MAX_FIRMWARE_BYTES:
                raise ValueError("Размер прошивки превышает 16 MiB")
            digest.update(chunk)
            output.write(chunk)
    if size < 4096:
        raise ValueError("Файл прошивки слишком мал")
    if digest.hexdigest() != entry["sha256"]:
        raise ValueError("SHA-256 прошивки не совпадает с каталогом")
    if destination.read_bytes()[:1] != b"\xe9":
        raise ValueError("Файл не похож на ESP32 image")
