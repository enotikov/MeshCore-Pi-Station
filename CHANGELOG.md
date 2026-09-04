# Changelog

## 0.6.0 — 2026-09-05

- Added a first-run wizard for USB, BLE, language, password and local HTTPS setup.
- Added persistent outgoing-message queuing with retry limits and delivery timelines.
- Added explicit reconnect states and connection-attempt diagnostics.
- Added Raspberry Pi health diagnostics for temperature, uptime, load, disk, database, USB and Bluetooth.
- Added configurable history retention and immediate pruning from the web interface.
- Added a built-in trusted catalog for official MeshCore 1.17.1 Heltec V4 USB Companion images, with board filtering and mandatory SHA-256 verification.
- Added database migrations that preserve existing 0.5.x data.

## 0.5.1 — 2026-09-04

- Corrected the RU/EN guides to state that authenticated Heltec V4 USB flashing is supported.
- Clarified that installation itself does not modify the board and flashing is an explicit, opt-in operation.

## 0.5.0 — 2026-09-04

- Added authenticated Heltec V4 firmware flashing from the Settings page.
- Added safe handling for regular application images at `0x10000` and merged images at `0x0`.
- Added USB-port discovery, ESP image validation, upload size limits and explicit confirmation.
- Added background flashing progress and automatic Companion reconnection.
- Added `esptool` to the Raspberry Pi package.

## 0.4.0 — 2026-09-04

- Added Bluetooth Low Energy transport with automatic discovery, explicit address and optional pairing PIN.
- Added configurable web username and password authentication for HTTP and WebSocket connections.
- Added native HTTPS startup with configurable PEM certificate, private key and encrypted-key password.
- Added a self-signed TLS certificate generator and security response headers.
- Added BlueZ and OpenSSL to the Raspberry Pi Debian package requirements.

## 0.3.0 — 2026-09-04

- Added Russian and English web interfaces with persistent language selection.
- Added bilingual PWA metadata and complete RU/EN documentation.
- Added a geographic node map with Web Mercator projection, fixed zoom and pan.
- Added OpenStreetMap and offline MBTiles basemap support.
- Added geographic coordinate selection to radio settings.
- Added packet details, live routes and three-second packet animations.
- Added radio statistics, contacts, channels, telemetry and path tools.
- Added encrypted backups and protected identity export.
- Added a Raspberry Pi OS Bookworm Debian installer and systemd service.
- Added automatic tests and Debian-package builds through GitHub Actions.
