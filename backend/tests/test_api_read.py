"""
API read-surface tests: health, models, languages, system, warm, and the
API-key middleware — all against FakeScheduler, no GPU, no torch.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

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


def test_api_key_is_enforced_but_health_is_exempt(tmp_path: Path) -> None:
    with _client(tmp_path, api_key="secret") as c:
        unauth = c.get("/api/models")
        assert unauth.status_code == 401
        assert unauth.headers["content-type"].startswith("application/problem+json")
        assert unauth.json()["code"] == "AUTHENTICATION_ERROR"
        assert c.get("/api/models", headers={"X-API-Key": "secret"}).status_code == 200
        assert c.get("/api/health").status_code == 200  # exempt
