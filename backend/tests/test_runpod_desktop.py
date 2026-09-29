"""Runpod credentials, provider contract, and desktop-only analytics."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.runpod.client import RunpodClient
from app.runpod.secrets import RunpodKeyStore
from tests.fakes import FakeScheduler


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_runpod_key_is_encrypted_and_user_bound(tmp_path: Path) -> None:
    store = RunpodKeyStore(tmp_path)
    store.set_key("a-test-runpod-secret")
    assert store.has_key()
    assert b"a-test-runpod-secret" not in store.path.read_bytes()
    assert store.get_key() == "a-test-runpod-secret"
    store.clear()
    assert store.get_key() is None


def test_runpod_client_uses_v2_paths_without_exposing_token() -> None:
    seen: list[tuple[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer hidden-token"
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/catalog/gpus"):
            return httpx.Response(200, json={"gpus": [{"id": "gpu-48", "memory": 48}]})
        if request.url.path.endswith("/network-volumes"):
            return httpx.Response(200, json={"networkVolumes": []})
        return httpx.Response(200, json={"pods": []})

    async def run() -> None:
        client = RunpodClient("hidden-token", transport=httpx.MockTransport(respond))
        try:
            assert len(await client.list_gpu_types()) == 1
            assert await client.list_pods() == []
            assert await client.list_volumes() == []
        finally:
            await client.close()

    asyncio.run(run())
    assert seen == [("GET", "/v2/catalog/gpus"), ("GET", "/v2/pods"),
                    ("GET", "/v2/network-volumes")]


def test_runpod_analytics_requires_desktop_and_session_key(tmp_path: Path) -> None:
    web = Settings(data_dir=tmp_path / "web", api_key="session", allow_fake_runtime=True)
    with TestClient(create_app(scheduler=FakeScheduler(), settings=web)) as client:
        assert client.get("/api/runpod/connection").status_code == 401
        response = client.get("/api/runpod/connection", headers={"X-API-Key": "session"})
        assert response.status_code == 404


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_runpod_desktop_connection_never_returns_secret(tmp_path: Path, monkeypatch) -> None:
    class FakeRunpod:
        def __init__(self, key: str) -> None:
            assert key == "runpod-test-token"

        async def list_gpu_types(self) -> list[dict[str, object]]:
            return [{"id": "test-gpu", "memory": 48,
                     "price": {"secure": 1.0}, "availability": "HIGH"}]

        async def list_pods(self) -> list[dict[str, object]]:
            return [{"id": "pod1", "name": "Studio", "status": "RUNNING",
                     "cost": 1.0, "env": {"POD_WORKER_TOKEN": "must-not-leak"}}]

        async def list_volumes(self) -> list[dict[str, object]]:
            return [{"id": "vol1", "name": "Models", "size": 200}]

        async def close(self) -> None:
            pass

    monkeypatch.setattr("app.api.routers.runpod.RunpodClient", FakeRunpod)
    assets = tmp_path / "dist"
    assets.mkdir()
    (assets / "index.html").write_text("<html><head></head></html>")
    settings = Settings(data_dir=tmp_path / "data", desktop_static_dir=assets,
                        api_key="session", allow_fake_runtime=True)
    with TestClient(create_app(scheduler=FakeScheduler(), settings=settings)) as client:
        headers = {"X-API-Key": "session"}
        assert client.put("/api/runpod/connection", headers=headers,
                          json={"api_key": "runpod-test-token"}).json() == {"connected": True}
        analytics = client.get("/api/runpod/analytics", headers=headers)
        assert analytics.status_code == 200
        assert "must-not-leak" not in analytics.text
        estimates = client.get("/api/runpod/estimate", headers=headers,
                               params={"model_id": "voxcpm2", "text": "Hello world"})
        assert estimates.status_code == 200
        assert estimates.json()["estimates"][0]["gpu_id"] == "test-gpu"
