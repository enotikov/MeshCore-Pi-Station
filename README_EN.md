# MeshCore Pi Station — English guide

## Purpose

MeshCore Pi Station turns a Raspberry Pi into a local control station for a MeshCore Companion Radio connected over USB. The interface can be opened from a computer, phone, or the Raspberry Pi display. No cloud messaging service is required.

The application does not flash the Heltec board. It starts with a simulator by default; real USB mode can be enabled later, after a compatible Companion Firmware has been installed on the Heltec V4.

## Version 0.3.0 features

- persistent Russian and English interfaces;
- direct and channel messages with local SQLite history;
- contacts, favourites, blocking, notes and QR exchange;
- channel management and export;
- RSSI, SNR, noise-floor, packet and receive-error monitoring;
- detailed packet cards with raw metadata;
- geographic node map with persistent zoom and pan;
- OpenStreetMap or fully offline MBTiles basemap;
- routes and three-second RX/TX packet animations;
- telemetry, path discovery, path management and trace;
- radio, location and hardware-limited TX power settings;
- node coordinate selection directly on a geographic map in Settings;
- adverts, live WebSocket updates and browser notifications;
- AES-256-GCM backups, protected identity export and CSV export;
- cacheable PWA shell and optional HTTP Basic authentication.

## Requirements

- Raspberry Pi 3, 4 or 5;
- Raspberry Pi OS Bookworm, 32-bit or 64-bit;
- Python 3.11 or newer;
- network access during initial Python dependency installation;
- TCP port 8080 available;
- for real radio operation: Heltec V4 with USB Companion Firmware and a data cable.

## Install the `.deb` package

```bash
sudo apt update
sudo apt install ./meshcore-pi-station_0.3.0_all.deb
sudo systemctl status meshcore-pi-station
```

Open `http://<raspberry-pi-ip>:8080`; use `hostname -I` to find the address. The service initially uses its simulator, so it can be verified before connecting or flashing the Heltec.

## Connect a USB Companion Radio

```bash
ls -l /dev/serial/by-id/ /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

Edit `/etc/default/meshcore-pi-station`:

```text
MESHCORE_TRANSPORT=serial
MESHCORE_SERIAL_PORT=auto
MESHCORE_SERIAL_BAUD=115200
```

```bash
sudo systemctl restart meshcore-pi-station
sudo journalctl -u meshcore-pi-station -f
```

The `meshcore` service account is automatically added to the `dialout` group.

## Offline maps

Place an MBTiles file at `/var/lib/meshcore-pi-station/maps/region.mbtiles` and set:

```text
MESHCORE_MBTILES_PATH=/var/lib/meshcore-pi-station/maps/region.mbtiles
```

After restarting, MBTiles takes priority over the online basemap. Do not bulk-download public OpenStreetMap tiles; use a prepared MBTiles database for offline operation.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MESHCORE_HOST` | `0.0.0.0` | Web-server bind address |
| `MESHCORE_PORT` | `8080` | HTTP port |
| `MESHCORE_DATA_DIR` | `/var/lib/meshcore-pi-station` | SQLite and application data |
| `MESHCORE_TRANSPORT` | `mock` | `mock` or `serial` |
| `MESHCORE_SERIAL_PORT` | `auto` | USB auto-detection or explicit path |
| `MESHCORE_SERIAL_BAUD` | `115200` | USB serial baud rate |
| `MESHCORE_RADIO_DEBUG` | `false` | Transport diagnostic logging |
| `MESHCORE_WEB_PASSWORD` | empty | Password for web user `meshcore` |
| `MESHCORE_MBTILES_PATH` | empty | Local map database path |
| `MESHCORE_TILE_URL` | OpenStreetMap | Online fallback without MBTiles |

Run `sudo systemctl restart meshcore-pi-station` after changing configuration.

## Security

The application is intended for a trusted local network. Do not expose port 8080 directly to the internet. Set `MESHCORE_WEB_PASSWORD=a-long-unique-password` to enable authentication; the username is `meshcore`. Use a VPN or an HTTPS reverse proxy for remote access. An identity export contains the node private key and must be protected separately.

## Upgrade, removal and diagnostics

```bash
sudo apt install ./meshcore-pi-station_<new-version>_all.deb
sudo systemctl status meshcore-pi-station
sudo journalctl -u meshcore-pi-station -n 200 --no-pager
curl http://127.0.0.1:8080/api/status
sudo apt remove meshcore-pi-station
```

Data remains in `/var/lib/meshcore-pi-station` to prevent accidental loss of history and keys. Remove it manually only after creating a backup.

## Development and packaging

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
MESHCORE_TRANSPORT=mock meshcore-pi-station
pytest
python3 scripts/build_deb.py
```
