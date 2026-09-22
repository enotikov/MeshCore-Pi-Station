"""Exercise the real postinst shell in a disposable Linux filesystem.

Only account management/systemd/pip are simulated. Venvs, console shebangs,
symlink activation, cleanup and rollback execute on the real filesystem.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux installer integration test')


def executable(path, content):
    path.write_text(content, encoding='utf-8')
    path.chmod(0o755)


@pytest.mark.parametrize('previous', ['none', 'directory', 'symlink'])
@pytest.mark.parametrize('failure', ['', 'install', 'restart', 'health', 'reload'])
def test_postinst_activation_and_rollback(tmp_path, previous, failure):
    base = tmp_path / 'opt'
    base.mkdir()
    app = tmp_path / 'app'
    app.mkdir()
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'sentinel').write_text('preserve station data')
    active = base / '.venv'
    if previous != 'none':
        old = base / ('old-release' if previous == 'symlink' else '.venv')
        (old / 'bin').mkdir(parents=True)
        executable(old / 'bin/meshcore-pi-station', '#!/bin/sh\necho old\n')
        if previous == 'symlink':
            active.symlink_to(old, target_is_directory=True)
    mock = tmp_path / 'commands'
    mock.mkdir()
    executable(mock / 'id', '#!/bin/sh\nexit 0\n')
    executable(mock / 'getent', '#!/bin/sh\nexit 1\n')
    executable(mock / 'sleep', '#!/bin/sh\nexit 0\n')
    executable(mock / 'install', '#!/bin/sh\nexit 0\n')
    executable(mock / 'python3', f'''#!{sys.executable}
import os, pathlib, subprocess, sys
if sys.argv[1:3] == ['-m', 'venv']:
    target = pathlib.Path(sys.argv[3])
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(target)], check=True)
    site = next(target.glob('lib/python*/site-packages'))
    (site / 'pip.py').write_text("import os, sys\\nraise SystemExit(1 if os.environ.get('FAIL') == 'install' else 0)\\n")
    package = site / 'meshcore_station'
    package.mkdir()
    (package / '__init__.py').write_text("__version__ = 'test'\\n")
    (package / 'main.py').write_text('')
    command = target / 'bin/meshcore-pi-station'
    command.write_text('#!' + str(target / 'bin/python') + "\\nimport meshcore_station\\nprint('new')\\n")
    command.chmod(0o755)
else:
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
''')
    executable(mock / 'systemctl', f'''#!{sys.executable}
import os, pathlib, subprocess, sys
base = pathlib.Path({str(base)!r})
command = sys.argv[1]
fail = os.environ.get('FAIL')
with (base / 'commands.log').open('a') as log:
    log.write(command + '\\n')
if command == 'stop':
    (base / 'running').unlink(missing_ok=True)
elif command == 'daemon-reload' and fail == 'reload':
    sys.exit(1)
elif command == 'restart':
    result = subprocess.check_output([str(base / '.venv/bin/meshcore-pi-station')], text=True).strip()
    if result == 'new' and fail == 'restart':
        sys.exit(1)
    (base / 'running').write_text(result)
elif command == 'is-active':
    marker = base / 'running'
    if not marker.exists() or (marker.read_text() == 'new' and fail == 'health'):
        sys.exit(1)
''')
    if previous != 'none':
        (base / 'running').write_text('old')
    script = (ROOT / 'deploy/postinst.sh').read_text()
    script = script.replace('/usr/lib/meshcore-pi-station', str(app))
    script = script.replace('/opt/meshcore-pi-station', str(base))
    script = script.replace('/var/lib/meshcore-pi-station', str(data))
    postinst = tmp_path / 'postinst'
    postinst.write_text(script)
    env = dict(os.environ, PATH=str(mock) + os.pathsep + os.environ['PATH'], FAIL=failure)
    result = subprocess.run(['dash', str(postinst)], env=env, capture_output=True, text=True)
    assert (result.returncode == 0) == (not failure), result.stdout + result.stderr
    assert (data / 'sentinel').read_text() == 'preserve station data'
    assert not list(base.glob('.venv.link.*'))
    if not failure:
        assert active.is_symlink()
        assert subprocess.check_output([str(active / 'bin/meshcore-pi-station')], text=True).strip() == 'new'
        assert (base / 'running').read_text() == 'new'
        if previous == 'directory':
            assert list(base.glob('.venv.legacy.*'))
        elif previous == 'symlink':
            assert old.exists()
    else:
        assert not list(base.glob('.venv.release.*'))
        if previous == 'none':
            assert not active.exists()
            assert not active.is_symlink()
        else:
            assert active.is_symlink() == (previous == 'symlink')
            assert subprocess.check_output([str(active / 'bin/meshcore-pi-station')], text=True).strip() == 'old'
            assert (base / 'running').read_text() == 'old'


def test_source_installer_uses_generated_package(tmp_path):
    project = tmp_path / 'source'
    (project / 'scripts').mkdir(parents=True)
    script = (ROOT / 'scripts/install.sh').read_text().replace('${EUID}', '0')
    (project / 'scripts/install.sh').write_text(script)
    package = project / 'station.deb'
    package.write_bytes(b'test package')
    (project / 'scripts/build_deb.py').write_text(f'print({str(package)!r})')
    mock = tmp_path / 'commands'
    mock.mkdir()
    executable(mock / 'apt-get', '#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGS_FILE"\n')
    args = tmp_path / 'apt-args'
    subprocess.run(['bash', str(project / 'scripts/install.sh')], check=True,
                   env=dict(os.environ, PATH=str(mock) + os.pathsep + os.environ['PATH'], ARGS_FILE=str(args)))
    assert args.read_text().splitlines() == ['install', '-y', str(package)]
