"""Check browser JSON request parsing without starting paid model setup."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import install_exception_handlers
from app.api.routers import runpod
from app.config import Settings


@pytest.mark.parametrize("enabled", [True, False])
def test_automatic_setup_requires_json_transport(tmp_path, monkeypatch, enabled):
    calls = []

    async def cloud(settings, method, *args):
        calls.append((method, args))
        return {"auto_setup_enabled": args[0]}

    monkeypatch.setattr(runpod, "_cloud", cloud)
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(runpod.router, prefix="/api")
    app.dependency_overrides[runpod.get_settings] = lambda: Settings(data_dir=tmp_path)
    payload = json.dumps({"enabled": enabled})
    with TestClient(app) as client:
        # Fetch defaults string bodies to text/plain when Content-Type is absent.
        rejected = client.put(
            "/api/runpod/setup/auto", content=payload,
            headers={"Content-Type": "text/plain;charset=UTF-8"},
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"] == "The request body failed validation."
        assert calls == []
        accepted = client.put(
            "/api/runpod/setup/auto", content=payload,
            headers={"Content-Type": "application/json"},
        )
        assert accepted.status_code == 200
        assert accepted.json() == {"auto_setup_enabled": enabled}
        assert calls == [("set_auto_setup", (enabled,))]
