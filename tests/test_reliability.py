"""Regression tests for radio recovery, ACK handling, contact merging and housekeeping."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from meshcore_station.automatic_backup import AutomaticBackupManager
from meshcore_station.database import Database
from meshcore_station.service import StationService
from meshcore_station.transports.base import TransportEvent
from meshcore_station.transports.meshcore_serial import MeshCoreSerialTransport
from meshcore_station.transports.mock import MockTransport

ROOT = Path(__file__).resolve().parents[1]
FULL_KEY = "a1b2c3d4e5f6" + "0" * 52


async def eventually(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition was not reached")
        await asyncio.sleep(0.01)


class CountingTransport(MockTransport):
    def __init__(self):
        super().__init__(seed=False)
        self.starts = 0

    async def start(self, handler):
        self.starts += 1
        await super().start(handler)


async def test_radio_reconnects_after_unexpected_disconnect(tmp_path):
    transport = CountingTransport()
    station = StationService(Database(tmp_path / "station.db"), transport, reconnect_interval=0.01)
    await station.start()
    try:
        assert transport.starts == 1
        transport._connected = False
        await station._on_transport_event(TransportEvent("status", transport.status))
        await eventually(lambda: transport.starts == 2 and station.status["connected"])
        assert station.status["connection_state"] == "connected"
    finally:
        await station.stop()


async def test_disconnect_during_maintenance_does_not_reconnect(tmp_path):
    transport = CountingTransport()
    station = StationService(Database(tmp_path / "station.db"), transport, reconnect_interval=0.01)
    await station.start()
    try:
        await station.pause_radio("firmware")
        await station._on_transport_event(TransportEvent("status", transport.status))
        await asyncio.sleep(0.1)
        assert transport.starts == 1
    finally:
        await station.stop()


async def test_queue_loop_survives_unexpected_errors(tmp_path):
    station = StationService(Database(tmp_path / "station.db"), MockTransport(seed=False),
                             message_retry_seconds=0.01)
    calls = []
    original = station._process_queue

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        await original()

    station._process_queue = flaky
    await station.start()
    try:
        await eventually(lambda: len(calls) >= 3)
        assert not station._queue_task.done()
    finally:
        await station.stop()


class AckTransport(MockTransport):
    def __init__(self):
        super().__init__(seed=False)
        self.acks: dict[str, asyncio.Future] = {}

    async def send_message(self, target_type, target_id, text):
        radio_id = f"ack-{len(self.acks)}"
        self.acks[radio_id] = asyncio.get_running_loop().create_future()
        return {"radio_id": radio_id, "status": "sent", "ack_timeout": 5.0}

    async def wait_for_ack(self, radio_id, timeout):
        try:
            return await asyncio.wait_for(self.acks[radio_id], timeout)
        except TimeoutError:
            return False


async def test_ack_wait_does_not_block_following_messages(tmp_path):
    transport = AckTransport()
    station = StationService(Database(tmp_path / "station.db"), transport)
    await station.start()
    try:
        first = await asyncio.wait_for(station.send_message("contact", FULL_KEY, "one"), 1)
        second = await asyncio.wait_for(station.send_message("contact", FULL_KEY, "two"), 1)
        assert first["status"] == second["status"] == "sent"
        transport.acks[first["radio_id"]].set_result(True)
        transport.acks[second["radio_id"]].set_result(False)
        await eventually(lambda: station.database.get_message(second["id"])["status"] == "unconfirmed")
        assert station.database.get_message(first["id"])["status"] == "delivered"
    finally:
        await station.stop()


async def test_stopping_marks_pending_ack_unconfirmed(tmp_path):
    transport = AckTransport()
    database = Database(tmp_path / "station.db")
    station = StationService(database, transport)
    await station.start()
    message = await station.send_message("contact", FULL_KEY, "pending")
    await station.stop()
    reopened = Database(tmp_path / "station.db")
    reopened.initialize()
    assert reopened.get_message(message["id"])["status"] == "unconfirmed"
    reopened.close()


async def test_serial_transport_accepts_ack_before_waiter():
    transport = MeshCoreSerialTransport("/dev/null")
    await transport._on_ack(SimpleNamespace(payload={"code": "0badc0de"}))
    assert await transport.wait_for_ack("0badc0de", 0.01) is True
    waiter = asyncio.create_task(transport.wait_for_ack("feedface", 1))
    await asyncio.sleep(0)
    await transport._on_ack(SimpleNamespace(payload={"code": "feedface"}))
    assert await waiter is True
    assert await transport.wait_for_ack("00000000", 0.01) is False


def test_prefix_stub_contact_is_merged_into_full_key(tmp_path):
    database = Database(tmp_path / "station.db")
    database.initialize()
    database.upsert_contact({"id": FULL_KEY[:12], "name": "Unknown"})
    database.update_contact_local(FULL_KEY[:12], notes="met at the field day")
    database.add_message(target_type="contact", target_id=FULL_KEY[:12], direction="in",
                         text="hello", status="received")
    database.add_packet_event("message", direction="in", contact_id=FULL_KEY[:12])
    database.upsert_contact({"id": FULL_KEY, "name": "Field node"})
    assert database.get_contact(FULL_KEY[:12]) is None
    merged = database.get_contact(FULL_KEY)
    assert merged["unread"] == 1 and merged["notes"] == "met at the field day"
    assert [m["text"] for m in database.list_messages("contact", FULL_KEY)] == ["hello"]
    assert database.list_packet_events()[0]["contact_id"] == FULL_KEY
    assert database.find_contact_by_prefix(FULL_KEY[:12])["id"] == FULL_KEY
    database.close()


def test_non_hex_ids_are_never_merged(tmp_path):
    database = Database(tmp_path / "station.db")
    database.initialize()
    database.upsert_contact({"id": "node-1", "name": "One"})
    database.upsert_contact({"id": "node-10", "name": "Ten"})
    assert database.get_contact("node-1") and database.get_contact("node-10")
    assert database.find_contact_by_prefix("node-") is None
    database.close()


async def test_prefix_message_uses_known_full_contact(tmp_path):
    station = StationService(Database(tmp_path / "station.db"), MockTransport(seed=False))
    await station.start()
    try:
        station.database.upsert_contact({"id": FULL_KEY, "name": "Field node"})
        await station._on_transport_event(TransportEvent("message", {
            "target_type": "contact", "target_id": FULL_KEY[:12], "text": "hi",
        }))
        assert station.database.get_contact(FULL_KEY[:12]) is None
        assert station.database.list_messages("contact", FULL_KEY)[0]["text"] == "hi"
    finally:
        await station.stop()


def test_automatic_backups_order_by_creation_not_local_time(tmp_path):
    database = Database(tmp_path / "station.db")
    database.initialize()
    manager = AutomaticBackupManager(database, tmp_path)
    manager._ensure_directory()
    # After clocks go back the newer backup has an earlier local-time prefix.
    older = manager.directory / f"station-20261025-023000-{1_000:020d}-000001-aaaaaaaaaaaa.mcpsa"
    newer = manager.directory / f"station-20261025-021500-{2_000:020d}-000002-bbbbbbbbbbbb.mcpsa"
    older.write_bytes(b"x")
    newer.write_bytes(b"x")
    assert [item["name"] for item in manager.list()] == [newer.name, older.name]
    manager._prune(1)
    assert newer.exists() and not older.exists()
    database.close()


def test_importing_main_has_no_side_effects(tmp_path):
    subprocess.run([sys.executable, "-c", "import meshcore_station.main"], cwd=tmp_path, check=True,
                   env=dict(os.environ, PYTHONPATH=str(ROOT / "src"), MESHCORE_DATA_DIR="data"))
    assert not (tmp_path / "data").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux installer integration test")
def test_postinst_prunes_only_stale_release_environments(tmp_path):
    from test_installer import executable

    base, app, data, mock = (tmp_path / name for name in ("opt", "app", "data", "commands"))
    for directory in (base, app, data, mock):
        directory.mkdir()
    previous = base / ".venv.release.previous"
    stale = base / ".venv.release.stale"
    for release in (previous, stale):
        (release / "bin").mkdir(parents=True)
    (base / ".venv").symlink_to(previous, target_is_directory=True)
    (base / "user-files").mkdir()
    for name in ("id", "sleep", "install", "systemctl"):
        executable(mock / name, "#!/bin/sh\nexit 0\n")
    executable(mock / "getent", "#!/bin/sh\nexit 1\n")
    executable(mock / "python3", f'''#!{sys.executable}
import os, pathlib, sys
if sys.argv[1:3] == ['-m', 'venv']:
    (pathlib.Path(sys.argv[3]) / 'bin').mkdir(parents=True)
    python = pathlib.Path(sys.argv[3]) / 'bin/python'
    python.write_text('#!/bin/sh\\nexit 0\\n')
    python.chmod(0o755)
else:
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
''')
    script = (ROOT / "deploy/postinst.sh").read_text()
    for source, target in (("/usr/lib/meshcore-pi-station", app), ("/opt/meshcore-pi-station", base),
                           ("/var/lib/meshcore-pi-station", data)):
        script = script.replace(source, str(target))
    (tmp_path / "postinst").write_text(script)
    subprocess.run(["dash", str(tmp_path / "postinst")], check=True,
                   env=dict(os.environ, PATH=str(mock) + os.pathsep + os.environ["PATH"]))
    active = (base / ".venv").resolve()
    assert active.name.startswith(".venv.release.") and active != previous
    assert previous.exists() and not stale.exists()
    assert (base / "user-files").exists()
