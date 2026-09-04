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

