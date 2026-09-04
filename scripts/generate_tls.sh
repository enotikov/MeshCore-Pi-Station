#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run / Запустите: sudo /usr/lib/meshcore-pi-station/scripts/generate_tls.sh" >&2
  exit 1
fi

TLS_DIR=/var/lib/meshcore-pi-station/tls
HOST_NAME="$(hostname)"
HOST_IP="$(hostname -I | awk '{print $1}')"
SAN="DNS:${HOST_NAME},DNS:localhost,IP:127.0.0.1"
if [[ -n ${HOST_IP} ]]; then SAN="${SAN},IP:${HOST_IP}"; fi

install -d -o meshcore -g meshcore -m 0750 "$TLS_DIR"
openssl req -x509 -nodes -newkey rsa:3072 -sha256 -days 825 \
  -keyout "$TLS_DIR/server.key" -out "$TLS_DIR/server.crt" \
  -subj "/CN=${HOST_NAME}" -addext "subjectAltName=${SAN}"
chown meshcore:meshcore "$TLS_DIR/server.key" "$TLS_DIR/server.crt"
chmod 0600 "$TLS_DIR/server.key"
chmod 0644 "$TLS_DIR/server.crt"
echo "TLS certificate: $TLS_DIR/server.crt"
echo "TLS private key:  $TLS_DIR/server.key"
