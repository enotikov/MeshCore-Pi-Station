# MeshCore Pi Station

Self-hosted bilingual web companion for a MeshCore USB or Bluetooth Companion Radio on Raspberry Pi.

- [English documentation](README_EN.md)
- [Документация на русском](README_RU.md)

The application provides chats, contacts, channels, packet diagnostics, radio settings,
telemetry, route tools and a geographic mesh map. It can be developed and demonstrated
without hardware by using the built-in simulator.

Current release: **0.4.0**. Target platform: **Raspberry Pi OS Bookworm**, 32-bit or
64-bit, with Python 3.11 or newer.

Quick installation from the release package:

```bash
sudo apt install ./meshcore-pi-station_0.4.0_all.deb
```

Open `http://<raspberry-pi-ip>:8080`. The package starts in simulator mode and does
not flash or modify the connected Heltec board.

License: MIT.
