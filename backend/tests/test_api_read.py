"""
API read-surface tests: health, models, languages, system, warm, and the
API-key middleware — all against FakeScheduler, no GPU, no torch.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeScheduler


def _client(tmp_path: Path, **settings_kw: object) -> TestClient:
    settings = Settings(data_dir=tmp_path, allow_fake_runtime=True, **settings_kw)
    return TestClient(create_app(scheduler=FakeScheduler(), settings=settings))


def test_health_is_ok_and_open(tmp_path: Path) -> None:
    with _client(tmp_path, api_key="secret") as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"  # reachable even with a key configured


def test_models_list_reports_verified_latin_cells(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.get("/api/models")
        assert r.status_code == 200
        body = r.json()
        vox = next(m for m in body["models"] if m["id"] == "voxcpm2")
        pairs = {(x["language"], x["script"]) for x in vox["languages"]}
        assert ("ur", "latin") in pairs and ("hi", "latin") in pairs
        assert all(x["verified"] for x in vox["languages"])  # only verified returned
        assert body["vram_budget_mb"] > 0


def test_models_list_surfaces_urdu_lora_as_experimental(tmp_path: Path) -> None:
    """
    voxcpm2_urdu_lora is `verified=False` but `experimental_listing=True` —
    the picker must still list it (mirroring Chatterbox) rather than hiding an
    unverified spec entirely, with its Urdu cell honestly marked unverified.
    """
    with _client(tmp_path) as c:
        body = c.get("/api/models").json()
        lora = next(m for m in body["models"] if m["id"] == "voxcpm2_urdu_lora")
        assert lora["experimental"] is True
        ur = next(x for x in lora["languages"] if x["language"] == "ur")
        assert ur["script"] == "arabic"
        assert ur["verified"] is False, "gate-failing cell must not read as verified"


def test_languages_lists_en_hi_ur(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.get("/api/languages")
        assert r.status_code == 200
        codes = {x["code"] for x in r.json()["languages"]}
        assert {"en", "hi", "ur"} <= codes


def test_system_status(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.get("/api/system")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["gpu"]["available"], bool)  # False on CI, fine
        assert body["fake_runtime_enabled"] is True


def test_warm_known_and_unknown_model(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        assert c.post("/api/models/warm", json={"model_id": "voxcpm2"}).status_code == 202
        r = c.post("/api/models/warm", json={"model_id": "does_not_exist"})
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/problem+json")
        assert r.json()["code"] == "MODEL_NOT_FOUND"


async def test_warm_on_startup_loads_before_first_request(tmp_path: Path) -> None:
    """The point of warm_on_startup: the lifespan kicks off the load rather than
    leaving the first real /generate to pay the cold-load cost.

    TestClient runs the lifespan on a separate anyio portal thread with its own
    event loop, so `app.state.warm_task` can't be awaited directly from here —
    that's a cross-loop await. Poll the fake's shared state instead.
    """
    scheduler = FakeScheduler()
    settings = Settings(data_dir=tmp_path, allow_fake_runtime=True, warm_on_startup="voxcpm2")
    with TestClient(create_app(scheduler=scheduler, settings=settings)):
        for _ in range(100):
            if "voxcpm2" in scheduler.warmed:
                break
            await asyncio.sleep(0.01)
        assert "voxcpm2" in scheduler.warmed


def test_no_warm_on_startup_by_default(tmp_path: Path) -> None:
    scheduler = FakeScheduler()
    with TestClient(create_app(scheduler=scheduler, settings=Settings(data_dir=tmp_path))):
        assert scheduler.warmed == []


def test_api_key_is_enforced_but_health_is_exempt(tmp_path: Path) -> None:
    with _client(tmp_path, api_key="secret") as c:
        unauth = c.get("/api/models")
        assert unauth.status_code == 401
        assert unauth.headers["content-type"].startswith("application/problem+json")
        assert unauth.json()["code"] == "AUTHENTICATION_ERROR"
        assert c.get("/api/models", headers={"X-API-Key": "secret"}).status_code == 200
        assert c.get("/api/health").status_code == 200  # exempt


def test_wildcard_cors_is_refused_when_an_api_key_is_set(tmp_path: Path) -> None:
    """
    A wildcard origin behind an API key means any site can drive this API from a
    victim's browser. The README promised this guard long before it existed.
    """
    with pytest.raises(ValidationError):
        Settings(data_dir=tmp_path, api_key="secret", cors_origins=["*"])

    # Without a key the deployment is already open by choice — allowed.
    s = Settings(data_dir=tmp_path, api_key="", cors_origins=["*"])
    assert s.cors_origins == ["*"]

    # A named origin behind a key is the supported production shape.
    s2 = Settings(
        data_dir=tmp_path, api_key="secret", cors_origins=["https://studio.example.com"]
    )
    assert s2.cors_origins == ["https://studio.example.com"]
