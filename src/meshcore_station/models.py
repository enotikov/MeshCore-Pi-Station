from typing import Literal

from pydantic import BaseModel, Field


class SendMessageRequest(BaseModel):
    target_type: Literal["contact", "channel"]
    target_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=160)
    ttl_seconds: int = Field(86400, ge=60, le=604800)


class MockIncomingRequest(SendMessageRequest):
    pass


class AdvertRequest(BaseModel):
    flood: bool = True


class DeviceSettingsRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    tx_power: int | None = Field(None, ge=0, le=40)
    frequency: float | None = Field(None, ge=300, le=2500)
    bandwidth: float | None = Field(None, ge=7.8, le=500)
    spreading_factor: int | None = Field(None, ge=5, le=12)
    coding_rate: int | None = Field(None, ge=5, le=8)
    telemetry_mode_base: int | None = Field(None, ge=0, le=3)
    telemetry_mode_loc: int | None = Field(None, ge=0, le=3)
    telemetry_mode_env: int | None = Field(None, ge=0, le=3)
    manual_add_contacts: bool | None = None
    multi_acks: int | None = Field(None, ge=0, le=1)
    advert_loc_policy: int | None = Field(None, ge=0, le=2)
    path_hash_mode: int | None = Field(None, ge=0, le=2)
    duty_cycle: int | None = Field(None, ge=1, le=100)
    cad: bool | None = None
    rx_boosted_gain: bool | None = None
    direct_tx_delay: float | None = Field(None, ge=0, le=2)
    flood_max: int | None = Field(None, ge=0, le=64)


class ContactUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    favorite: bool | None = None
    blocked: bool | None = None
    notes: str | None = Field(None, max_length=1000)


class ContactImportRequest(BaseModel):
    uri: str = Field(min_length=1, max_length=1000)


class ChannelRequest(BaseModel):
    id: int = Field(ge=0, le=255)
    name: str = Field(min_length=1, max_length=32)
    secret: str | None = Field(None, pattern="^[0-9a-fA-F]{32}$")
    region_scope: str | None = Field(None, max_length=64)


class PathRequest(BaseModel):
    path: str | None = Field(None, max_length=384)
    path_hash_mode: int | None = Field(None, ge=0, le=2)


class TraceRequest(BaseModel):
    path: str | None = Field(None, max_length=384)
    auth_code: int = Field(0, ge=0, le=0xFFFFFFFF)


class BackupImportRequest(BaseModel):
    data: dict


class EncryptedBackupRequest(BaseModel):
    password: str = Field(min_length=8, max_length=256)


class EncryptedRestoreRequest(EncryptedBackupRequest):
    payload_base64: str = Field(min_length=32, max_length=32 * 1024 * 1024)


class IdentityBackupRequest(EncryptedBackupRequest):
    confirmation: str


class DatagramRequest(BaseModel):
    channel_id: int = Field(ge=0, le=255)
    data_type: int = Field(ge=1, le=0xFFFF)
    payload_hex: str = Field(pattern="^(?:[0-9a-fA-F]{2}){1,163}$")
    flood: bool = True


class SetupRequest(BaseModel):
    transport: Literal["mock", "serial", "ble"]
    serial_port: str = Field("auto", min_length=1, max_length=256)
    ble_address: str = Field("auto", min_length=1, max_length=64)
    ble_pin: str = Field("", max_length=64)
    language: Literal["ru", "en"] = "ru"
    web_username: str = Field("meshcore", min_length=1, max_length=64)
    web_password: str = Field("", max_length=256)
    enable_https: bool = False


class HistoryPolicyRequest(BaseModel):
    history_days: int = Field(30, ge=1, le=3650)
    packet_limit: int = Field(10000, ge=100, le=1_000_000)
    stats_limit: int = Field(1440, ge=60, le=100_000)


class CatalogFlashRequest(BaseModel):
    firmware_id: str = Field(min_length=1, max_length=128)
    port: str = Field("auto", min_length=1, max_length=256)
    confirmation: str
