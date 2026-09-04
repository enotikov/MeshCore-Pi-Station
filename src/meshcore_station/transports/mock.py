from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from urllib.parse import parse_qs, quote_plus, urlparse

from .base import EventHandler, RadioTransport, TransportEvent


class MockTransport(RadioTransport):
    def __init__(self, seed: bool = True):
        self._handler: EventHandler | None = None
        self._connected = False
        self._seed = seed
        self._contacts = [
            {
                "id": "a1" * 32,
                "public_key": "a1" * 32,
                "name": "Orel Repeater",
                "kind": "repeater",
                "latitude": 52.9686,
                "longitude": 36.0695,
                "last_snr": 8.5,
                "last_seen": int(time.time()),
            },
            {
                "id": "b2" * 32,
                "public_key": "b2" * 32,
                "name": "Полевой узел",
                "kind": "chat",
                "latitude": 52.981,
                "longitude": 36.047,
                "last_snr": 5.25,
                "last_seen": int(time.time()),
            },
        ]
        self._channels = [
            {"id": 0, "name": "Public", "enabled": True, "secret": "8b3387e9c5cdea6ac9e5edbaa115cd72"},
            {"id": 1, "name": "Orel", "enabled": True, "secret": hashlib.sha256(b"#Orel").hexdigest()[:32]},
        ]
        self._started_at = int(time.time())
        self._packet_counts = {"recv": 18, "sent": 12, "flood_tx": 7, "direct_tx": 5,
                               "flood_rx": 11, "direct_rx": 7, "recv_errors": 1}
        self._device = {
            "name": "Mock Heltec V4", "firmware": "companion-simulator", "model": "Heltec V4",
            "version": "1.15.0-sim", "public_key": "c3" * 32, "max_contacts": 200,
            "max_channels": 8, "tx_power": 18, "max_tx_power": 22,
            "latitude": 52.9668, "longitude": 36.0583, "frequency": 869.525,
            "bandwidth": 250.0, "spreading_factor": 11, "coding_rate": 5,
            "telemetry_mode_base": 1, "telemetry_mode_loc": 1,
            "telemetry_mode_env": 0, "manual_add_contacts": True, "multi_acks": 1,
            "advert_loc_policy": 1, "path_hash_mode": 0, "duty_cycle": 50,
            "cad": False, "rx_boosted_gain": True, "direct_tx_delay": 0.2, "flood_max": 64,
        }

    @property
    def status(self) -> dict:
        return {
            "mode": "mock",
            "connected": self._connected,
            "port": "simulator",
            "device": self._device,
        }

    async def start(self, handler: EventHandler) -> None:
        self._handler = handler
        self._connected = True
        await handler(TransportEvent("status", self.status))

    async def stop(self) -> None:
        self._connected = False

    async def contacts(self) -> list[dict]:
        return list(self._contacts) if self._seed else []

    async def channels(self) -> list[dict]:
        return list(self._channels) if self._seed else []

    async def send_message(self, target_type: str, target_id: str, text: str) -> dict:
        if not self._connected:
            raise RuntimeError("Симулятор отключён")
        await asyncio.sleep(0.05)
        self._packet_counts["sent"] += 1
        self._packet_counts["direct_tx" if target_type == "contact" else "flood_tx"] += 1
        return {"radio_id": uuid.uuid4().hex[:16], "status": "delivered" if target_type == "contact" else "sent"}

    async def send_advert(self, flood: bool) -> None:
        if not self._connected:
            raise RuntimeError("Симулятор отключён")
        if self._handler:
            await self._handler(TransportEvent("advert_sent", {"flood": flood}))

    async def device_info(self) -> dict:
        return {**self._device, "battery_mv": 4060, "storage_used_kb": 148,
                "storage_total_kb": 4096, "device_time": int(time.time())}

    async def stats(self) -> dict:
        return {
            "core": {"battery_mv": 4060, "uptime_secs": int(time.time()) - self._started_at,
                     "errors": 0, "queue_len": 0},
            "radio": {"noise_floor": -112, "last_rssi": -83, "last_snr": 7.25,
                      "tx_air_secs": 34, "rx_air_secs": 81},
            "packets": dict(self._packet_counts),
        }

    async def update_device(self, values: dict) -> dict:
        if values.get("tx_power") is not None and values["tx_power"] > self._device["max_tx_power"]:
            raise ValueError(f"Максимальная мощность платы: {self._device['max_tx_power']} dBm")
        self._device.update({key: value for key, value in values.items() if value is not None})
        return await self.device_info()

    async def save_channel(self, channel: dict) -> dict:
        item = dict(channel)
        item["enabled"] = True
        if not item.get("secret"):
            item["secret"] = hashlib.sha256(item["name"].encode()).hexdigest()[:32]
        self._channels = [x for x in self._channels if int(x["id"]) != int(item["id"])]
        self._channels.append(item)
        self._channels.sort(key=lambda x: int(x["id"]))
        return item

    async def delete_channel(self, channel_id: int) -> None:
        self._channels = [x for x in self._channels if int(x["id"]) != channel_id]

    async def contact_action(self, contact_id: str, action: str, values: dict | None = None) -> dict:
        contact = next((x for x in self._contacts if x["id"] == contact_id), None)
        if not contact:
            raise RuntimeError("Контакт не найден")
        if action == "remove":
            self._contacts = [x for x in self._contacts if x["id"] != contact_id]
            return {"removed": True}
        if action == "telemetry":
            return {"battery_mv": 3920, "temperature": 21.8, "humidity": 48.0,
                    "latitude": contact.get("latitude"), "longitude": contact.get("longitude")}
        if action == "discover_path":
            contact.update({"out_path": "a1,c3", "out_path_len": 2, "out_path_hash_mode": 0})
        elif action == "reset_path":
            contact.update({"out_path": "", "out_path_len": -1, "out_path_hash_mode": 0})
        elif action == "set_path":
            path = str((values or {}).get("path") or "")
            contact.update({"out_path": path.replace(",", ""),
                            "out_path_len": len([x for x in path.split(",") if x]),
                            "out_path_hash_mode": int((values or {}).get("path_hash_mode") or 0)})
        return dict(contact)

    async def import_contact(self, uri: str) -> None:
        parsed = urlparse(uri)
        if parsed.scheme != "meshcore" or parsed.netloc != "contact" or parsed.path != "/add":
            raise ValueError("Некорректная MeshCore contact URI")
        query = parse_qs(parsed.query)
        public_key = query.get("public_key", [""])[0]
        if len(public_key) != 64:
            raise ValueError("Публичный ключ должен содержать 64 hex-символа")
        contact = {"id": public_key, "public_key": public_key,
                   "name": query.get("name", [public_key[:12]])[0],
                   "kind": {"1": "chat", "2": "repeater", "3": "room", "4": "sensor"}.get(query.get("type", ["1"])[0], "chat"),
                   "last_seen": int(time.time())}
        self._contacts = [x for x in self._contacts if x["id"] != public_key] + [contact]

    async def export_contact(self, contact_id: str | None = None) -> str:
        contact = self._device if contact_id is None else next((x for x in self._contacts if x["id"] == contact_id), None)
        if not contact:
            raise RuntimeError("Контакт не найден")
        public_key = contact.get("public_key") or contact.get("id")
        kind = {"chat": 1, "repeater": 2, "room": 3, "sensor": 4}.get(contact.get("kind", "chat"), 1)
        return f"meshcore://contact/add?name={quote_plus(str(contact.get('name', 'Node')))}&public_key={public_key}&type={kind}"

    async def trace(self, path: str | None = None, auth_code: int = 0) -> dict:
        hops = [x.strip() for x in (path or "a1,c3").split(",") if x.strip()]
        return {"tag": uuid.uuid4().hex[:8], "auth_code": auth_code, "path": hops,
                "snr": [8.5, 5.25][:len(hops)], "final_snr": 6.0, "simulated": True}

    async def send_datagram(self, channel_id: int, data_type: int, payload: bytes, flood: bool = True) -> dict:
        if len(payload) > 163:
            raise ValueError("Максимальный payload — 163 байта")
        self._packet_counts["sent"] += 1
        self._packet_counts["flood_tx" if flood else "direct_tx"] += 1
        return {"channel_id": channel_id, "data_type": data_type,
                "payload_hex": payload.hex(), "flood": flood, "status": "sent"}

    async def export_identity(self) -> dict:
        return {"public_key": self._device["public_key"], "private_key": "d4" * 32,
                "name": self._device["name"], "simulated": True}

    async def inject_message(self, target_type: str, target_id: str, text: str) -> None:
        if not self._handler:
            raise RuntimeError("Симулятор не запущен")
        self._packet_counts["recv"] += 1
        self._packet_counts["direct_rx" if target_type == "contact" else "flood_rx"] += 1
        await self._handler(
            TransportEvent(
                "message",
                {
                    "target_type": target_type,
                    "target_id": str(target_id),
                    "text": text,
                    "created_at": int(time.time()),
                    "metadata": {"simulated": True, "snr": 7.25, "rssi": -83,
                                 "path_len": 2, "path_hash_mode": 0,
                                 "route": "direct" if target_type == "contact" else "flood"},
                },
            )
        )
