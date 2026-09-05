from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import WebSocket

from .database import Database
from .transports.base import RadioTransport, TransportEvent

logger = logging.getLogger(__name__)


class StationService:
    def __init__(
        self,
        database: Database,
        transport: RadioTransport,
        reconnect_interval: float = 5.0,
        message_retry_seconds: float = 10.0,
        message_max_attempts: int = 5,
    ):
        self.database = database
        self.transport = transport
        self.reconnect_interval = reconnect_interval
        self.message_retry_seconds = message_retry_seconds
        self.message_max_attempts = message_max_attempts
        self._sockets: set[WebSocket] = set()
        self._state: dict[str, Any] = {"connected": False, "starting": True}
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._reconnect_task: asyncio.Task[None] | None = None
        self._stats_task: asyncio.Task[None] | None = None
        self._queue_task: asyncio.Task[None] | None = None
        self._last_stats: dict[str, Any] = {}
        self._maintenance = False
        self._maintenance_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._connection_attempt = 0

    @property
    def status(self) -> dict[str, Any]:
        return {**self._state, **self.transport.status}

    async def start(self) -> None:
        self.database.initialize()
        connected = await self._connect()
        if not connected:
            self._reconnect_task = asyncio.create_task(
                self._reconnect_loop(), name="meshcore-radio-reconnect"
            )
        self._stats_task = asyncio.create_task(self._stats_loop(), name="meshcore-stats")
        self._queue_task = asyncio.create_task(self._queue_loop(), name="meshcore-message-queue")

    async def _connect(self) -> bool:
        self._connection_attempt += 1
        self._state = {
            "starting": True, "connected": False, "connection_state": "connecting",
            "connection_attempt": self._connection_attempt, "error": None,
        }
        try:
            await self.transport.start(self._on_transport_event)
            await self.sync()
            self._state = {
                "starting": False, "connection_state": "connected", "connection_attempt": 0,
                "connected_at": int(time.time()), "error": None,
            }
            self._connection_attempt = 0
            await self.broadcast("status", self.status)
            return True
        except Exception as exc:
            logger.exception("Unable to start radio transport")
            try:
                await self.transport.stop()
            except Exception:
                logger.exception("Unable to clean up failed radio transport")
            self._state = {
                "starting": False, "connected": False, "connection_state": "retrying",
                "connection_attempt": self._connection_attempt, "error": str(exc),
            }
            await self.broadcast("status", self.status)
            return False

    async def _reconnect_loop(self) -> None:
        delay = self.reconnect_interval
        while not self._stop_event.is_set():
            self._state["retry_in"] = delay
            await self.broadcast("status", self.status)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                return
            except TimeoutError:
                pass
            if await self._connect():
                return
            delay = min(delay * 2, 60.0)

    async def pause_radio(self, reason: str) -> None:
        async with self._maintenance_lock:
            self._maintenance = True
            if self._reconnect_task:
                self._reconnect_task.cancel()
                try:
                    await self._reconnect_task
                except asyncio.CancelledError:
                    pass
                self._reconnect_task = None
            await self.transport.stop()
            self._state = {
                "starting": False, "connected": False, "maintenance": reason, "error": None
            }
            await self.broadcast("status", self.status)

    async def resume_radio(self) -> None:
        async with self._maintenance_lock:
            if not self._maintenance or self._stop_event.is_set():
                return
            self._maintenance = False
            self._state.pop("maintenance", None)
            if not await self._connect():
                self._reconnect_task = asyncio.create_task(
                    self._reconnect_loop(), name="meshcore-radio-reconnect"
                )

    async def reconnect_radio(self) -> dict[str, Any]:
        async with self._maintenance_lock:
            if self._maintenance:
                raise RuntimeError("Станция занята обслуживанием")
            if self._reconnect_task:
                self._reconnect_task.cancel()
                try:
                    await self._reconnect_task
                except asyncio.CancelledError:
                    pass
                self._reconnect_task = None
            await self.transport.stop()
            if not await self._connect():
                self._reconnect_task = asyncio.create_task(
                    self._reconnect_loop(), name="meshcore-radio-reconnect"
                )
            return self.status

    async def stop(self) -> None:
        self._stop_event.set()
        for task in (self._reconnect_task, self._stats_task, self._queue_task):
            if task:
                task.cancel()
        for task in (self._reconnect_task, self._stats_task, self._queue_task):
            if not task:
                continue
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._reconnect_task = None
        self._stats_task = None
        self._queue_task = None
        await self.transport.stop()
        self.database.close()

    async def _stats_loop(self) -> None:
        while not self._stop_event.is_set():
            if self.status.get("connected"):
                try:
                    await self.get_stats(refresh=True)
                except Exception:
                    logger.debug("Unable to poll radio stats", exc_info=True)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=15)
            except TimeoutError:
                pass

    async def get_stats(self, refresh: bool = False) -> dict[str, Any]:
        if refresh or not self._last_stats:
            self._last_stats = await self.transport.stats()
            self.database.add_stats_sample(self._last_stats)
        return {**self._last_stats, "history": self.database.list_stats_samples()}

    async def get_device_info(self) -> dict[str, Any]:
        return await self.transport.device_info()

    async def _queue_loop(self) -> None:
        while not self._stop_event.is_set():
            for message in self.database.expire_queued_messages():
                await self.broadcast("message", message)
            if self.status.get("connected") and not self._maintenance:
                for message in self.database.queued_messages():
                    await self._deliver_message(message)
                    if not self.status.get("connected"):
                        break
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.message_retry_seconds)
            except TimeoutError:
                pass

    async def update_device(self, values: dict[str, Any]) -> dict[str, Any]:
        result = await self.transport.update_device(values)
        await self.broadcast("status", self.status)
        return result

    async def sync(self) -> None:
        async with self._lock:
            for contact in await self.transport.contacts():
                self.database.upsert_contact(contact)
            for channel in await self.transport.channels():
                self.database.upsert_channel(channel)

    async def send_message(self, target_type: str, target_id: str, text: str, ttl_seconds: int = 86400) -> dict[str, Any]:
        message = self.database.add_message(
            target_type=target_type,
            target_id=target_id,
            direction="out",
            text=text,
            status="queued",
            expires_at=int(time.time()) + ttl_seconds,
        )
        await self.broadcast("message", message)
        if not self.status.get("connected") or self._maintenance:
            return message
        return await self._deliver_message(message)

    async def message_action(self, message_id: int, action: str) -> dict[str, Any]:
        # Never wait behind an active radio send and then pretend to cancel it.
        current = self.database.get_message(message_id)
        if current["status"] == "sending":
            raise ValueError("Отправка уже выполняется")
        async with self._send_lock:
            current = self.database.get_message(message_id)
            allowed = {"queued"} if action == "cancel" else {"failed", "expired", "cancelled", "unconfirmed"}
            if current["direction"] != "out" or current["status"] not in allowed:
                raise ValueError("Действие недоступно для текущего статуса")
            if action == "retry":
                self.database.reset_message_deadline(message_id)
            message = self.database.update_message(message_id, "cancelled" if action == "cancel" else "queued")
            await self.broadcast("message", message)
            return message

    async def _deliver_message(self, message: dict[str, Any]) -> dict[str, Any]:
        async with self._send_lock:
            message = self.database.get_message(int(message["id"]))
            if message["status"] != "queued":
                return message
            if message.get("expires_at") and message["expires_at"] <= int(time.time()):
                message = self.database.update_message(message["id"], "expired")
                await self.broadcast("message", message)
                return message
            if not self.status.get("connected") or self._maintenance:
                return message
            if message["target_type"] == "contact":
                contact = self.database.get_contact(str(message["target_id"]))
                if contact and contact.get("blocked"):
                    message = self.database.update_message(message["id"], "failed", error="Контакт заблокирован")
                    await self.broadcast("message", message)
                    return message
            message = self.database.update_message(message["id"], "sending", attempted=True)
            await self.broadcast("message", message)
            target_type = str(message["target_type"])
            target_id = str(message["target_id"])
            try:
                if target_type == "contact":
                    contact = self.database.get_contact(target_id)
                    if contact and contact.get("blocked"):
                        raise RuntimeError("Контакт заблокирован")
                result = await self.transport.send_message(target_type, target_id, str(message["text"]))
                message = self.database.update_message(
                    message["id"], result.get("status", "sent"), result.get("radio_id")
                )
            except Exception as exc:
                # Once handed to a transport, an exception cannot prove no RF send occurred.
                message = self.database.update_message(
                    message["id"], "unconfirmed", error=str(exc)
                )
            await self.broadcast("message", message)
            packet = self.database.add_packet_event(
                "message", direction="out",
                contact_id=target_id if target_type == "contact" else None,
                channel_id=target_id if target_type == "channel" else None,
                data=message,
            )
            await self.broadcast("packet", packet)
            return message

    async def _on_transport_event(self, event: TransportEvent) -> None:
        if event.type == "message":
            payload = event.payload
            target_type = str(payload["target_type"])
            target_id = str(payload["target_id"])
            if target_type == "contact" and not self.database.get_contact(target_id):
                contact = self.database.upsert_contact(
                    {
                        "id": target_id,
                        "public_key": target_id,
                        "name": str(payload.get("sender_name") or target_id[:12]),
                        "kind": "chat",
                    }
                )
                await self.broadcast("contact", contact)
            message = self.database.add_message(
                target_type=target_type,
                target_id=target_id,
                direction="in",
                text=str(payload["text"]),
                status="received",
                created_at=payload.get("created_at"),
                metadata=payload.get("metadata"),
            )
            packet = self.database.add_packet_event(
                "message", direction="in",
                contact_id=target_id if target_type == "contact" else None,
                channel_id=target_id if target_type == "channel" else None,
                data=message, created_at=message["created_at"],
            )
            await self.broadcast("message", message)
            await self.broadcast("packet", packet)
        elif event.type == "contact":
            contact = self.database.upsert_contact(event.payload)
            await self.broadcast("contact", contact)
        elif event.type == "status":
            connected = bool(self.transport.status.get("connected"))
            self._state["connection_state"] = "connected" if connected else "disconnected"
            self._state["connected"] = connected
            await self.broadcast("status", self.status)
        else:
            packet = self.database.add_packet_event(event.type, data=event.payload)
            await self.broadcast("packet", packet)
            await self.broadcast(event.type, event.payload)

    async def add_socket(self, socket: WebSocket) -> None:
        await socket.accept()
        self._sockets.add(socket)
        await socket.send_json({"type": "status", "payload": self.status})

    def remove_socket(self, socket: WebSocket) -> None:
        self._sockets.discard(socket)

    async def broadcast(self, event_type: str, payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for socket in tuple(self._sockets):
            try:
                await socket.send_json({"type": event_type, "payload": payload})
            except Exception:
                stale.append(socket)
        for socket in stale:
            self._sockets.discard(socket)
