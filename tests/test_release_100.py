import time
from dataclasses import replace

from fastapi.testclient import TestClient

import meshcore_station.main as main_module
from meshcore_station.database import Database
from meshcore_station.main import create_app
from meshcore_station.updates import parse_release, version_tuple
from test_api import settings


def test_link_timeseries_and_route_history_are_bounded(tmp_path):
    database = Database(tmp_path / "analytics.db")
    database.initialize()
    database.upsert_contact({"id": "node-a", "name": "Node A"})
    now = int(time.time())
    database.add_packet_event(
        "message", direction="in", contact_id="node-a", created_at=now,
        data={"metadata": {"snr": 7.5, "rssi": -93, "path_len": 2, "route": "direct", "path": "a1,c3"}},
    )
    series = database.link_quality_series(1, "node-a")
    assert series["bucket_seconds"] == 3600
    assert series["series"][0]["average_snr"] == 7.5
    assert series["series"][0]["average_rssi"] == -93.0
    assert series["series"][0]["rx_packets"] == 1
    routes = database.route_history(7, "node-a", 10)
    assert routes["events"][0]["name"] == "Node A"
    assert routes["events"][0]["path"] == "a1,c3"
    assert routes["events"][0]["hops"] == 2
    database.close()


def test_release_parser_accepts_only_semver_and_safe_assets():
    payload = {
        "tag_name": "v1.1.0", "html_url": "https://github.com/enotikov/MeshCore-Pi-Station/releases/tag/v1.1.0",
        "published_at": "2026-09-06T00:00:00Z",
        "assets": [
            {"name": "station.deb", "browser_download_url": "https://github.com/enotikov/file.deb", "size": 12},
            {"name": "unsafe", "browser_download_url": "http://example.com/file", "size": 1},
        ],
    }
    result = parse_release(payload, "1.0.0")
    assert result["update_available"] is True
    assert [item["name"] for item in result["assets"]] == ["station.deb"]
    assert version_tuple("v1.0.0") == (1, 0, 0)


def test_alert_acknowledgement_analytics_and_update_api(monkeypatch, tmp_path):
    original_diagnostics = main_module.system_diagnostics

    def diagnostics(data_dir, serial_ports):
        result = original_diagnostics(data_dir, serial_ports)
        result["warnings"] = ["disk_space_low"]
        return result

    monkeypatch.setattr(main_module, "system_diagnostics", diagnostics)
    monkeypatch.setattr(main_module, "check_latest_release", lambda version: {
        "current_version": version, "latest_version": version, "update_available": False,
        "release_url": "https://github.com/enotikov/MeshCore-Pi-Station/releases/tag/v1.0.0",
        "assets": [], "checked_at": int(time.time()), "installation_mode": "manual-verified-package",
    })
    app = create_app(replace(settings(tmp_path), web_password="long-password"))
    with TestClient(app) as client:
        client.auth = ("meshcore", "long-password")
        overview = client.get("/api/system/overview").json()
        assert overview["alert_details"][0]["code"] == "disk_space_low"
        acknowledged = client.post("/api/system/alerts/disk_space_low/acknowledge").json()
        assert acknowledged["acknowledged_at"] > 0
        overview = client.get("/api/system/overview").json()
        assert overview["alert_details"][0]["acknowledged_at"] > 0
        assert client.delete("/api/system/alerts/acknowledgements").json() == {"ok": True}
        assert client.get("/api/analytics/links/timeseries", params={"days": 0}).status_code == 422
        assert client.get("/api/analytics/routes", params={"limit": 0}).status_code == 422
        update = client.get("/api/system/update-check").json()
        assert update["current_version"] == "1.0.0"
        assert update["update_available"] is False
