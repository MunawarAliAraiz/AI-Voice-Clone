"""Optional caption credentials never block pasted scripts or start missing-key imports."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routers import transcript
from app.config import Settings
from app.main import create_app


def client(tmp_path, monkeypatch, *, desktop=True, key=None, broken=False):
    (tmp_path / "index.html").write_text("<html>Offline test</html>", encoding="utf-8")
    starts = []

    def get_key():
        if broken:
            raise RuntimeError("private-key-do-not-return")
        return key

    async def start(*args, **kwargs):
        starts.append(args)
        return {"id": "a" * 32, "phase": "starting", "detail": "Fetching captions…",
                "created_at": 1, "provider": "apify" if key else "local", "cached": False,
                "maximum_import_usd": 0.03}

    service = SimpleNamespace(keys=SimpleNamespace(get=get_key), start=start)
    monkeypatch.setattr(transcript, "caption_service", lambda _: service)
    settings = Settings(data_dir=tmp_path,
                        desktop_static_dir=tmp_path if desktop else None)
    # No lifespan context: unrelated cloud/tool services must not run.
    return TestClient(create_app(settings=settings)), starts


@pytest.mark.parametrize("route", ["/youtube/imports", "/youtube"])
def test_desktop_missing_apify_blocks_before_any_fetch(tmp_path, monkeypatch, route):
    api, starts = client(tmp_path, monkeypatch)
    response = api.post("/api/transcript" + route, json={"url": "hoMYed4N4S4"})
    assert response.status_code == 409
    assert "Connect Apify in Settings" in response.text
    assert starts == []


def test_invalid_saved_credential_is_not_a_caption_connection(tmp_path, monkeypatch):
    api, starts = client(tmp_path, monkeypatch, broken=True)
    response = api.post("/api/transcript/youtube/imports", json={"url": "hoMYed4N4S4"})
    assert response.status_code == 503 and starts == []
    assert "private-key" not in response.text


@pytest.mark.parametrize("desktop,key", [(True, "synthetic-token"), (False, None)])
def test_connected_desktop_and_browser_can_import(tmp_path, monkeypatch, desktop, key):
    api, starts = client(tmp_path, monkeypatch, desktop=desktop, key=key)
    response = api.post("/api/transcript/youtube/imports", json={"url": "hoMYed4N4S4"})
    assert response.status_code == 202 and len(starts) == 1
    assert "synthetic-token" not in response.text


def test_pasted_script_needs_neither_optional_account(tmp_path, monkeypatch):
    api, starts = client(tmp_path, monkeypatch)
    response = api.post("/api/transcript/prepare", json={"text": "Aap kaise hain?"})
    assert response.status_code == 200 and response.json()["chunks"]
    assert starts == []


@pytest.mark.parametrize("payload", [{"api_key": {"private-key": "do-not-echo"}},
                                     {"api_key": "do-not-echo", "other": "private-key"},
                                     ["private-key"]])
def test_invalid_apify_payload_never_echoes_input(tmp_path, monkeypatch, payload):
    api, _ = client(tmp_path, monkeypatch)
    response = api.put("/api/transcript/apify", json=payload)
    assert response.status_code == 400
    assert "do-not-echo" not in response.text and "private-key" not in response.text


def test_registered_cloudflare_credentials_require_desktop_session(tmp_path):
    (tmp_path / "index.html").write_text("<html>Offline test</html>", encoding="utf-8")
    settings = Settings(data_dir=tmp_path, desktop_static_dir=tmp_path, api_key="session")
    api = TestClient(create_app(settings=settings))
    for method in ("GET", "PUT", "DELETE"):
        response = api.request(method, "/api/integrations/cloudflare",
                               json={"account_id": "private-account", "api_token": "private-token"})
        assert response.status_code == 401
        assert "private-account" not in response.text and "private-token" not in response.text
    response = api.get("/api/integrations/cloudflare", headers={"X-API-Key": "session"})
    assert response.status_code == 200
    assert response.json()["connected"] is False
    assert response.json()["protection_ready"] is False


def test_registered_account_analytics_requires_desktop_session(tmp_path, monkeypatch):
    from app.api.routers import runpod

    (tmp_path / "index.html").write_text("<html>Offline test</html>", encoding="utf-8")
    settings = Settings(data_dir=tmp_path, desktop_static_dir=tmp_path, api_key="session")
    keys = []
    closed = []

    def protected_key(_):
        keys.append(True)
        return "synthetic-private-key"

    class Client:
        def __init__(self, key):
            assert key == "synthetic-private-key"

        async def close(self):
            closed.append(True)

    async def summary(_, period):
        return {"period": period, "total_usd": 3.25}

    monkeypatch.setattr(runpod, "_key", protected_key)
    monkeypatch.setattr(runpod, "RunpodClient", Client)
    monkeypatch.setattr(runpod, "account_analytics", summary)
    api = TestClient(create_app(settings=settings))
    assert api.get("/api/runpod/analytics/account").status_code == 401
    assert keys == []
    response = api.get("/api/runpod/analytics/account?period=7d",
                       headers={"X-API-Key": "session"})
    assert response.status_code == 200 and response.json()["total_usd"] == 3.25
    assert keys == [True] and closed == [True]
    assert "synthetic-private-key" not in response.text
