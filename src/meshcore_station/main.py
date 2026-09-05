from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import os
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .backup import validate_backup
from .security import LoginLimiter, bootstrap_password
from .database import Database
from .diagnostics import system_diagnostics
from .firmware import FirmwareFlasher
from .firmware_catalog import download_verified, load_catalog
from .models import (
    AdvertRequest, BackupImportRequest, ChannelRequest, ContactImportRequest,
    ContactUpdateRequest, DatagramRequest, DeviceSettingsRequest, EncryptedBackupRequest,
    EncryptedRestoreRequest, IdentityBackupRequest, MockIncomingRequest, PathRequest,
    SendMessageRequest, TraceRequest, SetupRequest, HistoryPolicyRequest, CatalogFlashRequest,
)
from .service import StationService
from .tls import generate_self_signed_certificate
from .transports.meshcore_serial import MeshCoreSerialTransport, list_serial_ports
from .transports.mock import MockTransport


def _valid_basic_authorization(header: str, settings: Settings) -> bool:
    if not settings.web_password:
        return True
    try:
        scheme, encoded = header.split(" ", 1)
        if scheme.lower() != "basic":
            return False
        username, password = base64.b64decode(encoded, validate=True).decode("utf-8").split(":", 1)
        return secrets.compare_digest(username.encode("utf-8"), settings.web_username.encode("utf-8")) and secrets.compare_digest(
            password.encode("utf-8"), settings.web_password.encode("utf-8")
        )
    except (ValueError, UnicodeDecodeError):
        return False


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(
        settings.database_path,
        packet_limit=settings.packet_history_limit,
        stats_limit=settings.stats_history_limit,
    )
    if settings.transport == "serial":
        transport = MeshCoreSerialTransport(settings.serial_port, settings.serial_baud, settings.debug_radio)
    elif settings.transport == "ble":
        transport = MeshCoreSerialTransport(
            settings.ble_address,
            settings.serial_baud,
            settings.debug_radio,
            mode="ble",
            ble_pin=settings.ble_pin,
        )
    elif settings.transport == "mock":
        transport = MockTransport(seed=settings.mock_seed)
    else:
        raise ValueError("MESHCORE_TRANSPORT должен быть mock, serial или ble")
    station = StationService(
        database,
        transport,
        message_retry_seconds=settings.message_retry_seconds,
        message_max_attempts=settings.message_max_attempts,
    )

    async def firmware_event(payload: dict) -> None:
        await station.broadcast("firmware", payload)

    flasher = FirmwareFlasher(
        station, settings.data_dir, settings.serial_port,
        baud=settings.firmware_flash_baud, callback=firmware_event,
    )
    firmware_upload_lock = asyncio.Lock()
    setup_lock = asyncio.Lock()
    auth_settings = settings
    bootstrap = not bool(settings.web_password)
    limiter = LoginLimiter()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal auth_settings
        if bootstrap:
            auth_settings = replace(settings, web_password=bootstrap_password(settings.data_dir))
        await station.start()
        policy = database.get_settings()
        database.prune_history(
            int(policy.get("history_days", settings.history_days)),
            int(policy.get("packet_limit", settings.packet_history_limit)),
            int(policy.get("stats_limit", settings.stats_history_limit)),
        )
        yield
        await flasher.close()
        await station.stop()

    app = FastAPI(title="MeshCore Pi Station", version="0.7.0", lifespan=lifespan)
    app.state.station = station
    app.state.settings = settings
    app.state.flasher = flasher

    @app.middleware("http")
    async def optional_basic_auth(request: Request, call_next):
        peer = request.client.host if request.client else "unknown"
        if limiter.blocked(peer):
            return Response(status_code=429, headers={"Retry-After": "60"})
        if not _valid_basic_authorization(request.headers.get("authorization", ""), auth_settings):
            if request.headers.get("authorization"):
                limiter.failed(peer)
            message = "Initial login: use the configured username and the setup-token file in the station data directory. / Первый вход: пароль находится в файле setup-token в каталоге данных станции." if bootstrap else "Authentication required"
            return Response(message, status_code=401, headers={"WWW-Authenticate": 'Basic realm="MeshCore Pi Station", charset="UTF-8"'})
        limiter.success(peer)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
        response = await call_next(request)
        return response

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.get("/api/status")
    async def status():
        return {**station.status, "version": "0.7.0", "onboarding_complete": bool(database.get_settings().get("onboarding_complete"))}

    @app.get("/api/setup")
    async def setup_status():
        pending = {}
        if settings.runtime_config_path.is_file():
            pending = json.loads(settings.runtime_config_path.read_text(encoding="utf-8"))
        return {
            "complete": bool(database.get_settings().get("onboarding_complete")),
            "transport": pending.get("transport", settings.transport),
            "serial_port": pending.get("serial_port", settings.serial_port),
            "ble_address": pending.get("ble_address", settings.ble_address),
            "serial_ports": list_serial_ports(),
            "web_username": auth_settings.web_username,
            "password_required": bootstrap,
            "https_enabled": bool(pending.get("tls_cert", settings.tls_cert) and pending.get("tls_key", settings.tls_key)),
        }

    @app.post("/api/setup")
    async def save_setup(request: SetupRequest):
        async with setup_lock:
            return await persist_setup(request)

    async def persist_setup(request: SetupRequest):
        nonlocal auth_settings, bootstrap
        if request.serial_port != "auto" and not request.serial_port.startswith("/dev/"):
            raise HTTPException(400, "USB-порт должен находиться в /dev или иметь значение auto")
        if request.web_password and len(request.web_password) < 12:
            raise HTTPException(400, "Пароль должен содержать не менее 12 символов")
        if bootstrap and not request.web_password:
            raise HTTPException(400, "Задайте постоянный пароль не менее 12 символов")
        if ":" in request.web_username:
            raise HTTPException(400, "Имя пользователя не может содержать двоеточие")
        payload = request.model_dump()
        if not payload["web_password"]:
            payload["web_password"] = auth_settings.web_password
        if not payload["ble_pin"]:
            payload["ble_pin"] = settings.ble_pin
        existing = {}
        if settings.runtime_config_path.is_file():
            existing = json.loads(settings.runtime_config_path.read_text(encoding="utf-8"))
        if not request.ble_pin:
            payload["ble_pin"] = existing.get("ble_pin", settings.ble_pin)
        payload["tls_cert"] = ""
        payload["tls_key"] = ""
        if request.enable_https:
            cert = existing.get("tls_cert") or settings.tls_cert
            key = existing.get("tls_key") or settings.tls_key
            if not cert or not key or not Path(cert).is_file() or not Path(key).is_file():
                cert, key = await asyncio.to_thread(generate_self_signed_certificate, settings.data_dir / "tls")
            payload["tls_cert"], payload["tls_key"] = str(cert), str(key)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.runtime_config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = settings.runtime_config_path.with_suffix(".json.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
        temporary.replace(settings.runtime_config_path)
        try:
            settings.runtime_config_path.chmod(0o600)
        except OSError:
            pass
        database.set_setting("onboarding_complete", True)
        database.set_setting("configured_transport", request.transport)
        auth_settings = replace(auth_settings, web_username=request.web_username, web_password=payload["web_password"])
        bootstrap = False
        (settings.data_dir / "setup-token").unlink(missing_ok=True)
        for socket in tuple(station._sockets):
            await socket.close(code=1008)
            station.remove_socket(socket)
        return {
            "ok": True,
            "restart_required": True,
            "transport": request.transport,
            "serial_port": request.serial_port,
            "ble_address": request.ble_address,
            "language": request.language,
            "web_username": request.web_username,
            "https_enabled": request.enable_https,
        }

    @app.get("/api/system/diagnostics")
    async def diagnostics():
        return await asyncio.to_thread(system_diagnostics, settings.data_dir, list_serial_ports())

    @app.get("/api/history/policy")
    async def history_policy():
        saved = database.get_settings()
        return {
            "history_days": int(saved.get("history_days", settings.history_days)),
            "packet_limit": int(saved.get("packet_limit", settings.packet_history_limit)),
            "stats_limit": int(saved.get("stats_limit", settings.stats_history_limit)),
        }

    @app.put("/api/history/policy")
    async def update_history_policy(request: HistoryPolicyRequest):
        values = request.model_dump()
        for key, value in values.items():
            database.set_setting(key, value)
        removed = database.prune_history(**values)
        return {**values, "removed": removed}

    @app.get("/api/firmware/status")
    async def firmware_status():
        return {
            **flasher.status,
            "enabled": settings.firmware_flash_enabled,
            "auth_configured": not bootstrap,
            "ports": list_serial_ports(),
            "max_bytes": 16 * 1024 * 1024,
            "catalog_configured": bool(settings.firmware_catalog_url),
        }

    @app.get("/api/firmware/catalog")
    async def firmware_catalog():
        try:
            entries = await asyncio.to_thread(load_catalog, settings.firmware_catalog_url)
            return {"configured": bool(settings.firmware_catalog_url), "firmware": entries}
        except Exception as exc:
            raise HTTPException(502, f"Не удалось загрузить каталог: {exc}") from exc

    @app.post("/api/firmware/catalog/flash", status_code=202)
    async def flash_catalog_firmware(request: CatalogFlashRequest):
        if not settings.firmware_flash_enabled or bootstrap:
            raise HTTPException(403, "Прошивка требует включённой функции и входа по паролю")
        if request.confirmation != "HELTEC V4":
            raise HTTPException(400, "Введите подтверждение HELTEC V4")
        if request.port != "auto" and not request.port.startswith("/dev/"):
            raise HTTPException(400, "USB-порт должен находиться в /dev или иметь значение auto")
        try:
            catalog = await asyncio.to_thread(load_catalog, settings.firmware_catalog_url)
            entry = next(item for item in catalog if item["id"] == request.firmware_id)
        except StopIteration as exc:
            raise HTTPException(404, "Прошивка отсутствует в доверенном каталоге") from exc
        except Exception as exc:
            raise HTTPException(502, f"Не удалось загрузить каталог: {exc}") from exc
        async with firmware_upload_lock:
            if flasher.status["busy"]:
                raise HTTPException(409, "Прошивка уже выполняется")
            upload_dir = settings.data_dir / "firmware"
            upload_dir.mkdir(parents=True, exist_ok=True)
            image_path = upload_dir / f"{uuid.uuid4().hex}.bin"
            try:
                await asyncio.to_thread(download_verified, entry, image_path)
                flasher.start(image_path, entry["filename"], entry["mode"], request.port)
            except Exception as exc:
                image_path.unlink(missing_ok=True)
                raise HTTPException(400, f"Проверка прошивки не пройдена: {exc}") from exc
        return {**flasher.status, "verified_sha256": entry["sha256"]}

    @app.post("/api/firmware/flash", status_code=202)
    async def firmware_flash(
        request: Request,
        filename: str = Query(min_length=1, max_length=180),
        mode: str = Query(pattern="^(update|full)$"),
        port: str = Query(default="auto", min_length=1, max_length=256),
    ):
        if not settings.firmware_flash_enabled:
            raise HTTPException(403, "Прошивка отключена в конфигурации станции")
        if bootstrap:
            raise HTTPException(403, "Для прошивки необходимо включить вход по паролю")
        if request.headers.get("x-flash-confirmation") != "HELTEC V4":
            raise HTTPException(400, "Введите подтверждение HELTEC V4")
        safe_name = Path(filename).name
        lower_name = safe_name.lower()
        if not lower_name.endswith(".bin"):
            raise HTTPException(400, "Поддерживаются только ESP32-S3 .bin образы")
        merged = lower_name.endswith("merged.bin")
        if mode == "full" and not merged:
            raise HTTPException(400, "Для полной прошивки выберите файл *merged.bin")
        if mode == "update" and merged:
            raise HTTPException(400, "Merged-образ необходимо прошивать в полном режиме")
        if port != "auto" and not port.startswith("/dev/"):
            raise HTTPException(400, "USB-порт должен находиться в /dev или иметь значение auto")

        async with firmware_upload_lock:
            if flasher.status["busy"]:
                raise HTTPException(409, "Прошивка уже выполняется")
            upload_dir = settings.data_dir / "firmware"
            upload_dir.mkdir(parents=True, exist_ok=True)
            image_path = upload_dir / f"{uuid.uuid4().hex}.bin"
            size = 0
            try:
                with image_path.open("wb") as output:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > 16 * 1024 * 1024:
                            raise HTTPException(413, "Размер прошивки превышает 16 MiB")
                        output.write(chunk)
                if size < 4096:
                    raise HTTPException(400, "Файл прошивки слишком мал")
                with image_path.open("rb") as image:
                    if image.read(1) != b"\xe9":
                        raise HTTPException(400, "Файл не похож на ESP32 image")
                flasher.start(image_path, safe_name, mode, port)
            except Exception:
                if not flasher.status["busy"]:
                    image_path.unlink(missing_ok=True)
                raise
        return flasher.status

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
        result = await station.send_message(request.target_type, request.target_id, request.text, request.ttl_seconds)
        if result["status"] == "failed":
            raise HTTPException(503, result.get("error", "Ошибка отправки"))
        return result

    @app.post("/api/messages/{message_id}/cancel")
    async def cancel_message(message_id: int):
        return await change_message(message_id, "cancel")

    @app.post("/api/messages/{message_id}/retry")
    async def retry_message(message_id: int):
        return await change_message(message_id, "retry")

    async def change_message(message_id: int, action: str):
        try:
            return await station.message_action(message_id, action)
        except KeyError as exc:
            raise HTTPException(404, "Сообщение не найдено") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

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
            async with station._send_lock:
                result = database.import_data(request.data)
            await station.broadcast("database_changed", result)
            return result
        except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            raise HTTPException(400, "Некорректная резервная копия; данные не изменены") from exc

    @app.post("/api/backup/preview")
    async def preview_backup(request: BackupImportRequest):
        try:
            return validate_backup(request.data)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, "Некорректная резервная копия") from exc

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
            async with station._send_lock:
                result = database.import_data(json.loads(plaintext))
            await station.broadcast("database_changed", result)
            return result
        except (ValueError, InvalidTag, KeyError, TypeError, sqlite3.Error) as exc:
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
        origin = socket.headers.get("origin")
        peer = socket.client.host if socket.client else "unknown"
        if limiter.blocked(peer) or (origin and urlsplit(origin).netloc != socket.headers.get("host")):
            await socket.close(code=1008)
            return
        if not _valid_basic_authorization(socket.headers.get("authorization", ""), auth_settings):
            limiter.failed(peer)
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
    if settings.web_password and not settings.web_username:
        raise RuntimeError("MESHCORE_WEB_USERNAME не может быть пустым при включённом пароле")
    if bool(settings.tls_cert) != bool(settings.tls_key):
        raise RuntimeError("MESHCORE_TLS_CERT и MESHCORE_TLS_KEY должны быть заданы вместе")
    for path in (settings.tls_cert, settings.tls_key):
        if path and not path.is_file():
            raise RuntimeError(f"Файл TLS не найден: {path}")
    uvicorn.run(
        "meshcore_station.main:app",
        host=settings.host,
        port=settings.port,
        proxy_headers=False,
        ssl_certfile=str(settings.tls_cert) if settings.tls_cert else None,
        ssl_keyfile=str(settings.tls_key) if settings.tls_key else None,
        ssl_keyfile_password=settings.tls_key_password or None,
    )


if __name__ == "__main__":
    run()
