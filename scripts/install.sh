#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo "Run / Запустите: sudo bash scripts/install.sh" >&2
  exit 1
fi
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "Python 3.11+ is required. Use Raspberry Pi OS Bookworm." >&2
  exit 1
}
# Use the same locked dependencies, activation and rollback as package installs.
PACKAGE="$(python3 "$SOURCE_DIR/scripts/build_deb.py")"
apt-get install -y "$PACKAGE"
echo "MeshCore Pi Station: http://$(hostname -I | awk '{print $1}'):8080"
