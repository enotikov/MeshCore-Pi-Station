import hashlib
import io
import json

import pytest

from meshcore_station import firmware_catalog


def test_builtin_catalog_contains_verified_usb_companion_images():
    entries = firmware_catalog.load_catalog("builtin")
    assert {entry["mode"] for entry in entries} == {"update", "full"}
    assert all(entry["board"] == "heltec-v4" for entry in entries)
    assert all("companion_radio_usb" in entry["url"] for entry in entries)


def test_catalog_filters_board_and_verifies_download(monkeypatch, tmp_path):
    image = b"\xe9" + b"x" * 4095
    digest = hashlib.sha256(image).hexdigest()
    manifest = json.dumps({
        "firmware": [
            {
                "id": "v4", "name": "Heltec V4", "version": "1.0", "board": "heltec-v4",
                "mode": "update", "url": "https://downloads.example/v4.bin", "sha256": digest,
            },
            {
                "id": "other", "board": "other-board", "mode": "update",
                "url": "https://downloads.example/other.bin", "sha256": digest,
            },
        ]
    }).encode()

    def fake_open(request, timeout):
        return io.BytesIO(image if request.full_url.endswith(".bin") else manifest)

    monkeypatch.setattr(firmware_catalog.urllib.request, "urlopen", fake_open)
    entries = firmware_catalog.load_catalog("https://downloads.example/catalog.json")
    assert [entry["id"] for entry in entries] == ["v4"]
    destination = tmp_path / "firmware.bin"
    firmware_catalog.download_verified(entries[0], destination)
    assert destination.read_bytes() == image


def test_catalog_rejects_insecure_url_and_bad_digest(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        firmware_catalog.load_catalog("http://downloads.example/catalog.json")

    image = b"\xe9" + b"x" * 4095
    monkeypatch.setattr(
        firmware_catalog.urllib.request, "urlopen", lambda request, timeout: io.BytesIO(image)
    )
    with pytest.raises(ValueError, match="SHA-256"):
        firmware_catalog.download_verified(
            {"url": "https://downloads.example/v4.bin", "sha256": "0" * 64},
            tmp_path / "bad.bin",
        )
