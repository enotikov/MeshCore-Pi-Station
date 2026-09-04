#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run / Запустите: sudo ./scripts/install.sh" >&2
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR=/usr/lib/meshcore-pi-station
VENV_DIR=/opt/meshcore-pi-station/.venv
DATA_DIR=/var/lib/meshcore-pi-station

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "Python 3.11+ is required. Use Raspberry Pi OS Bookworm." >&2
  exit 1
}

if ! id meshcore >/dev/null 2>&1; then
  useradd --system --home "$DATA_DIR" --create-home --shell /usr/sbin/nologin meshcore
fi
getent group dialout >/dev/null 2>&1 && usermod -aG dialout meshcore

install -d -m 0755 "$APP_DIR" /opt/meshcore-pi-station
install -d -o meshcore -g meshcore -m 0750 "$DATA_DIR" "$DATA_DIR/maps"
cp -a "$SOURCE_DIR/src" "$SOURCE_DIR/scripts" "$SOURCE_DIR/pyproject.toml" "$SOURCE_DIR/README.md" "$SOURCE_DIR/README_RU.md" "$SOURCE_DIR/README_EN.md" "$SOURCE_DIR/LICENSE" "$APP_DIR/"

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --disable-pip-version-check --upgrade pip
"$VENV_DIR/bin/pip" install --disable-pip-version-check --upgrade "$APP_DIR"

install -m 0644 "$SOURCE_DIR/deploy/meshcore-pi-station.service" /etc/systemd/system/meshcore-pi-station.service
if [[ ! -e /etc/default/meshcore-pi-station ]]; then
  install -m 0640 "$SOURCE_DIR/deploy/meshcore-pi-station.default" /etc/default/meshcore-pi-station
fi

systemctl daemon-reload
systemctl enable --now meshcore-pi-station
echo "MeshCore Pi Station: http://$(hostname -I | awk '{print $1}'):8080 (mock mode)"
