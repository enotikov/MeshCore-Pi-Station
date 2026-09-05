"""Initial access and bounded, per-peer authentication throttling."""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path


def bootstrap_password(data_dir: Path) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "setup-token"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(24)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(token + "\n")
    return token


class LoginLimiter:
    def __init__(self):
        self.failures: dict[str, tuple[int, float]] = {}

    def blocked(self, peer: str) -> bool:
        now = time.monotonic()
        self.failures = {key: item for key, item in self.failures.items() if item[1] > now}
        return self.failures.get(peer, (0, 0))[0] >= 10

    def failed(self, peer: str) -> None:
        count, deadline = self.failures.get(peer, (0, time.monotonic() + 60))
        if len(self.failures) >= 1024 and peer not in self.failures:
            self.failures.pop(next(iter(self.failures)))
        self.failures[peer] = (count + 1, deadline)

    def success(self, peer: str) -> None:
        self.failures.pop(peer, None)
