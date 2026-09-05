"""Validate legacy-compatible data backups before touching the live database."""
from typing import Literal

from pydantic import BaseModel, Field

from .models import HistoryPolicyRequest


class Contact(BaseModel):
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)


class Channel(BaseModel):
    id: int = Field(ge=0, le=255)
    name: str = Field(min_length=1, max_length=256)
    secret: str = Field("", max_length=256)


class Event(BaseModel):
    status: str = Field(min_length=1, max_length=64)
    created_at: int = Field(ge=0)
    detail: str = Field("", max_length=10000)


class Message(BaseModel):
    target_type: Literal["contact", "channel"]
    target_id: str = Field(min_length=1, max_length=256)
    direction: Literal["in", "out"]
    text: str = Field(max_length=10000)
    status: Literal["queued", "sending", "sent", "delivered", "received", "failed", "unconfirmed", "expired", "cancelled"]
    created_at: int = Field(ge=0)
    expires_at: int | None = Field(None, ge=0)
    attempt_count: int = Field(0, ge=0)
    last_attempt: int | None = Field(None, ge=0)
    metadata: dict = Field(default_factory=dict)
    timeline: list[Event] = Field(default_factory=list, max_length=10000)


class Backup(BaseModel):
    version: Literal[1]
    contacts: list[Contact] = Field(default_factory=list, max_length=10000)
    channels: list[Channel] = Field(default_factory=list, max_length=256)
    messages: list[Message] = Field(default_factory=list, max_length=100000)
    settings: dict = Field(default_factory=dict)


def validate_backup(data: dict) -> dict[str, int]:
    backup = Backup.model_validate(data)
    HistoryPolicyRequest.model_validate(backup.settings)
    return {"contacts": len(backup.contacts), "channels": len(backup.channels), "messages": len(backup.messages)}
