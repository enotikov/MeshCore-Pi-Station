from __future__ import annotations

import glob
import logging
import time
from typing import Any

from .base import EventHandler, RadioTransport, TransportEvent

logger = logging.getLogger(__name__)


def resolve_serial_port(configured: str) -> str:
    if configured != "auto":
        return configured
    candidates: list[str] = []
    for pattern in ("/dev/serial/by-id/*", "/dev/ttyACM*", "/dev/ttyUSB*"):
        candidates.extend(sorted(glob.glob(pattern)))
    if not candidates:
        raise RuntimeError("MeshCore USB-модем не найден")
    return candidates[0]


class MeshCoreSerialTransport(RadioTransport):
    """Adapter around the official meshcore_py package.

    It is intentionally isolated from the rest of the application so the UI,
    database and tests can run before the Heltec companion firmware is ready.
    """

    def __init__(
        self,
        port: str,
        baud: int = 115200,
        debug: bool = False,
        *,
        mode: str = "serial",
        ble_pin: str | None = None,
    ):
        if mode not in {"serial", "ble"}:
            raise ValueError("Transport mode must be serial or ble")
        self._configured_port = port
        self._baud = baud
        self._debug = debug
        self._mode = mode
        self._ble_pin = ble_pin
        self._port: str | None = None
        self._mc: Any = None
        self._handler: EventHandler | None = None
        self._subscriptions: list[Any] = []
        self._device: dict[str, Any] = {}

    @property
    def status(self) -> dict[str, Any]:
        return {
            "mode": self._mode,
            "connected": bool(self._mc and self._mc.is_connected),
            "port": self._port or self._configured_port,
            "baud": self._baud if self._mode == "serial" else None,
            "device": self._device,
        }

    async def start(self, handler: EventHandler) -> None:
        from meshcore import EventType, MeshCore

        self._handler = handler
        if self._mode == "serial":
            self._port = resolve_serial_port(self._configured_port)
            self._mc = await MeshCore.create_serial(
                self._port,
                self._baud,
                debug=self._debug,
                auto_reconnect=True,
                max_reconnect_attempts=0,
            )
        else:
            address = None if self._configured_port.lower() in {"", "auto"} else self._configured_port
            self._port = address or "auto"
            self._mc = await MeshCore.create_ble(
                address=address,
                pin=self._ble_pin or None,
                debug=self._debug,
                auto_reconnect=True,
                max_reconnect_attempts=0,
            )
        if self._mc is None:
            raise RuntimeError(f"Нет ответа MeshCore Companion на {self._port}")

        async def on_private(event: Any) -> None:
            payload = event.payload or {}
            prefix = str(payload.get("pubkey_prefix", ""))
            contact = self._mc.get_contact_by_key_prefix(prefix) or {}
            await handler(
                TransportEvent(
                    "message",
                    {
                        "target_type": "contact",
                        "target_id": contact.get("public_key") or prefix,
                        "text": str(payload.get("text", "")),
                        "created_at": int(payload.get("sender_timestamp") or time.time()),
                        "metadata": payload,
                    },
                )
            )

        async def on_channel(event: Any) -> None:
            payload = event.payload or {}
            await handler(
                TransportEvent(
                    "message",
                    {
                        "target_type": "channel",
                        "target_id": str(payload.get("channel_idx", payload.get("channel", 0))),
                        "text": str(payload.get("text", "")),
                        "created_at": int(payload.get("sender_timestamp") or time.time()),
                        "metadata": payload,
                    },
                )
            )

        async def on_connection(event: Any) -> None:
            await handler(TransportEvent("status", self.status))

        async def on_advert(event: Any) -> None:
            payload = self._json_safe(event.payload)
            await handler(TransportEvent("advertisement", payload if isinstance(payload, dict) else {"value": payload}))
            await self._sync_contacts_to_handler()

        async def on_diagnostic(event: Any) -> None:
            payload = self._json_safe(event.payload)
            await handler(TransportEvent(event.type.value, payload if isinstance(payload, dict) else {"value": payload}))

        self._subscriptions = [
            self._mc.subscribe(EventType.CONTACT_MSG_RECV, on_private),
            self._mc.subscribe(EventType.CHANNEL_MSG_RECV, on_channel),
            self._mc.subscribe(EventType.ADVERTISEMENT, on_advert),
            self._mc.subscribe(EventType.CONNECTED, on_connection),
            self._mc.subscribe(EventType.DISCONNECTED, on_connection),
            self._mc.subscribe(EventType.TRACE_DATA, on_diagnostic),
            self._mc.subscribe(EventType.PATH_UPDATE, on_diagnostic),
            self._mc.subscribe(EventType.PATH_RESPONSE, on_diagnostic),
            self._mc.subscribe(EventType.TELEMETRY_RESPONSE, on_diagnostic),
            self._mc.subscribe(EventType.CHANNEL_DATA_RECV, on_diagnostic),
            self._mc.subscribe(EventType.RX_LOG_DATA, on_diagnostic),
        ]
        await self._mc.start_auto_message_fetching()
        try:
            self._event_payload(await self._mc.commands.set_time(int(time.time())), "SET_TIME")
        except Exception:
            logger.warning("Unable to synchronize Companion clock", exc_info=True)
        await self.device_info()
        await handler(TransportEvent("status", self.status))

    async def stop(self) -> None:
        if not self._mc:
            return
        for subscription in self._subscriptions:
            self._mc.unsubscribe(subscription)
        self._subscriptions.clear()
        await self._mc.stop_auto_message_fetching()
        await self._mc.disconnect()
        self._mc = None

    @staticmethod
    def _normalise_contact(contact_id: str, contact: dict[str, Any]) -> dict[str, Any]:
        public_key = str(contact.get("public_key") or contact_id)
        contact_type = contact.get("type", contact.get("adv_type", 1))
        kind = {1: "chat", 2: "repeater", 3: "room", 4: "sensor"}.get(contact_type, "chat")
        return {
            "id": public_key,
            "public_key": public_key,
            "name": contact.get("adv_name") or public_key[:12],
            "kind": kind,
            "latitude": contact.get("latitude", contact.get("adv_lat")),
            "longitude": contact.get("longitude", contact.get("adv_lon")),
            "last_seen": contact.get("last_advert", contact.get("last_seen", int(time.time()))),
            "last_snr": contact.get("last_snr"),
            "raw": contact,
        }

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        if isinstance(value, (bytes, bytearray)):
            return {"hex": bytes(value).hex()}
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    async def contacts(self) -> list[dict[str, Any]]:
        if not self._mc:
            return []
        await self._mc.ensure_contacts()
        return [self._normalise_contact(str(key), value) for key, value in self._mc.contacts.items()]

    async def _sync_contacts_to_handler(self) -> None:
        if not self._handler:
            return
        for contact in await self.contacts():
            await self._handler(TransportEvent("contact", contact))

    async def channels(self) -> list[dict[str, Any]]:
        from meshcore import EventType

        if not self._mc:
            return []
        channels: list[dict[str, Any]] = []
        max_channels = int(self._device.get("max_channels") or 8)
        for index in range(max_channels):
            result = await self._mc.commands.get_channel(index)
            if result.type == EventType.ERROR:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            name = str(payload.get("name", payload.get("channel_name", ""))).strip("\x00")
            if name:
                secret = payload.get("secret", payload.get("channel_secret", b""))
                if hasattr(secret, "hex"):
                    secret = secret.hex()
                channels.append({"id": index, "name": name, "enabled": True,
                                 "secret": str(secret or "")})
        return channels

    async def send_message(self, target_type: str, target_id: str, text: str) -> dict[str, Any]:
        from meshcore import EventType

        if not self._mc:
            raise RuntimeError("USB-модем не подключён")
        if target_type == "channel":
            result = await self._mc.commands.send_chan_msg(int(target_id), text)
            if result.type == EventType.ERROR:
                raise RuntimeError(str(result.payload.get("reason", "Ошибка отправки")))
            return {"radio_id": None, "status": "sent"}

        contact = self._mc.get_contact_by_key_prefix(target_id)
        if contact is None:
            raise RuntimeError("Контакт не найден в памяти модема")
        result = await self._mc.commands.send_msg_with_retry(contact, text)
        if result is None or result.type == EventType.ERROR:
            raise RuntimeError("Подтверждение доставки не получено")
        expected = result.payload.get("expected_ack", b"")
        radio_id = expected.hex() if hasattr(expected, "hex") else str(expected)
        return {"radio_id": radio_id, "status": "delivered"}

    async def send_advert(self, flood: bool) -> None:
        from meshcore import EventType

        if not self._mc:
            raise RuntimeError("USB-модем не подключён")
        result = await self._mc.commands.send_advert(flood=flood)
        if result.type == EventType.ERROR:
            raise RuntimeError(str(result.payload.get("reason", "Ошибка advert")))

    @staticmethod
    def _event_payload(event: Any, operation: str) -> dict[str, Any]:
        from meshcore import EventType

        if event is None or event.type == EventType.ERROR:
            reason = event.payload.get("reason", operation) if event else operation
            raise RuntimeError(str(reason))
        return dict(event.payload) if isinstance(event.payload, dict) else {}

    async def device_info(self) -> dict[str, Any]:
        if not self._mc:
            return dict(self._device)
        self_info = self._event_payload(await self._mc.commands.send_appstart(), "SELF_INFO")
        query = self._event_payload(await self._mc.commands.send_device_query(), "DEVICE_INFO")
        battery = self._event_payload(await self._mc.commands.get_bat(), "BATTERY")
        merged = {**self._device, **query, **self_info, **battery}
        aliases = {
            "adv_name": "name", "ver": "version", "fw_build": "firmware",
            "adv_lat": "latitude", "adv_lon": "longitude", "radio_freq": "frequency",
            "radio_bw": "bandwidth", "radio_sf": "spreading_factor", "radio_cr": "coding_rate",
            "used_kb": "storage_used_kb", "total_kb": "storage_total_kb",
        }
        for source, target in aliases.items():
            if source in merged:
                merged[target] = merged[source]
        self._device = merged
        return dict(self._device)

    async def stats(self) -> dict[str, Any]:
        if not self._mc:
            raise RuntimeError("USB-модем не подключён")
        commands = self._mc.commands
        return {
            "core": self._event_payload(await commands.get_stats_core(), "CORE_STATS"),
            "radio": self._event_payload(await commands.get_stats_radio(), "RADIO_STATS"),
            "packets": self._event_payload(await commands.get_stats_packets(), "PACKET_STATS"),
        }

    async def update_device(self, values: dict[str, Any]) -> dict[str, Any]:
        if not self._mc:
            raise RuntimeError("USB-модем не подключён")
        commands = self._mc.commands
        max_power = int(self._device.get("max_tx_power") or 0)
        tx_power = values.get("tx_power")
        if tx_power is not None:
            if max_power and int(tx_power) > max_power:
                raise ValueError(f"Максимальная мощность платы: {max_power} dBm")
            self._event_payload(await commands.set_tx_power(int(tx_power)), "TX_POWER")
        if values.get("name") is not None:
            self._event_payload(await commands.set_name(str(values["name"])), "NAME")
        if values.get("latitude") is not None or values.get("longitude") is not None:
            lat = float(values.get("latitude", self._device.get("latitude", 0)))
            lon = float(values.get("longitude", self._device.get("longitude", 0)))
            self._event_payload(await commands.set_coords(lat, lon), "COORDS")
        radio_keys = {"frequency", "bandwidth", "spreading_factor", "coding_rate"}
        if any(values.get(key) is not None for key in radio_keys):
            self._event_payload(
                await commands.set_radio(
                    float(values.get("frequency", self._device.get("frequency", 869.525))),
                    float(values.get("bandwidth", self._device.get("bandwidth", 250))),
                    int(values.get("spreading_factor", self._device.get("spreading_factor", 11))),
                    int(values.get("coding_rate", self._device.get("coding_rate", 5))),
                ), "RADIO"
            )
        setters = {
            "telemetry_mode_base": commands.set_telemetry_mode_base,
            "telemetry_mode_loc": commands.set_telemetry_mode_loc,
            "telemetry_mode_env": commands.set_telemetry_mode_env,
            "manual_add_contacts": commands.set_manual_add_contacts,
            "multi_acks": commands.set_multi_acks,
            "advert_loc_policy": commands.set_advert_loc_policy,
            "path_hash_mode": commands.set_path_hash_mode,
        }
        for key, setter in setters.items():
            if values.get(key) is not None:
                self._event_payload(await setter(values[key]), key)
        cli_settings = {
            "duty_cycle": ("dutycycle", lambda value: str(int(value))),
            "cad": ("cad", lambda value: "on" if value else "off"),
            "rx_boosted_gain": ("radio.rxgain", lambda value: "on" if value else "off"),
            "direct_tx_delay": ("direct.txdelay", lambda value: str(float(value))),
            "flood_max": ("flood.max", lambda value: str(int(value))),
        }
        for key, (command, formatter) in cli_settings.items():
            if values.get(key) is not None:
                self._event_payload(
                    await commands.run_cli_command(f"set {command} {formatter(values[key])}"), key
                )
                self._device[key] = values[key]
        return await self.device_info()

    async def save_channel(self, channel: dict[str, Any]) -> dict[str, Any]:
        if not self._mc:
            raise RuntimeError("USB-модем не подключён")
        secret = bytes.fromhex(channel["secret"]) if channel.get("secret") else None
        self._event_payload(
            await self._mc.commands.set_channel(int(channel["id"]), str(channel["name"]), secret),
            "SET_CHANNEL",
        )
        return {**channel, "enabled": True}

    async def delete_channel(self, channel_id: int) -> None:
        if not self._mc:
            raise RuntimeError("USB-модем не подключён")
        self._event_payload(
            await self._mc.commands.set_channel(channel_id, "", bytes(16)), "DELETE_CHANNEL"
        )

    async def contact_action(self, contact_id: str, action: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._mc:
            raise RuntimeError("USB-модем не подключён")
        contact = self._mc.get_contact_by_key_prefix(contact_id)
        if not contact:
            raise RuntimeError("Контакт не найден в памяти модема")
        if action == "remove":
            event = await self._mc.commands.remove_contact(contact_id)
        elif action == "telemetry":
            event = await self._mc.commands.req_telemetry_sync(contact)
        elif action == "discover_path":
            event = await self._mc.commands.send_path_discovery_sync(contact)
        elif action == "reset_path":
            event = await self._mc.commands.reset_path(contact_id)
        elif action == "set_path":
            event = await self._mc.commands.change_contact_path(
                contact, str((values or {}).get("path") or ""), (values or {}).get("path_hash_mode")
            )
        else:
            raise ValueError("Неизвестное действие контакта")
        return self._event_payload(event, action)

    async def import_contact(self, uri: str) -> None:
        if not self._mc:
            raise RuntimeError("USB-модем не подключён")
        self._event_payload(await self._mc.commands.import_contact(uri.encode("utf-8")), "IMPORT_CONTACT")

    async def export_contact(self, contact_id: str | None = None) -> str:
        if not self._mc:
            raise RuntimeError("USB-модем не подключён")
        event = await self._mc.commands.export_contact(contact_id)
        payload = self._event_payload(event, "EXPORT_CONTACT")
        return str(payload.get("uri") or payload.get("contact_uri") or payload.get("data") or "")

    async def trace(self, path: str | None = None, auth_code: int = 0) -> dict[str, Any]:
        if not self._mc:
            raise RuntimeError("USB-модем не подключён")
        return self._event_payload(await self._mc.commands.send_trace(path=path, auth_code=auth_code), "TRACE")

    async def send_datagram(self, channel_id: int, data_type: int, payload: bytes, flood: bool = True) -> dict[str, Any]:
        from meshcore import EventType

        if not self._mc:
            raise RuntimeError("USB-модем не подключён")
        if len(payload) > 163:
            raise ValueError("Максимальный payload — 163 байта")
        if data_type == 0:
            raise ValueError("data_type 0 зарезервирован")
        path_len = 0xFF if flood else 0
        command = bytes([0x3E, channel_id, path_len]) + int(data_type).to_bytes(2, "little") + payload
        event = await self._mc.commands.send(command, [EventType.OK, EventType.ERROR])
        self._event_payload(event, "CHANNEL_DATAGRAM")
        return {"channel_id": channel_id, "data_type": data_type,
                "payload_hex": payload.hex(), "flood": flood, "status": "sent"}

    async def export_identity(self) -> dict[str, Any]:
        if not self._mc:
            raise RuntimeError("USB-модем не подключён")
        payload = self._event_payload(await self._mc.commands.export_private_key(), "PRIVATE_KEY")
        return self._json_safe({"public_key": self._device.get("public_key"), **payload})
