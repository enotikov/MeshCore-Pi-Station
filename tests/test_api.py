import asyncio
import base64
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from meshcore_station.config import Settings
from meshcore_station.main import create_app
from meshcore_station.database import Database
from meshcore_station.firmware import build_esptool_commands
from meshcore_station.service import StationService
from meshcore_station.transports.meshcore_serial import MeshCoreSerialTransport
from meshcore_station.transports.mock import MockTransport


def settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8080,
        data_dir=tmp_path,
        transport="mock",
        serial_port="auto",
        serial_baud=115200,
        ble_address="auto",
        ble_pin="",
        debug_radio=False,
        mock_seed=True,
        web_username="meshcore",
        web_password="",
        tls_cert=None,
        tls_key=None,
        tls_key_password="",
        firmware_flash_enabled=False,
        firmware_flash_baud=460800,
        mbtiles_path=None,
        tile_url="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    )


def test_mock_station_flow(tmp_path: Path):
    with TestClient(create_app(settings(tmp_path))) as client:
        status = client.get("/api/status").json()
        assert status["connected"] is True
        assert status["mode"] == "mock"

        contacts = client.get("/api/contacts").json()
        channels = client.get("/api/channels").json()
        assert contacts
        assert channels

        contact_id = contacts[0]["id"]
        response = client.post(
            "/api/messages",
            json={"target_type": "contact", "target_id": contact_id, "text": "Проверка"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "delivered"

        incoming = client.post(
            "/api/mock/incoming",
            json={"target_type": "contact", "target_id": contact_id, "text": "Ответ"},
        )
        assert incoming.status_code == 200
        history = client.get(
            "/api/messages", params={"target_type": "contact", "target_id": contact_id}
        ).json()
        assert [item["text"] for item in history] == ["Проверка", "Ответ"]


def test_rejects_long_message(tmp_path: Path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/api/messages",
            json={"target_type": "channel", "target_id": "0", "text": "x" * 161},
        )
        assert response.status_code == 422


def test_radio_reconnects_after_initial_failure(tmp_path: Path):
    class FlakyTransport(MockTransport):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def start(self, handler):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("USB ещё не подключён")
            await super().start(handler)

    async def scenario():
        transport = FlakyTransport()
        station = StationService(
            Database(tmp_path / "reconnect.db"), transport, reconnect_interval=0.01
        )
        await station.start()
        assert station.status["connected"] is False
        await asyncio.sleep(0.05)
        assert station.status["connected"] is True
        assert transport.attempts == 2
        await station.stop()

    asyncio.run(scenario())


def test_management_and_diagnostics_api(tmp_path: Path):
    with TestClient(create_app(settings(tmp_path))) as client:
        device = client.get("/api/device").json()
        assert device["max_tx_power"] == 22
        assert client.put("/api/device", json={"tx_power": 23}).status_code == 400
        updated = client.put(
            "/api/device", json={"name": "Orel Base", "tx_power": 20, "latitude": 52.97,
                                 "duty_cycle": 10, "cad": True, "flood_max": 32}
        ).json()
        assert updated["name"] == "Orel Base"
        assert updated["tx_power"] == 20
        assert updated["duty_cycle"] == 10
        assert updated["cad"] is True

        stats = client.get("/api/stats").json()
        assert {"core", "radio", "packets", "history"} <= stats.keys()
        assert stats["packets"]["recv"] >= 0
        map_config = client.get("/api/map/config").json()
        assert map_config["mbtiles"] is False
        assert "{z}" in map_config["tile_url"]

        contact_id = client.get("/api/contacts").json()[0]["id"]
        patched = client.patch(
            f"/api/contacts/{contact_id}", json={"favorite": True, "notes": "test"}
        ).json()
        assert patched["favorite"] == 1
        assert client.post(f"/api/contacts/{contact_id}/telemetry").status_code == 200
        assert client.post(f"/api/contacts/{contact_id}/path/discover").status_code == 200
        uri = client.get(f"/api/contacts/{contact_id}/export").json()["uri"]
        assert uri.startswith("meshcore://contact/add?")
        assert client.get("/api/qr", params={"value": uri}).headers["content-type"].startswith("image/svg+xml")

        channel = {"id": 3, "name": "#Field", "region_scope": "Orel"}
        assert client.post("/api/channels", json=channel).status_code == 200
        exported = client.get("/api/channels/3/export").json()["uri"]
        assert exported.startswith("meshcore://channel/add?")
        assert client.delete("/api/channels/3").status_code == 200

        assert client.post("/api/trace", json={"path": "a1,c3"}).status_code == 200
        datagram = client.post(
            "/api/datagrams",
            json={"channel_id": 0, "data_type": 65535, "payload_hex": "a1b2c3", "flood": True},
        )
        assert datagram.status_code == 200
        backup = client.get("/api/backup").json()
        assert backup["version"] == 1
        assert client.post("/api/backup", json={"data": backup}).status_code == 200
        encrypted = client.post("/api/backup/encrypted", json={"password": "test-password"})
        assert encrypted.content.startswith(b"MCPS1")
        restored = client.post(
            "/api/backup/restore-encrypted",
            json={"password": "test-password", "payload_base64": base64.b64encode(encrypted.content).decode()},
        )
        assert restored.status_code == 200
        wrong = client.post(
            "/api/backup/restore-encrypted",
            json={"password": "wrong-pass", "payload_base64": base64.b64encode(encrypted.content).decode()},
        )
        assert wrong.status_code == 400
        identity = client.post(
            "/api/identity/backup",
            json={"password": "identity-pass", "confirmation": "EXPORT IDENTITY"},
        )
        assert identity.status_code == 200
        assert identity.content.startswith(b"MCID1")
        assert "text/csv" in client.get("/api/messages.csv").headers["content-type"]


def test_optional_basic_auth(tmp_path: Path):
    protected = replace(settings(tmp_path), web_username="operator", web_password="secret")
    with TestClient(create_app(protected)) as client:
        assert client.get("/api/status").status_code == 401
        assert client.get("/api/status", auth=("meshcore", "wrong")).status_code == 401
        assert client.get("/api/status", auth=("meshcore", "secret")).status_code == 401
        response = client.get("/api/status", auth=("operator", "secret"))
        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"


def test_ble_transport_status():
    transport = MeshCoreSerialTransport("auto", mode="ble", ble_pin="123456")
    assert transport.status["mode"] == "ble"
    assert transport.status["port"] == "auto"
    assert transport.status["baud"] is None


def test_firmware_flash_is_disabled_by_default(tmp_path: Path):
    with TestClient(create_app(settings(tmp_path))) as client:
        status = client.get("/api/firmware/status").json()
        assert status["enabled"] is False
        response = client.post(
            "/api/firmware/flash?filename=firmware.bin&mode=update&port=auto",
            content=b"\xe9" + b"\0" * 4095,
            headers={"X-Flash-Confirmation": "HELTEC V4"},
        )
        assert response.status_code == 403


def test_esptool_commands_use_safe_heltec_v4_offsets(tmp_path: Path):
    image = tmp_path / "firmware.bin"
    update = build_esptool_commands("/dev/ttyACM0", image, "update", 460800)
    full = build_esptool_commands("/dev/ttyACM0", image, "full", 460800)
    assert update[-1][-2] == "0x10000"
    assert full[0][-1] == "erase_flash"
    assert full[-1][-2] == "0x0"


def test_valid_firmware_upload_is_queued(tmp_path: Path):
    configured = replace(
        settings(tmp_path), firmware_flash_enabled=True, web_password="secret"
    )
    app = create_app(configured)
    captured = {}

    def fake_start(image, filename, mode, port):
        captured.update(filename=filename, mode=mode, port=port, magic=image.read_bytes()[:1])
        image.unlink()

    app.state.flasher.start = fake_start
    with TestClient(app) as client:
        response = client.post(
            "/api/firmware/flash?filename=companion.bin&mode=update&port=auto",
            content=b"\xe9" + b"\0" * 4095,
            headers={"X-Flash-Confirmation": "HELTEC V4"},
            auth=("meshcore", "secret"),
        )
    assert response.status_code == 202
    assert captured == {
        "filename": "companion.bin", "mode": "update", "port": "auto", "magic": b"\xe9"
    }
