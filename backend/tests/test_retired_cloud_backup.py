"""旧云备份入口必须在读取配置、联网或写入活动库之前返回 410。"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app.api import settings_api
from backend.app.config import settings
from main import app


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/settings/backup/create", None),
        ("GET", "/api/settings/backup/list", None),
        ("POST", "/api/settings/backup/restore", {"backup_id": "old", "confirmed": False}),
        ("DELETE", "/api/settings/backup/old", None),
    ],
)
def test_cloud_backup_routes_are_gone_before_side_effects(tmp_path, monkeypatch, method, path, body):
    library = tmp_path / "library"
    metadata = library / ".teamhub"
    metadata.mkdir(parents=True)
    database_file = metadata / "db.sqlite"
    database_file.write_bytes(b"untouched active database")
    config_file = metadata / "config.json"
    config_file.write_text('{"sentinel": true}', encoding="utf-8")
    monkeypatch.setattr(settings, "library_path", str(library))
    calls = []

    def read_config():
        calls.append("config")
        return {"cloud_access_token": "test-only"}

    def call_cloud(*args, **kwargs):
        calls.append("network")
        return {"backup_id": "old", "upload_url": "https://unused.invalid"}

    def upload(*args, **kwargs):
        calls.append("upload")
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(settings_api, "load_library_config", read_config)
    # These compatibility implementations disappear once the routes are retired.
    monkeypatch.setattr(settings_api, "_call_cloud", call_cloud, raising=False)
    if hasattr(settings_api, "_http"):
        monkeypatch.setattr(settings_api._http, "put", upload)

    response = TestClient(app).request(method, path, json=body)

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "GONE_DECOMMISSIONED"
    assert calls == []
    assert database_file.read_bytes() == b"untouched active database"
    assert config_file.read_text(encoding="utf-8") == '{"sentinel": true}'


def test_confirmed_cloud_restore_cannot_start_download(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "library_path", str(tmp_path))
    calls = []

    def read_config():
        calls.append("config")
        return {}

    monkeypatch.setattr(settings_api, "load_library_config", read_config)
    response = TestClient(app).post(
        "/api/settings/backup/restore", json={"backup_id": "old", "confirmed": True}
    )
    assert response.status_code == 410
    assert calls == []
