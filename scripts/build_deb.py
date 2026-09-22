#!/usr/bin/env python3
"""Build an architecture-independent Debian package without dpkg-deb."""

from __future__ import annotations

import io
import hashlib
import shutil
import tarfile
import tempfile
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "meshcore-pi-station"


def locked_constraints() -> str:
    """Export deterministic pip constraints from the committed uv lock file."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions: dict[str, set[str]] = {}
    for package in lock.get("package", []):
        name, version = package.get("name"), package.get("version")
        if name and version and name != APP_NAME and package.get("source", {}).get("registry"):
            versions.setdefault(str(name), set()).add(str(version))
    conflicting = {name: values for name, values in versions.items() if len(values) != 1}
    if conflicting:
        raise ValueError(f"uv.lock contains ambiguous package versions: {conflicting}")
    return "".join(f"{name}=={next(iter(values))}\n" for name, values in sorted(versions.items()))


def add_tree(archive: tarfile.TarFile, source: Path, destination: str) -> None:
    for path in [source, *sorted(source.rglob("*"))]:
        target = Path(destination, path.relative_to(source)).as_posix()
        info = archive.gettarinfo(str(path), arcname=target)
        if path.is_dir():
            info.mode = 0o755
        elif path.suffix == ".sh":
            info.mode = 0o755
        else:
            info.mode = 0o644
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        if path.is_file():
            with path.open("rb") as handle:
                archive.addfile(info, handle)
        else:
            archive.addfile(info)


def make_tar(files: dict[str, tuple[bytes, int]], trees: list[tuple[Path, str]] | None = None) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.GNU_FORMAT) as archive:
        for name, (content, mode) in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(content), mode, int(time.time())
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            archive.addfile(info, io.BytesIO(content))
        for source, destination in trees or []:
            add_tree(archive, source, destination)
    return output.getvalue()


def write_ar(path: Path, members: list[tuple[str, bytes]]) -> None:
    with path.open("wb") as archive:
        archive.write(b"!<arch>\n")
        for name, content in members:
            header = (f"{name + '/':<16}{int(time.time()):<12}{0:<6}{0:<6}{0o100644:<8o}{len(content):<10}`\n").encode("ascii")
            archive.write(header)
            archive.write(content)
            if len(content) % 2:
                archive.write(b"\n")


def build() -> Path:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    output_dir = ROOT / "dist"
    output_dir.mkdir(exist_ok=True)
    package_path = output_dir / f"{APP_NAME}_{version}_all.deb"
    postinst = (ROOT / "deploy/postinst.sh").read_text(encoding="utf-8")
    prerm = """#!/bin/sh
set -e
if [ "$1" = remove ] && command -v systemctl >/dev/null 2>&1; then systemctl stop meshcore-pi-station.service || true; systemctl disable meshcore-pi-station.service || true; fi
exit 0
"""
    postrm = """#!/bin/sh
set -e
if command -v systemctl >/dev/null 2>&1; then systemctl daemon-reload; fi
echo 'Application data was retained in /var/lib/meshcore-pi-station.'
exit 0
"""
    installed_size = sum(p.stat().st_size for p in (ROOT / "src").rglob("*") if p.is_file()) // 1024 + 64
    control = f"""Package: {APP_NAME}
Version: {version}
Section: net
Priority: optional
Architecture: all
Essential: no
Depends: python3 (>= 3.11), python3-venv, python3-pip, adduser, bluez, openssl
Maintainer: MeshCore Pi Station contributors
Installed-Size: {installed_size}
Homepage: https://github.com/enotikov/MeshCore-Pi-Station
Description: Bilingual Raspberry Pi web companion for MeshCore radios
 Provides chats, packet monitoring, radio configuration, geographic maps,
 telemetry, diagnostics and Heltec V4 firmware flashing over USB serial,
 plus Bluetooth Low Energy connectivity.
"""
    control_tar = make_tar({"control": (control.encode(), 0o644), "conffiles": (b"/etc/default/meshcore-pi-station\n", 0o644), "postinst": (postinst.encode(), 0o755), "prerm": (prerm.encode(), 0o755), "postrm": (postrm.encode(), 0o755)})
    with tempfile.TemporaryDirectory() as temporary:
        app = Path(temporary) / "app"
        app.mkdir()
        shutil.copytree(ROOT / "src", app / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "scripts", app / "scripts", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("pyproject.toml", "README.md", "README_RU.md", "README_EN.md", "CHANGELOG.md", "LICENSE"):
            shutil.copy2(ROOT / name, app / name)
        (app / "constraints.txt").write_text(locked_constraints(), encoding="utf-8", newline="\n")
        data_files = {"lib/systemd/system/meshcore-pi-station.service": ((ROOT / "deploy/meshcore-pi-station.service").read_bytes(), 0o644), "etc/default/meshcore-pi-station": ((ROOT / "deploy/meshcore-pi-station.default").read_bytes(), 0o640)}
        data_tar = make_tar(data_files, [(app, "usr/lib/meshcore-pi-station")])
    write_ar(package_path, [("debian-binary", b"2.0\n"), ("control.tar.gz", control_tar), ("data.tar.gz", data_tar)])
    digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    package_path.with_suffix(package_path.suffix + ".sha256").write_text(
        f"{digest}  {package_path.name}\n", encoding="ascii", newline="\n"
    )
    return package_path


if __name__ == "__main__":
    print(build())
