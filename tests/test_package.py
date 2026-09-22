import hashlib
import io
from pathlib import Path
import shutil
import tarfile

from scripts import build_deb


def test_package_has_portable_checksum_constraints_and_control(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    for name in ('src', 'scripts', 'deploy'):
        shutil.copytree(root / name, tmp_path / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('uv.lock', 'pyproject.toml', 'README.md', 'README_EN.md', 'README_RU.md', 'CHANGELOG.md', 'LICENSE'):
        shutil.copy2(root / name, tmp_path / name)
    monkeypatch.setattr(build_deb, 'ROOT', tmp_path)
    package = build_deb.build()
    checksum = package.with_suffix('.deb.sha256').read_bytes()
    assert checksum == f'{hashlib.sha256(package.read_bytes()).hexdigest()}  {package.name}\n'.encode('ascii')
    stream = io.BytesIO(package.read_bytes())
    assert stream.read(8) == b'!<arch>\n'
    members = {}
    while header := stream.read(60):
        size = int(header[48:58])
        members[header[:16].decode().strip().rstrip('/')] = stream.read(size)
        if size % 2:
            stream.read(1)
    with tarfile.open(fileobj=io.BytesIO(members['data.tar.gz'])) as data:
        constraints = data.extractfile('usr/lib/meshcore-pi-station/constraints.txt').read()
        assert constraints == build_deb.locked_constraints().encode('utf-8')
        assert not any('__pycache__' in name for name in data.getnames())
        unit = data.extractfile('lib/systemd/system/meshcore-pi-station.service').read()
        assert b'StateDirectoryMode=0750' in unit
        assert b'UMask=0077' in unit
    with tarfile.open(fileobj=io.BytesIO(members['control.tar.gz'])) as control:
        postinst = control.extractfile('postinst').read()
        assert postinst == (root / 'deploy/postinst.sh').read_text(encoding='utf-8').encode('utf-8')
        assert b'\r' not in postinst
        assert b'dialout plugdev video' in postinst
