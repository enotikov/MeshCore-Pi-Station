# MeshCore Pi Station

Self-hosted bilingual web companion for a MeshCore USB or Bluetooth Companion Radio on Raspberry Pi.

- [English documentation](README_EN.md)
- [Документация на русском](README_RU.md)

The application provides chats, contacts, channels, packet diagnostics, radio settings,
telemetry, route tools and a geographic mesh map. It can be developed and demonstrated
without hardware by using the built-in simulator.

Current release: **0.6.0**. Target platform: **Raspberry Pi OS Bookworm**, 32-bit or
64-bit, with Python 3.11 or newer.

Quick installation from the release package:

```bash
sudo apt install ./meshcore-pi-station_0.6.0_all.deb
```

Open `http://<raspberry-pi-ip>:8080`. The package starts in simulator mode and does
not modify a connected board during installation. Authenticated Heltec V4 firmware
flashing can be enabled explicitly and run from the Settings page over USB.
Version 0.6.0 adds a first-run wizard, persistent message queue, delivery timeline,
Raspberry Pi diagnostics, configurable retention and SHA-256 verified firmware catalogs.

License: MIT.
