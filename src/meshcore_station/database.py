from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: Path, *, packet_limit: int = 10000, stats_limit: int = 1440):
        self.path = path
        self.packet_limit = packet_limit
        self.stats_limit = stats_limit
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS contacts (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    public_key TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'chat',
                    latitude REAL,
                    longitude REAL,
                    last_seen INTEGER,
                    last_snr REAL,
                    unread INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    secret TEXT NOT NULL DEFAULT '',
                    region_scope TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    radio_id TEXT,
                    target_type TEXT NOT NULL CHECK(target_type IN ('contact', 'channel')),
                    target_id TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('in', 'out')),
                    text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_messages_target
                    ON messages(target_type, target_id, created_at);

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS packet_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    direction TEXT NOT NULL DEFAULT '',
                    contact_id TEXT,
                    channel_id TEXT,
                    created_at INTEGER NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_packet_events_time
                    ON packet_events(created_at DESC);

                CREATE TABLE IF NOT EXISTS stats_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at INTEGER NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_stats_samples_time
                    ON stats_samples(created_at DESC);

                CREATE TABLE IF NOT EXISTS message_status_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_message_status_events_message
                    ON message_status_events(message_id, created_at, id);
                """
            )
            columns = {
                row[1] for row in self._connection.execute("PRAGMA table_info(contacts)").fetchall()
            }
            for name, definition in (
                ("favorite", "INTEGER NOT NULL DEFAULT 0"),
                ("blocked", "INTEGER NOT NULL DEFAULT 0"),
                ("notes", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    self._connection.execute(f"ALTER TABLE contacts ADD COLUMN {name} {definition}")
            channel_columns = {
                row[1] for row in self._connection.execute("PRAGMA table_info(channels)").fetchall()
            }
            for name in ("secret", "region_scope"):
                if name not in channel_columns:
                    self._connection.execute(
                        f"ALTER TABLE channels ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                    )
            message_columns = {
                row[1] for row in self._connection.execute("PRAGMA table_info(messages)").fetchall()
            }
            for name, definition in (
                ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_attempt", "INTEGER"),
                ("error", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in message_columns:
                    self._connection.execute(f"ALTER TABLE messages ADD COLUMN {name} {definition}")
            interrupted = self._connection.execute(
                "SELECT id FROM messages WHERE direction='out' AND status='sending'"
            ).fetchall()
            if interrupted:
                timestamp = int(time.time())
                self._connection.execute(
                    "UPDATE messages SET status='queued', error='Станция была перезапущена' "
                    "WHERE direction='out' AND status='sending'"
                )
                self._connection.executemany(
                    "INSERT INTO message_status_events(message_id, status, created_at, detail) "
                    "VALUES(?, 'queued', ?, 'Станция была перезапущена')",
                    [(int(row["id"]), timestamp) for row in interrupted],
                )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def upsert_contact(self, contact: dict[str, Any]) -> dict[str, Any]:
        contact_id = str(contact["id"])
        payload = {
            "id": contact_id,
            "name": str(contact.get("name") or contact_id[:12]),
            "public_key": str(contact.get("public_key") or contact_id),
            "kind": str(contact.get("kind") or "chat"),
            "latitude": contact.get("latitude"),
            "longitude": contact.get("longitude"),
            "last_seen": int(contact.get("last_seen") or time.time()),
            "last_snr": contact.get("last_snr"),
            "raw_json": json.dumps(contact.get("raw") or contact, ensure_ascii=False),
        }
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO contacts
                    (id, name, public_key, kind, latitude, longitude, last_seen, last_snr, raw_json)
                VALUES
                    (:id, :name, :public_key, :kind, :latitude, :longitude, :last_seen, :last_snr, :raw_json)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    public_key=excluded.public_key,
                    kind=excluded.kind,
                    latitude=COALESCE(excluded.latitude, contacts.latitude),
                    longitude=COALESCE(excluded.longitude, contacts.longitude),
                    last_seen=MAX(excluded.last_seen, contacts.last_seen),
                    last_snr=COALESCE(excluded.last_snr, contacts.last_snr),
                    raw_json=excluded.raw_json
                """,
                payload,
            )
        return self.get_contact(contact_id) or payload

    def get_contact(self, contact_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM contacts WHERE id=?", (contact_id,)
            ).fetchone()
        return self._contact_row(row) if row else None

    def list_contacts(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM contacts ORDER BY favorite DESC, unread DESC, name COLLATE NOCASE"
            ).fetchall()
        return [self._contact_row(row) for row in rows]

    @staticmethod
    def _contact_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["raw"] = json.loads(result.pop("raw_json"))
        return result

    def upsert_channel(self, channel: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "id": int(channel["id"]),
            "name": str(channel.get("name") or f"Channel {channel['id']}"),
            "enabled": int(bool(channel.get("enabled", True))),
            "secret": str(channel.get("secret") or ""),
            "region_scope": str(channel.get("region_scope") or ""),
        }
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO channels(id, name, enabled, secret, region_scope)
                VALUES(:id, :name, :enabled, :secret, :region_scope)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, enabled=excluded.enabled,
                    secret=CASE WHEN excluded.secret='' THEN channels.secret ELSE excluded.secret END,
                    region_scope=excluded.region_scope
                """,
                payload,
            )
        return payload

    def list_channels(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, name, enabled, CASE WHEN secret='' THEN 0 ELSE 1 END AS has_secret, region_scope FROM channels WHERE enabled=1 ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_channel(self, channel_id: int, include_secret: bool = False) -> dict[str, Any] | None:
        fields = "id, name, enabled, secret, region_scope" if include_secret else "id, name, enabled, CASE WHEN secret='' THEN 0 ELSE 1 END AS has_secret, region_scope"
        with self._lock:
            row = self._connection.execute(
                f"SELECT {fields} FROM channels WHERE id=?", (channel_id,)
            ).fetchone()
        return dict(row) if row else None

    def add_message(
        self,
        *,
        target_type: str,
        target_id: str,
        direction: str,
        text: str,
        status: str,
        radio_id: str | None = None,
        created_at: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = (
            radio_id,
            target_type,
            str(target_id),
            direction,
            text,
            status,
            int(created_at or time.time()),
            json.dumps(metadata or {}, ensure_ascii=False),
        )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO messages
                    (radio_id, target_type, target_id, direction, text, status, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
            message_id = cursor.lastrowid
            self._connection.execute(
                "INSERT INTO message_status_events(message_id, status, created_at) VALUES(?, ?, ?)",
                (message_id, status, int(created_at or time.time())),
            )
            if direction == "in" and target_type == "contact":
                self._connection.execute(
                    "UPDATE contacts SET unread=unread+1 WHERE id=?", (str(target_id),)
                )
        return self.get_message(int(message_id))

    def get_message(self, message_id: int) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM messages WHERE id=?", (message_id,)
            ).fetchone()
        if row is None:
            raise KeyError(message_id)
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        result["timeline"] = self.message_timeline(message_id)
        return result

    def update_message(
        self,
        message_id: int,
        status: str,
        radio_id: str | None = None,
        *,
        error: str = "",
        attempted: bool = False,
    ) -> dict[str, Any]:
        timestamp = int(time.time())
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE messages SET status=?, radio_id=COALESCE(?, radio_id), error=?,
                   attempt_count=attempt_count+?, last_attempt=CASE WHEN ? THEN ? ELSE last_attempt END
                   WHERE id=?""",
                (status, radio_id, error, int(attempted), int(attempted), timestamp, message_id),
            )
            self._connection.execute(
                "INSERT INTO message_status_events(message_id, status, created_at, detail) VALUES(?, ?, ?, ?)",
                (message_id, status, timestamp, error),
            )
        return self.get_message(message_id)

    def message_timeline(self, message_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT status, created_at, detail FROM message_status_events WHERE message_id=? ORDER BY id",
                (message_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def queued_messages(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id FROM messages WHERE direction='out' AND status='queued' ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        return [self.get_message(int(row["id"])) for row in rows]

    def list_messages(self, target_type: str, target_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM (
                    SELECT * FROM messages
                    WHERE target_type=? AND target_id=?
                    ORDER BY created_at DESC, id DESC LIMIT ?
                ) ORDER BY created_at, id
                """,
                (target_type, str(target_id), limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            item["timeline"] = self.message_timeline(int(item["id"]))
            result.append(item)
        return result

    def mark_read(self, contact_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("UPDATE contacts SET unread=0 WHERE id=?", (contact_id,))

    def update_contact_local(self, contact_id: str, **values: Any) -> dict[str, Any]:
        allowed = {"name", "favorite", "blocked", "notes"}
        values = {key: value for key, value in values.items() if key in allowed and value is not None}
        if not values:
            contact = self.get_contact(contact_id)
            if contact is None:
                raise KeyError(contact_id)
            return contact
        assignments = ", ".join(f"{key}=?" for key in values)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                f"UPDATE contacts SET {assignments} WHERE id=?",
                (*values.values(), contact_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(contact_id)
        return self.get_contact(contact_id)  # type: ignore[return-value]

    def delete_contact(self, contact_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM contacts WHERE id=?", (contact_id,))

    def delete_channel(self, channel_id: int) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM channels WHERE id=?", (channel_id,))

    def add_packet_event(
        self,
        event_type: str,
        *,
        direction: str = "",
        contact_id: str | None = None,
        channel_id: str | None = None,
        data: dict[str, Any] | None = None,
        created_at: int | None = None,
    ) -> dict[str, Any]:
        timestamp = int(created_at or time.time())
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO packet_events(event_type, direction, contact_id, channel_id, created_at, data_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_type, direction, contact_id, channel_id, timestamp,
                 json.dumps(data or {}, ensure_ascii=False)),
            )
            event_id = int(cursor.lastrowid)
            self._connection.execute(
                "DELETE FROM packet_events WHERE id NOT IN (SELECT id FROM packet_events ORDER BY id DESC LIMIT ?)"
                , (self.packet_limit,)
            )
        return {"id": event_id, "event_type": event_type, "direction": direction,
                "contact_id": contact_id, "channel_id": channel_id,
                "created_at": timestamp, "data": data or {}}

    def list_packet_events(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM packet_events ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item.pop("data_json"))
            result.append(item)
        return result

    def add_stats_sample(self, data: dict[str, Any]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO stats_samples(created_at, data_json) VALUES(?, ?)",
                (int(time.time()), json.dumps(data, ensure_ascii=False)),
            )
            self._connection.execute(
                "DELETE FROM stats_samples WHERE id NOT IN (SELECT id FROM stats_samples ORDER BY id DESC LIMIT ?)"
                , (self.stats_limit,)
            )

    def prune_history(self, history_days: int, packet_limit: int, stats_limit: int) -> dict[str, int]:
        cutoff = int(time.time()) - history_days * 86400
        self.packet_limit = packet_limit
        self.stats_limit = stats_limit
        with self._lock, self._connection:
            before_messages = self._connection.total_changes
            self._connection.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
            removed_messages = self._connection.total_changes - before_messages
            before_packets = self._connection.total_changes
            self._connection.execute("DELETE FROM packet_events WHERE created_at < ?", (cutoff,))
            self._connection.execute(
                "DELETE FROM packet_events WHERE id NOT IN (SELECT id FROM packet_events ORDER BY id DESC LIMIT ?)",
                (packet_limit,),
            )
            removed_packets = self._connection.total_changes - before_packets
            before_stats = self._connection.total_changes
            self._connection.execute("DELETE FROM stats_samples WHERE created_at < ?", (cutoff,))
            self._connection.execute(
                "DELETE FROM stats_samples WHERE id NOT IN (SELECT id FROM stats_samples ORDER BY id DESC LIMIT ?)",
                (stats_limit,),
            )
            removed_stats = self._connection.total_changes - before_stats
        return {"messages": removed_messages, "packets": removed_packets, "stats": removed_stats}

    def list_stats_samples(self, limit: int = 120) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT created_at, data_json FROM stats_samples ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {"created_at": row["created_at"], **json.loads(row["data_json"])}
            for row in reversed(rows)
        ]

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            rows = self._connection.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}

    def export_data(self) -> dict[str, Any]:
        with self._lock:
            channel_rows = self._connection.execute(
                "SELECT id, name, enabled, secret, region_scope FROM channels ORDER BY id"
            ).fetchall()
        return {
            "version": 1,
            "exported_at": int(time.time()),
            "contacts": self.list_contacts(),
            "channels": [dict(row) for row in channel_rows],
            "messages": self._all_messages(),
            "settings": self.get_settings(),
        }

    def _all_messages(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM messages ORDER BY id").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result

    def import_data(self, data: dict[str, Any]) -> dict[str, int]:
        if int(data.get("version", 0)) != 1:
            raise ValueError("Неподдерживаемая версия резервной копии")
        counts = {"contacts": 0, "channels": 0, "messages": 0, "settings": 0}
        for contact in data.get("contacts", []):
            self.upsert_contact(contact)
            self.update_contact_local(
                str(contact["id"]), favorite=contact.get("favorite"),
                blocked=contact.get("blocked"), notes=contact.get("notes"),
            )
            counts["contacts"] += 1
        for channel in data.get("channels", []):
            self.upsert_channel(channel)
            counts["channels"] += 1
        for key, value in data.get("settings", {}).items():
            self.set_setting(str(key), value)
            counts["settings"] += 1
        with self._lock:
            existing_radio_ids = {
                row[0] for row in self._connection.execute(
                    "SELECT radio_id FROM messages WHERE radio_id IS NOT NULL"
                ).fetchall()
            }
            existing_messages = {
                tuple(row) for row in self._connection.execute(
                    "SELECT target_type, target_id, direction, text, created_at FROM messages"
                ).fetchall()
            }
        for message in data.get("messages", []):
            radio_id = message.get("radio_id")
            if radio_id and radio_id in existing_radio_ids:
                continue
            fingerprint = (
                message["target_type"], str(message["target_id"]), message["direction"],
                message["text"], int(message.get("created_at") or 0),
            )
            if fingerprint in existing_messages:
                continue
            self.add_message(
                target_type=message["target_type"], target_id=str(message["target_id"]),
                direction=message["direction"], text=message["text"],
                status=message.get("status", "received"), radio_id=radio_id,
                created_at=message.get("created_at"), metadata=message.get("metadata"),
            )
            existing_messages.add(fingerprint)
            counts["messages"] += 1
        return counts
