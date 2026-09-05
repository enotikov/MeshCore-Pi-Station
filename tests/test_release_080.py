import asyncio
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from meshcore_station.automatic_backup import AutomaticBackupManager, MAGIC
from meshcore_station.database import Database
from meshcore_station.diagnostics import system_diagnostics
from meshcore_station.main import create_app
from test_api import settings


def test_message_search_filters_and_escapes_wildcards(tmp_path):
    db = Database(tmp_path / "search.db")
    db.initialize()
    db.upsert_contact({"id": "node", "name": "Orel Node"})
    db.add_message(target_type="contact", target_id="node", direction="in", text="Alpha 100%", status="received", created_at=100)
    db.add_message(target_type="channel", target_id="0", direction="out", text="Bravo", status="unconfirmed", created_at=200)
    assert db.search_messages("Orel")[0]["target_name"] == "Orel Node"
    assert db.search_messages("100%")[0]["text"] == "Alpha 100%"
    assert [item["text"] for item in db.search_messages(status="unconfirmed")] == ["Bravo"]
    assert [item["text"] for item in db.search_messages(since=150, until=250)] == ["Bravo"]
    assert db.health_summary()["counts"]["messages"] == 2
    assert db.health_summary()["quick_check"] == "ok"
    db.close()


def test_link_quality_aggregates_observed_values_and_delivery(tmp_path):
    db = Database(tmp_path / "quality.db")
    db.initialize()
    db.upsert_contact({"id": "node", "name": "Field Node", "last_snr": 4.0,
                       "raw": {"out_path_len": 3}})
    created_at = int(time.time()) - 5
    message = db.add_message(target_type="contact", target_id="node", direction="out",
                             text="ping", status="queued", created_at=created_at)
    db.update_message(message["id"], "sending", attempted=True)
    db.update_message(message["id"], "delivered")
    db.add_packet_event("message", direction="in", contact_id="node",
                        data={"metadata": {"snr": 8.5, "rssi": -91, "path_len": 2}})
    db.add_packet_event("message", direction="out", contact_id="node", data={})
    quality = db.link_quality(7)
    node = quality["nodes"][0]
    assert node["name"] == "Field Node"
    assert node["average_snr"] == 8.5
    assert node["average_rssi"] == -91.0
    assert node["average_hops"] == 2.0
    assert (node["rx_packets"], node["tx_packets"]) == (1, 1)
    assert node["delivery_rate"] == 100.0
    assert node["average_delivery_seconds"] >= 5
    assert quality["summary"]["active_nodes"] == 1
    db.close()


def test_automatic_backup_is_encrypted_rotated_and_restorable(tmp_path):
    db = Database(tmp_path / "station.db")
    db.initialize()
    db.upsert_contact({"id": "private-id", "name": "Private Node"})
    manager = AutomaticBackupManager(db, tmp_path)
    manager.set_policy(True, 12, 2)
    created = [manager.create() for _ in range(3)]
    assert len(manager.list()) == 2
    payload = manager.path(created[-1]["name"]).read_bytes()
    assert payload.startswith(MAGIC)
    assert b"Private Node" not in payload
    assert manager.decode(created[-1]["name"])["contacts"][0]["name"] == "Private Node"
    assert manager.key_path.stat().st_size == 32
    with pytest.raises(ValueError):
        manager.path("../station.db")
    db.close()


def test_new_management_api_and_redacted_report(tmp_path):
    configured = replace(settings(tmp_path), web_password="long-password")
    app = create_app(configured)
    with TestClient(app) as client:
        client.auth = ("meshcore", "long-password")
        contact = client.get("/api/contacts").json()[0]
        secret_text = "PRIVATE MESSAGE DO NOT EXPORT"
        client.post("/api/messages", json={"target_type": "contact", "target_id": contact["id"], "text": secret_text})
        results = client.get("/api/messages/search", params={"q": "PRIVATE MESSAGE"}).json()
        assert results[0]["text"] == secret_text
        assert client.get("/api/messages/search", params={"since": 20, "until": 10}).status_code == 400
        assert client.post("/api/messages", json={"target_type": "contact", "target_id": contact["id"], "text": "x" * 134}).status_code == 422
        assert client.post("/api/messages", json={"target_type": "contact", "target_id": contact["id"], "text": "x" * 133}).status_code == 200
        quality = client.get("/api/analytics/links", params={"days": 7}).json()
        assert quality["nodes"][0]["delivery_rate"] == 100.0
        assert client.get("/api/analytics/links", params={"days": 0}).status_code == 422

        overview = client.get("/api/system/overview").json()
        assert overview["database"]["quick_check"] == "ok"
        assert "queue_pending" in overview
        report = client.get("/api/system/support-report")
        assert report.status_code == 200
        assert "attachment" in report.headers["content-disposition"]
        assert secret_text not in report.text
        assert "contacts" in report.json()["database"]["counts"]

        policy = client.put("/api/backup/automatic", json={"enabled": True, "interval_hours": 6, "retain_count": 3}).json()
        assert policy["enabled"] is True
        assert policy["interval_hours"] == 6
        created = client.post("/api/backup/automatic/run").json()
        assert created["name"].endswith(".mcpsa")
        status = client.get("/api/backup/automatic").json()
        assert status["backups"][0]["name"] == created["name"]
        assert client.get(f"/api/backup/automatic/{created['name']}").content.startswith(MAGIC)
        assert client.post(f"/api/backup/automatic/{created['name']}/restore").status_code == 200
        assert client.get("/api/backup/automatic/..%2Fstation.db").status_code in {404, 422}
        assert client.post("/api/system/reconnect").json()["connected"] is True


def test_automatic_backup_scheduler_runs_due_copy(tmp_path):
    async def scenario():
        db = Database(tmp_path / "scheduler.db")
        db.initialize()
        manager = AutomaticBackupManager(db, tmp_path)
        manager.set_policy(True, 1, 2)
        task = asyncio.create_task(manager._loop())
        for _ in range(100):
            if manager.list():
                break
            await asyncio.sleep(0.01)
        manager._stop.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(manager.list()) == 1
        db.close()
    asyncio.run(scenario())


def test_diagnostics_schema_is_stable(tmp_path):
    result = system_diagnostics(tmp_path, [])
    assert {"memory", "throttling", "warnings", "disk"} <= result.keys()
    assert set(result["memory"]) == {"total", "available", "used"}
