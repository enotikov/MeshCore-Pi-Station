#!/bin/sh
set -eu
APP_DIR=/usr/lib/meshcore-pi-station
BASE_DIR=/opt/meshcore-pi-station
VENV_DIR="$BASE_DIR/.venv"
DATA_DIR=/var/lib/meshcore-pi-station
SERVICE=meshcore-pi-station.service
NEW=""
OLD=""
LINK="$BASE_DIR/.venv.link.$$"
LEGACY="$BASE_DIR/.venv.legacy.$$"
SWITCHING=0
WAS_ACTIVE=0
cleanup() {
    result=$?
    trap - EXIT HUP INT TERM
    if [ "$SWITCHING" = 1 ]; then
        systemctl stop "$SERVICE" || true
        if [ -L "$VENV_DIR" ]; then rm -f -- "$VENV_DIR"; fi
        if [ -d "$LEGACY" ]; then
            mv -- "$LEGACY" "$VENV_DIR"
        elif [ -n "$OLD" ]; then
            ln -s -- "$OLD" "$VENV_DIR"
        fi
        if [ "$WAS_ACTIVE" = 1 ]; then systemctl restart "$SERVICE" || true; fi
    fi
    rm -f -- "$LINK"
    if [ -n "$NEW" ]; then rm -rf -- "$NEW"; fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
    echo 'MeshCore Pi Station requires Python 3.11+.' >&2
    exit 1
}
command -v systemctl >/dev/null
if ! id meshcore >/dev/null 2>&1; then
    adduser --system --group --home "$DATA_DIR" --no-create-home meshcore
fi
for device_group in dialout plugdev video; do
    if getent group "$device_group" >/dev/null 2>&1; then
        usermod -a -G "$device_group" meshcore
    fi
done
install -d -m 0755 "$BASE_DIR"
install -d -o meshcore -g meshcore -m 0750 "$DATA_DIR" "$DATA_DIR/maps"
# Build at its permanent path: venv console scripts embed absolute shebangs.
NEW="$(mktemp -d "$BASE_DIR/.venv.release.XXXXXXXX")"
chmod 0755 "$NEW"
python3 -m venv "$NEW"
"$NEW/bin/python" -m pip install --disable-pip-version-check --constraint "$APP_DIR/constraints.txt" "$APP_DIR"
"$NEW/bin/python" -c 'import meshcore_station.main; print(meshcore_station.__version__)'
"$NEW/bin/python" -m pip check
if systemctl is-active --quiet "$SERVICE"; then WAS_ACTIVE=1; fi
if [ -L "$VENV_DIR" ]; then OLD="$(readlink "$VENV_DIR")"; fi
systemctl stop "$SERVICE"
SWITCHING=1
if [ -d "$VENV_DIR" ] && [ ! -L "$VENV_DIR" ]; then mv -- "$VENV_DIR" "$LEGACY"; fi
ln -s -- "$NEW" "$LINK"
python3 -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' "$LINK" "$VENV_DIR"
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"
# Type=simple can report success before Python has imported the application.
for attempt in 1 2 3; do
    sleep 2
    systemctl is-active --quiet "$SERVICE"
done
# Retain the previous environment for manual recovery; never prune user paths.
SWITCHING=0
NEW=""
trap - EXIT HUP INT TERM
exit 0
