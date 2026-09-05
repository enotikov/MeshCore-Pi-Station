from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    transport: str
    serial_port: str
    serial_baud: int
    ble_address: str
    ble_pin: str
    debug_radio: bool
    mock_seed: bool
    web_username: str
    web_password: str
    tls_cert: Path | None
    tls_key: Path | None
    tls_key_password: str
    firmware_flash_enabled: bool
    firmware_flash_baud: int
    mbtiles_path: Path | None
    tile_url: str
    firmware_catalog_url: str = ""
    message_retry_seconds: float = 10.0
    message_max_attempts: int = 5
    history_days: int = 30
    packet_history_limit: int = 10000
    stats_history_limit: int = 1440
    config_file: Path | None = None

    @property
    def database_path(self) -> Path:
        return self.data_dir / "station.db"

    @property
    def runtime_config_path(self) -> Path:
        return self.config_file or self.data_dir / "station.json"

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("MESHCORE_DATA_DIR", "data")).expanduser()
        runtime: dict[str, object] = {}
        config_path = Path(os.getenv("MESHCORE_CONFIG_FILE", str(data_dir / "station.json"))).expanduser()
        try:
            if config_path.is_file():
                loaded = json.loads(config_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    runtime = loaded
        except (OSError, ValueError):
            runtime = {}

        def value(env_name: str, key: str, default: object) -> object:
            return runtime.get(key, os.getenv(env_name, default))

        return cls(
            config_file=config_path,
            host=os.getenv("MESHCORE_HOST", "0.0.0.0"),
            port=int(os.getenv("MESHCORE_PORT", "8080")),
            data_dir=data_dir,
            transport=str(value("MESHCORE_TRANSPORT", "transport", "mock")).strip().lower(),
            serial_port=str(value("MESHCORE_SERIAL_PORT", "serial_port", "auto")).strip(),
            serial_baud=int(os.getenv("MESHCORE_SERIAL_BAUD", "115200")),
            ble_address=str(value("MESHCORE_BLE_ADDRESS", "ble_address", "auto")).strip(),
            ble_pin=str(value("MESHCORE_BLE_PIN", "ble_pin", "")),
            debug_radio=_as_bool(os.getenv("MESHCORE_RADIO_DEBUG")),
            mock_seed=_as_bool(os.getenv("MESHCORE_MOCK_SEED"), True),
            web_username=str(value("MESHCORE_WEB_USERNAME", "web_username", "meshcore")),
            web_password=str(value("MESHCORE_WEB_PASSWORD", "web_password", "")),
            tls_cert=Path(str(value("MESHCORE_TLS_CERT", "tls_cert", ""))).expanduser()
            if value("MESHCORE_TLS_CERT", "tls_cert", "") else None,
            tls_key=Path(str(value("MESHCORE_TLS_KEY", "tls_key", ""))).expanduser()
            if value("MESHCORE_TLS_KEY", "tls_key", "") else None,
            tls_key_password=os.getenv("MESHCORE_TLS_KEY_PASSWORD", ""),
            firmware_flash_enabled=_as_bool(os.getenv("MESHCORE_FIRMWARE_FLASH")),
            firmware_flash_baud=int(os.getenv("MESHCORE_FIRMWARE_BAUD", "460800")),
            mbtiles_path=Path(os.environ["MESHCORE_MBTILES_PATH"]).expanduser()
            if os.getenv("MESHCORE_MBTILES_PATH") else None,
            tile_url=os.getenv(
                "MESHCORE_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            ),
            firmware_catalog_url=os.getenv("MESHCORE_FIRMWARE_CATALOG_URL", "builtin").strip(),
            message_retry_seconds=max(1.0, float(os.getenv("MESHCORE_MESSAGE_RETRY_SECONDS", "10"))),
            message_max_attempts=max(1, int(os.getenv("MESHCORE_MESSAGE_MAX_ATTEMPTS", "5"))),
            history_days=max(1, int(os.getenv("MESHCORE_HISTORY_DAYS", "30"))),
            packet_history_limit=max(100, int(os.getenv("MESHCORE_PACKET_HISTORY_LIMIT", "10000"))),
            stats_history_limit=max(60, int(os.getenv("MESHCORE_STATS_HISTORY_LIMIT", "1440"))),
        )
