from __future__ import annotations

import base64
import csv
import io
import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .database import Database
from .models import (
    AdvertRequest, BackupImportRequest, ChannelRequest, ContactImportRequest,
    ContactUpdateRequest, DatagramRequest, DeviceSettingsRequest, EncryptedBackupRequest,
    EncryptedRestoreRequest, IdentityBackupRequest, MockIncomingRequest, PathRequest,
    SendMessageRequest, TraceRequest,
)
from .service import StationService
from .transports.meshcore_serial import MeshCoreSerialTransport
from .transports.mock import MockTransport


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(settings.database_path)
    if settings.transport == "serial":
        transport = MeshCoreSerialTransport(settings.serial_port, settings.serial_baud, settings.debug_radio)
    elif settings.transport == "mock":
        transport = MockTransport(seed=settings.mock_seed)
    else:
        raise ValueError("MESHCORE_TRANSPORT должен быть mock или serial")
    station = StationService(database, transport)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await station.start()
        yield
        await station.stop()

    app = FastAPI(title="MeshCore Pi Station", version="0.2.0", lifespan=lifespan)
    app.state.station = station
    app.state.settings = settings

    @app.middleware("http")
    async def optional_basic_auth(request: Request, call_next):
        if not settings.web_password:
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        valid = False
        if auth.lower().startswith("basic "):
            try:
                userpass = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
                username, password = userpass.split(":", 1)
                valid = secrets.compare_digest(username, "meshcore") and secrets.compare_digest(password, settings.web_password)
            except Exception:
                valid = False
        if not valid:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="MeshCore Pi Station"'})
        return await call_next(request)

    @app.get("/api/status")
    async def status():
        return station.status

    @app.get("/api/contacts")
    async def contacts():
        return database.list_contacts()

    @app.get("/api/channels")
    async def channels():
        return database.list_channels()

    @app.get("/api/device")
    async def device():
        try:
            return await station.get_device_info()
        except Exception as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.put("/api/device")
    async def update_device(request: DeviceSettingsRequest):
        try:
            return await station.update_device(request.model_dump(exclude_none=True))
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/stats")
    async def stats(refresh: bool = True):
        try:
            return await station.get_stats(refresh=refresh)
        except Exception as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/api/packets")
    async def packets(limit: int = Query(200, ge=1, le=2000)):
        return database.list_packet_events(limit)

    @app.get("/api/map/config")
    async def map_config():
        local = bool(settings.mbtiles_path and settings.mbtiles_path.is_file())
        return {
            "mbtiles": local,
            "tile_url": "/api/tiles/{z}/{x}/{y}.png" if local else settings.tile_url,
            "attribution": "© OpenStreetMap contributors",
        }

    @app.get("/api/tiles/{z}/{x}/{y}.png")
    async def map_tile(z: int, x: int, y: int):
        path = settings.mbtiles_path
        if not path or not path.is_file():
            raise HTTPException(404, "Локальная MBTiles-подложка не настроена")
        tms_y = (1 << z) - 1 - y
        try:
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
                row = connection.execute(
                    "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                    (z, x, tms_y),
                ).fetchone()
        except sqlite3.Error as exc:
            raise HTTPException(500, f"Ошибка MBTiles: {exc}") from exc
        if not row:
            raise HTTPException(404, "Тайл не найден")
        return Response(row[0], media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/messages")
    async def messages(
        target_type: str = Query(pattern="^(contact|channel)$"),
        target_id: str = Query(min_length=1),
        limit: int = Query(200, ge=1, le=500),
    ):
        if target_type == "contact":
            database.mark_read(target_id)
        return database.list_messages(target_type, target_id, limit)

    @app.post("/api/messages")
    async def send_message(request: SendMessageRequest):
        result = await station.send_message(request.target_type, request.target_id, request.text)
        if result["status"] == "failed":
            raise HTTPException(503, result.get("error", "Ошибка отправки"))
        return result

    @app.post("/api/advert")
    async def advert(request: AdvertRequest):
        try:
            await transport.send_advert(request.flood)
        except Exception as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"ok": True, "flood": request.flood}

    @app.patch("/api/contacts/{contact_id}")
    async def update_contact(contact_id: str, request: ContactUpdateRequest):
        try:
            contact = database.update_contact_local(contact_id, **request.model_dump(exclude_none=True))
            await station.broadcast("contact", contact)
            return contact
        except KeyError as exc:
            raise HTTPException(404, "Контакт не найден") from exc

    @app.delete("/api/contacts/{contact_id}")
    async def delete_contact(contact_id: str):
        try:
            await transport.contact_action(contact_id, "remove")
            database.delete_contact(contact_id)
            await station.broadcast("contacts_changed", {"id": contact_id})
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/contacts/import")
    async def import_contact(request: ContactImportRequest):
        try:
            await transport.import_contact(request.uri)
            await station.sync()
            await station.broadcast("contacts_changed", {})
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/contacts/export")
    async def export_self():
        try:
            return {"uri": await transport.export_contact(None)}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/contacts/{contact_id}/export")
    async def export_contact(contact_id: str):
        try:
            return {"uri": await transport.export_contact(contact_id)}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/contacts/{contact_id}/telemetry")
    async def contact_telemetry(contact_id: str):
        try:
            return await transport.contact_action(contact_id, "telemetry")
        except Exception as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/api/contacts/{contact_id}/path/discover")
    async def discover_path(contact_id: str):
        try:
            result = await transport.contact_action(contact_id, "discover_path")
            await station.sync()
            return result
        except Exception as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.delete("/api/contacts/{contact_id}/path")
    async def reset_path(contact_id: str):
        try:
            return await transport.contact_action(contact_id, "reset_path")
        except Exception as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.put("/api/contacts/{contact_id}/path")
    async def set_path(contact_id: str, request: PathRequest):
        try:
            return await transport.contact_action(contact_id, "set_path", request.model_dump())
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/channels")
    async def save_channel(request: ChannelRequest):
        try:
            channel = await transport.save_channel(request.model_dump(exclude_none=True))
            database.upsert_channel(channel)
            await station.broadcast("channels_changed", {"id": channel["id"]})
            return database.get_channel(int(channel["id"]))
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/channels/{channel_id}")
    async def delete_channel(channel_id: int):
        try:
            await transport.delete_channel(channel_id)
            database.delete_channel(channel_id)
            await station.broadcast("channels_changed", {"id": channel_id})
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/channels/{channel_id}/export")
    async def export_channel(channel_id: int):
        channel = database.get_channel(channel_id, include_secret=True)
        if not channel:
            raise HTTPException(404, "Канал не найден")
        query = {"name": channel["name"], "secret": channel["secret"]}
        if channel.get("region_scope"):
            query["region_scope"] = channel["region_scope"]
        return {"uri": "meshcore://channel/add?" + urlencode(query)}

    @app.get("/api/qr")
    async def qr(value: str = Query(min_length=1, max_length=1500)):
        import segno

        output = io.BytesIO()
        segno.make(value, error="m").save(output, kind="svg", scale=5, border=2, dark="#10251f")
        return Response(output.getvalue(), media_type="image/svg+xml",
                        headers={"Cache-Control": "no-store"})

    @app.post("/api/trace")
    async def trace(request: TraceRequest):
        try:
            result = await transport.trace(request.path, request.auth_code)
            event = database.add_packet_event("trace", direction="out", data=result)
            await station.broadcast("packet", event)
            return result
        except Exception as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/api/datagrams")
    async def send_datagram(request: DatagramRequest):
        try:
            result = await transport.send_datagram(
                request.channel_id, request.data_type, bytes.fromhex(request.payload_hex), request.flood
            )
            event = database.add_packet_event("datagram", direction="out",
                                              channel_id=str(request.channel_id), data=result)
            await station.broadcast("packet", event)
            return result
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/backup")
    async def backup():
        return JSONResponse(
            database.export_data(),
            headers={"Content-Disposition": "attachment; filename=meshcore-pi-station-backup.json"},
        )

    @app.post("/api/backup")
    async def restore_backup(request: BackupImportRequest):
        try:
            result = database.import_data(request.data)
            await station.broadcast("database_changed", result)
            return result
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    def backup_key(password: str, salt: bytes) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                         iterations=600_000).derive(password.encode("utf-8"))

    @app.post("/api/backup/encrypted")
    async def encrypted_backup(request: EncryptedBackupRequest):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        salt, nonce = os.urandom(16), os.urandom(12)
        plaintext = json.dumps(database.export_data(), ensure_ascii=False).encode("utf-8")
        encrypted = AESGCM(backup_key(request.password, salt)).encrypt(nonce, plaintext, b"MCPS1")
        payload = b"MCPS1" + salt + nonce + encrypted
        return Response(payload, media_type="application/octet-stream",
                        headers={"Content-Disposition": "attachment; filename=meshcore-backup.mcps"})

    @app.post("/api/backup/restore-encrypted")
    async def restore_encrypted(request: EncryptedRestoreRequest):
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            payload = base64.b64decode(request.payload_base64, validate=True)
            if payload[:5] != b"MCPS1" or len(payload) < 50:
                raise ValueError("Некорректный формат зашифрованного backup")
            salt, nonce, encrypted = payload[5:21], payload[21:33], payload[33:]
            plaintext = AESGCM(backup_key(request.password, salt)).decrypt(nonce, encrypted, b"MCPS1")
            result = database.import_data(json.loads(plaintext))
            await station.broadcast("database_changed", result)
            return result
        except (ValueError, InvalidTag, json.JSONDecodeError) as exc:
            raise HTTPException(400, "Неверный пароль или повреждённый backup") from exc

    @app.post("/api/identity/backup")
    async def identity_backup(request: IdentityBackupRequest):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if request.confirmation != "EXPORT IDENTITY":
            raise HTTPException(400, "Введите точную фразу EXPORT IDENTITY")
        try:
            identity = await transport.export_identity()
            salt, nonce = os.urandom(16), os.urandom(12)
            plaintext = json.dumps(identity, ensure_ascii=False).encode("utf-8")
            encrypted = AESGCM(backup_key(request.password, salt)).encrypt(nonce, plaintext, b"MCID1")
            return Response(b"MCID1" + salt + nonce + encrypted,
                            media_type="application/octet-stream",
                            headers={"Content-Disposition": "attachment; filename=meshcore-identity.mcid"})
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/messages.csv")
    async def export_messages():
        output = io.StringIO()
        rows = database.export_data()["messages"]
        writer = csv.DictWriter(output, fieldnames=["created_at", "target_type", "target_id", "direction", "status", "text", "radio_id"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return Response(output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=meshcore-messages.csv"})

    @app.post("/api/mock/incoming")
    async def mock_incoming(request: MockIncomingRequest):
        if not isinstance(transport, MockTransport):
            raise HTTPException(404, "Доступно только в режиме симулятора")
        await transport.inject_message(request.target_type, request.target_id, request.text)
        return {"ok": True}

    @app.websocket("/ws")
    async def websocket(socket: WebSocket):
        if settings.web_password:
            auth = socket.headers.get("authorization", "")
            try:
                userpass = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
                username, password = userpass.split(":", 1)
                valid = secrets.compare_digest(username, "meshcore") and secrets.compare_digest(password, settings.web_password)
            except Exception:
                valid = False
            if not valid:
                await socket.close(code=1008)
                return
        await station.add_socket(socket)
        try:
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            station.remove_socket(socket)

    static_dir = Path(__file__).with_name("static")

    @app.get("/")
    async def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/service-worker.js")
    async def service_worker():
        return FileResponse(static_dir / "service-worker.js", media_type="application/javascript",
                            headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app


app = create_app()


def run() -> None:
    settings = Settings.from_env()
    uvicorn.run("meshcore_station.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
