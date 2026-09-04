from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TransportEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


EventHandler = Callable[[TransportEvent], Awaitable[None]]


class RadioTransport(ABC):
    @property
    @abstractmethod
    def status(self) -> dict[str, Any]: ...

    @abstractmethod
    async def start(self, handler: EventHandler) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def contacts(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def channels(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def send_message(self, target_type: str, target_id: str, text: str) -> dict[str, Any]: ...

    @abstractmethod
    async def send_advert(self, flood: bool) -> None: ...

    @abstractmethod
    async def device_info(self) -> dict[str, Any]: ...

    @abstractmethod
    async def stats(self) -> dict[str, Any]: ...

    @abstractmethod
    async def update_device(self, values: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    async def save_channel(self, channel: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    async def delete_channel(self, channel_id: int) -> None: ...

    @abstractmethod
    async def contact_action(self, contact_id: str, action: str, values: dict[str, Any] | None = None) -> dict[str, Any]: ...

    @abstractmethod
    async def import_contact(self, uri: str) -> None: ...

    @abstractmethod
    async def export_contact(self, contact_id: str | None = None) -> str: ...

    @abstractmethod
    async def trace(self, path: str | None = None, auth_code: int = 0) -> dict[str, Any]: ...

    @abstractmethod
    async def send_datagram(self, channel_id: int, data_type: int, payload: bytes, flood: bool = True) -> dict[str, Any]: ...

    @abstractmethod
    async def export_identity(self) -> dict[str, Any]: ...
