# MeshCore Pi Station — English guide

## Purpose

MeshCore Pi Station turns a Raspberry Pi into a local control station for a MeshCore Companion Radio connected over USB or Bluetooth Low Energy. The interface can be opened from a computer, phone, or the Raspberry Pi display. No cloud messaging service is required.

The application does not flash the Heltec board. It starts with a simulator by default; real USB or BLE mode can be enabled later, after a compatible Companion Firmware has been installed on the Heltec V4.

## Version 0.5.0 features

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
- USB serial or Bluetooth Low Energy Companion Radio connection;
- cacheable PWA shell, built-in HTTPS and configurable HTTP Basic authentication.
- authenticated Heltec V4 USB flashing with live progress in the web interface.

## Requirements

- Raspberry Pi 3, 4 or 5;
- Raspberry Pi OS Bookworm, 32-bit or 64-bit;
- Python 3.11 or newer;
- network access during initial Python dependency installation;
- TCP port 8080 available;
- for real radio operation: Heltec V4 with Companion Firmware and either a data cable or working BLE.

## Install the `.deb` package

```bash
sudo apt update
sudo apt install ./meshcore-pi-station_0.5.0_all.deb
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

## Flash Heltec V4 from the web interface

Flashing is disabled by default. Enable HTTPS and password authentication first, then add this to `/etc/default/meshcore-pi-station`:

```text
MESHCORE_FIRMWARE_FLASH=true
MESHCORE_FIRMWARE_BAUD=460800
```

Restart the service and open “Settings → Flash Heltec V4”. Connect the board to the Raspberry Pi with a USB data cable, choose the firmware and enter the exact confirmation `HELTEC V4`.

- A regular `*.bin` uses Update mode and writes the application at `0x10000` without erasing user data.
- A `*merged.bin` uses Full mode, erases flash and writes the complete image from `0x0`.

The station accepts ESP images up to 16 MiB, releases the serial port, runs `esptool`, reports progress and reconnects to the Companion afterwards. Do not disconnect USB or power during this operation. Always verify that the image targets Heltec V4 / ESP32-S3; board pin mapping cannot be identified from the image alone.

## Connect a Bluetooth Companion Radio

Enable Bluetooth and discover the device:

```bash
sudo systemctl enable --now bluetooth
bluetoothctl scan on
```

Set the following in `/etc/default/meshcore-pi-station`:

```text
MESHCORE_TRANSPORT=ble
MESHCORE_BLE_ADDRESS=auto
MESHCORE_BLE_PIN=
```

`auto` discovers a device whose name starts with MeshCore. Set an explicit MAC address such as `AA:BB:CC:DD:EE:FF` when needed. A PIN is required only by some Companion Firmware builds. Restart the service after editing. Use USB serial if the Heltec Bluetooth hardware is faulty.

## HTTPS and username/password login

Generate a self-signed certificate for the local network:

```bash
sudo /usr/lib/meshcore-pi-station/scripts/generate_tls.sh
```

Configure `/etc/default/meshcore-pi-station`:

```text
MESHCORE_WEB_USERNAME=operator
MESHCORE_WEB_PASSWORD=replace-with-a-long-unique-password
MESHCORE_TLS_CERT=/var/lib/meshcore-pi-station/tls/server.crt
MESHCORE_TLS_KEY=/var/lib/meshcore-pi-station/tls/server.key
```

After `sudo systemctl restart meshcore-pi-station`, open `https://<raspberry-pi-ip>:8080`. Browsers warn about a self-signed certificate; use a certificate from your own trusted CA for warning-free HTTPS. Authentication is disabled when the password is empty. Certificate and key must be configured together.

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
| `MESHCORE_PORT` | `8080` | HTTP or HTTPS port |
| `MESHCORE_DATA_DIR` | `/var/lib/meshcore-pi-station` | SQLite and application data |
| `MESHCORE_TRANSPORT` | `mock` | `mock`, `serial` or `ble` |
| `MESHCORE_SERIAL_PORT` | `auto` | USB auto-detection or explicit path |
| `MESHCORE_SERIAL_BAUD` | `115200` | USB serial baud rate |
| `MESHCORE_BLE_ADDRESS` | `auto` | BLE discovery or explicit Heltec MAC |
| `MESHCORE_BLE_PIN` | empty | Optional BLE pairing PIN |
| `MESHCORE_RADIO_DEBUG` | `false` | Transport diagnostic logging |
| `MESHCORE_WEB_USERNAME` | `meshcore` | Web-interface username |
| `MESHCORE_WEB_PASSWORD` | empty | Password; empty disables authentication |
| `MESHCORE_TLS_CERT` | empty | HTTPS PEM certificate |
| `MESHCORE_TLS_KEY` | empty | HTTPS PEM private key |
| `MESHCORE_TLS_KEY_PASSWORD` | empty | Encrypted TLS key password |
| `MESHCORE_FIRMWARE_FLASH` | `false` | Enable web-based Heltec firmware flashing |
| `MESHCORE_FIRMWARE_BAUD` | `460800` | `esptool` write speed |
| `MESHCORE_MBTILES_PATH` | empty | Local map database path |
| `MESHCORE_TILE_URL` | OpenStreetMap | Online fallback without MBTiles |

Run `sudo systemctl restart meshcore-pi-station` after changing configuration.

## Security

Do not expose the application to the internet without HTTPS and a password. HTTP Basic credentials are protected in transit only when TLS is enabled. Use the supplied generator for a local self-signed certificate, or a trusted certificate for a public hostname. An identity export contains the node private key and must be protected separately.

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
