from pathlib import Path

from meshcore_station.database import Database


def test_message_history_and_unread(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    contact = db.upsert_contact({"id": "abc", "name": "Test node", "public_key": "abc"})
    assert contact["name"] == "Test node"

    message = db.add_message(
        target_type="contact",
        target_id="abc",
        direction="in",
        text="Привет",
        status="received",
    )
    assert message["text"] == "Привет"
    assert db.list_contacts()[0]["unread"] == 1
    assert db.list_messages("contact", "abc")[0]["status"] == "received"

    db.mark_read("abc")
    assert db.list_contacts()[0]["unread"] == 0
    db.close()


def test_delivery_timeline_queue_and_retention(tmp_path: Path):
    db = Database(tmp_path / "queue.db", packet_limit=2, stats_limit=2)
    db.initialize()
    message = db.add_message(
        target_type="channel", target_id="0", direction="out", text="Queued", status="queued"
    )
    sending = db.update_message(message["id"], "sending", attempted=True)
    delivered = db.update_message(message["id"], "sent", radio_id="abc")
    assert sending["attempt_count"] == 1
    assert [event["status"] for event in delivered["timeline"]] == ["queued", "sending", "sent"]

    for index in range(4):
        db.add_packet_event("test", data={"index": index})
        db.add_stats_sample({"index": index})
    assert len(db.list_packet_events(10)) == 2
    assert len(db.list_stats_samples(10)) == 2
    assert db.prune_history(30, 100, 60) == {"messages": 0, "packets": 0, "stats": 0}
    db.close()


def test_interrupted_send_is_recovered_after_restart(tmp_path: Path):
    path = tmp_path / "recover.db"
    db = Database(path)
    db.initialize()
    message = db.add_message(
        target_type="channel", target_id="0", direction="out", text="Recover", status="queued"
    )
    db.update_message(message["id"], "sending", attempted=True)
    db.close()

    reopened = Database(path)
    reopened.initialize()
    recovered = reopened.get_message(message["id"])
    assert recovered["status"] == "queued"
    assert recovered["timeline"][-1]["detail"] == "Станция была перезапущена"
    reopened.close()
