import json

from meshcore_station.config import Settings


def test_wizard_config_overrides_packaged_defaults(monkeypatch, tmp_path):
    (tmp_path / "station.json").write_text(
        json.dumps({
            "transport": "serial", "serial_port": "/dev/ttyACM0",
            "web_username": "operator", "web_password": "secret-from-wizard",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("MESHCORE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MESHCORE_TRANSPORT", "mock")
    monkeypatch.setenv("MESHCORE_WEB_PASSWORD", "packaged-default")
    settings = Settings.from_env()
    assert settings.transport == "serial"
    assert settings.serial_port == "/dev/ttyACM0"
    assert settings.web_username == "operator"
    assert settings.web_password == "secret-from-wizard"
