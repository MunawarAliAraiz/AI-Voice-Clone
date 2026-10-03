"""The Pod boundary is authenticated and refuses model substitution."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.remote_worker.main import create_worker_app
from tests.fakes import FakeScheduler


def test_auth_and_synthesis(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, allow_fake_runtime=True)
    app = create_worker_app(scheduler=FakeScheduler(), settings=settings, token="worker-secret")
    with TestClient(app) as client:
        assert client.get("/v1/health").status_code == 401
        headers = {"Authorization": "Bearer worker-secret"}
        assert client.get("/v1/health", headers=headers).json()["protocol_version"] == 1
        response = client.post(
            "/v1/synthesize",
            headers=headers,
            data={"request": json.dumps({"model_id": "voxcpm2", "text": "Hello"})},
            files={"reference_audio": ("reference.wav", b"sample", "audio/wav")},
        )
        assert response.status_code == 200
        assert response.headers["x-model-id"] == "voxcpm2"
        assert len(response.headers["x-request-id"]) == 32
        assert response.content
        assert not list(tmp_path.glob("vcs-worker-*"))


def test_bad_request_identity_is_rejected_before_scheduling(tmp_path: Path) -> None:
    scheduler = FakeScheduler()
    app = create_worker_app(
        scheduler=scheduler, settings=Settings(data_dir=tmp_path), token="secret",
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/synthesize",
            headers={"Authorization": "Bearer secret", "X-Request-Id": "private-script"},
            data={"request": json.dumps({"model_id": "voxcpm2", "text": "Hello"})},
            files={"reference_audio": ("reference.wav", b"sample", "audio/wav")},
        )
        assert response.status_code == 422
        assert not scheduler.requests
        assert "private-script" not in response.text


def test_unknown_model_is_rejected_before_upload(tmp_path: Path) -> None:
    app = create_worker_app(scheduler=FakeScheduler(), settings=Settings(data_dir=tmp_path),
                            token="secret")
    with TestClient(app) as client:
        response = client.post(
            "/v1/synthesize",
            headers={"Authorization": "Bearer secret"},
            data={"request": json.dumps({"model_id": "invented", "text": "Hello"})},
            files={"reference_audio": ("reference.wav", b"sample", "audio/wav")},
        )
        assert response.status_code == 404
