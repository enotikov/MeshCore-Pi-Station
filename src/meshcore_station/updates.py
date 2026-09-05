from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import Any


RELEASE_API = "https://api.github.com/repos/enotikov/MeshCore-Pi-Station/releases/latest"
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Unsupported release version: {value}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def parse_release(payload: dict[str, Any], current_version: str) -> dict[str, Any]:
    tag = str(payload.get("tag_name") or "")
    latest = tag.removeprefix("v")
    current = version_tuple(current_version)
    remote = version_tuple(tag)
    assets = []
    for item in payload.get("assets") or []:
        name = str(item.get("name") or "")
        url = str(item.get("browser_download_url") or "")
        if name and url.startswith("https://github.com/"):
            assets.append({"name": name, "url": url, "size": int(item.get("size") or 0)})
    return {
        "current_version": current_version,
        "latest_version": latest,
        "update_available": remote > current,
        "release_url": str(payload.get("html_url") or ""),
        "published_at": payload.get("published_at"),
        "assets": assets,
        "checked_at": int(time.time()),
        "installation_mode": "manual-verified-package",
    }


def check_latest_release(current_version: str, timeout: float = 8.0) -> dict[str, Any]:
    request = urllib.request.Request(
        RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "MeshCore-Pi-Station"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"GitHub returned HTTP {response.status}")
        payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub returned an invalid release document")
    return parse_release(payload, current_version)
