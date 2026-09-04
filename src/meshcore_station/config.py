from __future__ import annotations

import os
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
    debug_radio: bool
    mock_seed: bool
    web_password: str
    mbtiles_path: Path | None
    tile_url: str

    @property
    def database_path(self) -> Path:
        return self.data_dir / "station.db"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.getenv("MESHCORE_HOST", "0.0.0.0"),
            port=int(os.getenv("MESHCORE_PORT", "8080")),
            data_dir=Path(os.getenv("MESHCORE_DATA_DIR", "data")).expanduser(),
            transport=os.getenv("MESHCORE_TRANSPORT", "mock").strip().lower(),
            serial_port=os.getenv("MESHCORE_SERIAL_PORT", "auto").strip(),
            serial_baud=int(os.getenv("MESHCORE_SERIAL_BAUD", "115200")),
            debug_radio=_as_bool(os.getenv("MESHCORE_RADIO_DEBUG")),
            mock_seed=_as_bool(os.getenv("MESHCORE_MOCK_SEED"), True),
            web_password=os.getenv("MESHCORE_WEB_PASSWORD", ""),
            mbtiles_path=Path(os.environ["MESHCORE_MBTILES_PATH"]).expanduser()
            if os.getenv("MESHCORE_MBTILES_PATH") else None,
            tile_url=os.getenv(
                "MESHCORE_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            ),
        )
