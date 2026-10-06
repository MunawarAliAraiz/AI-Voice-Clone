"""Registered app routes retain desktop authentication and private errors."""
from fastapi.testclient import TestClient

from app.api.routers import storage_access
from app.config import Settings
from app.main import create_app
from app.runpod.storage_access import StorageBinding


def test_registered_storage_routes_require_desktop_session_key(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>Offline test</html>", encoding="utf-8")
    settings = Settings(data_dir=tmp_path, desktop_static_dir=tmp_path, api_key="session")
    monkeypatch.setattr(storage_access, "_binding", lambda _: StorageBinding(
        account="a" * 64, volume_id="volume123", region="US-NE-1"))
    # No context manager: do not start unrelated app services or tool downloads.
    client = TestClient(create_app(settings=settings))
    assert client.get("/api/runpod/storage-access").status_code == 401
    for path in ("/api/runpod/storage-access", "/api/runpod/storage-access/check"):
        response = client.request("PUT" if path.endswith("access") else "POST", path,
                                  json={"access_key": "private", "secret_key": "private"})
        assert response.status_code == 401
        assert "private" not in response.text
    response = client.get("/api/runpod/storage-access", headers={"X-API-Key": "session"})
    assert response.status_code == 200 and response.json()["connected"] is False
    assert response.json()["download_supported"] is False


def test_registered_storage_route_never_echoes_invalid_secret(tmp_path):
    (tmp_path / "index.html").write_text("<html>Offline test</html>", encoding="utf-8")
    settings = Settings(data_dir=tmp_path, desktop_static_dir=tmp_path, api_key="session")
    client = TestClient(create_app(settings=settings))
    response = client.put("/api/runpod/storage-access", headers={"X-API-Key": "session"},
                          json={"access_key": {"private": "do-not-echo"},
                                "secret_key": "also-do-not-echo"})
    assert response.status_code == 400
    assert "do-not-echo" not in response.text
