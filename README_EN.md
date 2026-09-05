# MeshCore Pi Station — English guide

## Upgrading to 1.0.0: initial access and recovery

Use your configured password if one already exists. Otherwise, sign in with the configured username (default `meshcore`) and read the initial password on the Raspberry Pi:

```bash
sudo cat /var/lib/meshcore-pi-station/setup-token
```

The service creates this file on startup and removes it after a permanent password of at least 12 characters is set. Development instances use `MESHCORE_DATA_DIR/setup-token`. Use a trusted local network for setup or configure TLS first: a one-time code does not encrypt HTTP. Ten failed logins from a peer block attempts until the end of a one-minute window; forwarded proxy headers are not used to identify peers.

Password changes apply immediately and close existing WebSockets. USB/BLE and HTTPS changes require a service restart. Authentication uses HTTP Basic; separate session management and logout are not included.

Select a queue lifetime before sending: 15 minutes, 1 hour, 24 hours or 7 days. Expiry stops waiting messages, not an active transmission. Click a message to cancel a queued send or manually retry a failed, cancelled, expired or unconfirmed message. Manual retry starts a new 24-hour lifetime. A missing ACK or restart during transmission results in “delivery unconfirmed”: the packet may have arrived, so retrying can deliver a duplicate. Channel “sent” is not recipient confirmation. Legacy queues without a deadline remain without one until manually retried.

Backups include contacts, channel secrets, messages with status timelines and retention policy. They **exclude** `station.json`, login passwords, BLE PINs, TLS certificates, maps, statistics and the radio private key. Identity has a separate export. Prefer encrypted `.mcps` files; plain JSON contains channel secrets.

Restore merges records without deleting records absent from the backup, stages and validates changes, and saves a pre-restore SQLite snapshot under `/var/lib/meshcore-pi-station/recovery/before-restore-*.db`. Imported pending messages are cancelled and require manual retry. Restored retention settings apply after restart or saving the retention form. Recovery snapshots are not encrypted; Linux file permissions restrict access. They are not automatically deleted, so monitor disk space.

To roll back a restore: stop the service, preserve the current `station.db`, `station.db-wal` and `station.db-shm` separately if present, copy the selected snapshot to `station.db`, ensure stale WAL/SHM files are absent, set ownership to `meshcore:meshcore`, and start the service. Never replace a database while the application is running.

This release was not installed on a physical Raspberry Pi or tested with a physical Heltec during preparation.

## Purpose

MeshCore Pi Station turns a Raspberry Pi into a local control station for a MeshCore Companion Radio connected over USB or Bluetooth Low Energy. The interface can be opened from a computer, phone, or the Raspberry Pi display. No cloud messaging service is required.

The application can flash a Heltec V4 over USB from the Settings page. It starts in simulator mode and firmware flashing is disabled by default for safety; enable it after configuring HTTPS and password authentication. Once compatible Companion Firmware is installed, the Heltec can be used over USB or Bluetooth Low Energy.

## Version 1.0.0 features

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
- first-run setup for USB, BLE, language, password and local HTTPS;
- persistent outgoing queue with cancellation, manual retry, expiry and a per-message delivery timeline;
- Raspberry Pi temperature, uptime, load, disk, database, USB and Bluetooth diagnostics;
- configurable history retention and database limits;
- optional trusted firmware catalogs with Heltec V4 and SHA-256 verification.
- unified radio, queue, database and automatic-backup health overview;
- per-node link quality for 24 hours, 7 days or 30 days: SNR, RSSI, RX/TX, delivery, latency and hops;
- Raspberry Pi memory, undervoltage, frequency-capping and throttling diagnostics;
- global message search by text, node, channel, status and date range;
- encrypted scheduled backups with rotation, download and same-station restore;
- privacy-filtered support reports without messages, coordinates, contacts, passwords or keys;
- safe manual Heltec reconnection and a 133-character outbound-message limit.
- acknowledgeable active-station warning center with acknowledgement reset;
- per-node RSSI, SNR and packet-volume time-series charts;
- route history with direction, timestamp, hop count and available path metadata;
- read-only GitHub Release checks without automatically running package installation.

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
sudo apt install ./meshcore-pi-station_1.0.0_all.deb
sudo systemctl status meshcore-pi-station
```

Open `http://<raspberry-pi-ip>:8080`; use `hostname -I` to find the address. The service initially uses its simulator, so it can be verified without a Heltec. Configure protected access and enable flashing as described below when you are ready to install firmware.

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

After `sudo systemctl restart meshcore-pi-station`, open `https://<raspberry-pi-ip>:8080`. Browsers warn about a self-signed certificate; use a certificate from your own trusted CA for warning-free HTTPS. When the permanent password is empty, initial access requires the one-time code from the `setup-token` file in the station data directory. Certificate and key must be configured together.

## Offline maps

Place an MBTiles file at `/var/lib/meshcore-pi-station/maps/region.mbtiles` and set:

```text
MESHCORE_MBTILES_PATH=/var/lib/meshcore-pi-station/maps/region.mbtiles
```

After restarting, MBTiles takes priority over the online basemap. Do not bulk-download public OpenStreetMap tiles; use a prepared MBTiles database for offline operation.

## Setup wizard and message queue

On first launch, version 1.0.0 asks for the language, USB/BLE/simulator transport, device address, username, password and local HTTPS. The wizard writes `/var/lib/meshcore-pi-station/station.json` with mode `0600`; this file takes priority for wizard-managed values. Delete it to return to `/etc/default/meshcore-pi-station`. Password changes take effect immediately and require signing in again. Restart the service after changing the transport or HTTPS.

When the Companion is unavailable, outgoing messages remain queued in SQLite. The station sends them after reconnection if they have not expired. Uncertain results require manual retry. Each message card shows its complete delivery timeline and per-attempt error details.

Outbound text is limited to 133 characters as specified by the Companion Protocol. Both the browser and API enforce the limit. Split longer text into multiple messages.

## Link-quality analytics

“Monitor → Link quality” aggregates locally retained data for 24 hours, 7 days or 30 days. It shows average SNR and RSSI, received and transmitted packet counts, direct-message confirmation rate, average confirmation time, hops and last activity for every known node.

Delivery rate includes only direct sends that were actually attempted. Channel sends have no per-recipient acknowledgment and are excluded. Missing RSSI, latency or route information remains empty rather than being estimated. SQLite performs the aggregation without loading the complete packet history into Raspberry Pi memory.

## Automatic backups and support reports

Settings can enable encrypted scheduled backups and configure their interval and retained-file count. Files are stored under `/var/lib/meshcore-pi-station/automatic-backups`; their local AES-256-GCM key is `/var/lib/meshcore-pi-station/automatic-backup.key`. Preserve the key separately for disaster recovery. Lowering the retention count immediately removes the oldest automatic backups.

Automatic backups can be created, downloaded and restored from the interface. They are intended for the same station or recovery with its preserved key. A password-protected `.mcps` remains the portable option between installations.

“Download report” produces JSON containing the application version, OS, radio state, device paths, errors, memory, disk, database health and backup metadata. The UI shows its contents before download. Messages, coordinates, contacts, passwords, BLE PINs, channel secrets, TLS keys and radio identity are excluded.

## Trusted firmware catalog

The built-in catalog contains official USB Companion Firmware for the standard Heltec V4. For a custom source, `MESHCORE_FIRMWARE_CATALOG_URL` points to an HTTPS JSON document:

```json
{"firmware":[{"id":"heltec-v4-1.16","name":"Heltec V4 Companion","version":"1.16.0","board":"heltec-v4","mode":"update","url":"https://example.org/firmware.bin","sha256":"64-character-hex"}]}
```

Only `heltec-v4` entries are accepted. The station downloads the image to the Raspberry Pi and verifies its size, ESP32 signature and SHA-256 before invoking the protected flashing workflow.

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
| `MESHCORE_FIRMWARE_CATALOG_URL` | `builtin` | Built-in catalog or trusted HTTPS JSON URL; empty disables the catalog |
| `MESHCORE_MESSAGE_RETRY_SECONDS` | `10` | Queue polling interval |
| `MESHCORE_MESSAGE_MAX_ATTEMPTS` | `5` | Legacy option; automatic retries after transmission are disabled |
| `MESHCORE_HISTORY_DAYS` | `30` | Message and event retention |
| `MESHCORE_PACKET_HISTORY_LIMIT` | `10000` | Maximum retained packet events |
| `MESHCORE_STATS_HISTORY_LIMIT` | `1440` | Maximum retained statistics samples |
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
