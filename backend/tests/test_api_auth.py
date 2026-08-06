"""
CORS preflight must survive the API-key middleware.

This is the predecessor's exact bug: `ApiKeyMiddleware` added AFTER
`CORSMiddleware` becomes the outermost layer, so it answers every
cross-origin preflight with 403 the moment a key is set — and a browser
reports that as an opaque CORS failure, not as auth. `create_app` adds the
key middleware FIRST (innermost) and CORS LAST (outermost) to prevent it.
Nothing tested that ordering until now.

The ngrok deployment made this load-bearing: the `ngrok-skip-browser-warning`
header the frontend sends is non-simple, so every request is preflighted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeScheduler

ORIGIN = "https://studio.example.com"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path,
        allow_fake_runtime=True,
        api_key="test-secret-key",
        cors_origins=[ORIGIN],
    )
    return TestClient(create_app(scheduler=FakeScheduler(), settings=settings))


def test_preflight_succeeds_with_api_key_configured(client: TestClient) -> None:
    """The regression guard: OPTIONS is answered by CORS, never 403'd by auth."""
    with client as c:
        r = c.options(
            "/api/models",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-api-key,ngrok-skip-browser-warning",
            },
        )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGIN


def test_request_without_key_is_rejected(client: TestClient) -> None:
    with client as c:
        r = c.get("/api/models")
    assert r.status_code == 401
    assert r.json()["code"] == "AUTHENTICATION_ERROR"


def test_request_with_wrong_key_is_rejected(client: TestClient) -> None:
    with client as c:
        r = c.get("/api/models", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_request_with_correct_key_succeeds(client: TestClient) -> None:
    with client as c:
        r = c.get("/api/models", headers={"X-API-Key": "test-secret-key"})
    assert r.status_code == 200


def test_wildcard_cors_is_refused_behind_a_key(tmp_path: Path) -> None:
    """A key means someone else can reach this; `*` would then let any site
    drive it from a victim's browser. `config.py` refuses that at boot."""
    with pytest.raises(ValueError, match="not allowed together"):
        Settings(data_dir=tmp_path, api_key="secret", cors_origins=["*"])
