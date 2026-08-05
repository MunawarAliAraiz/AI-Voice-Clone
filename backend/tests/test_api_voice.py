"""
Voice enrollment tests: upload + consent + validation + CRUD, over TestClient.

The db is NOT injected — the app opens a real (temp-file) SQLite in the test's
event loop, avoiding aiosqlite cross-loop issues. The scheduler is faked.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeScheduler


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path, allow_fake_runtime=True)
    return TestClient(create_app(scheduler=FakeScheduler(), settings=settings))


def _wav_bytes(dur: float = 1.5, sr: int = 16000) -> bytes:
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    x = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, x, sr, format="WAV")
    return buf.getvalue()


def _upload(c: TestClient, **form: object):
    return c.post(
        "/api/voices",
        files={"file": ("ref.wav", _wav_bytes(), "audio/wav")},
        data={"name": "My voice", "language": "ur", "consent": "true", **form},
    )


def test_enroll_validates_and_measures(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = _upload(c)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["id"] and body["name"] == "My voice" and body["language"] == "ur"
        assert 1.0 < body["duration_sec"] < 2.0
        assert body["sample_rate"] == 16000
        assert body["peak_dbfs"] is not None and body["is_clipped"] is False
        assert body["audio_url"].startswith("/api/media/voice/") and "?t=" in body["audio_url"]


def test_enroll_requires_consent(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = _upload(c, consent="false")
        assert r.status_code == 400
        assert r.json()["code"] == "VALIDATION_ERROR"


def test_enroll_rejects_non_audio(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        r = c.post(
            "/api/voices",
            files={"file": ("ref.wav", b"this is not audio", "audio/wav")},
            data={"name": "x", "language": "en", "consent": "true"},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "AUDIO_VALIDATION_ERROR"


def test_list_get_delete(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        pid = _upload(c).json()["id"]
        assert c.get("/api/voices").json()["total"] == 1
        assert c.get(f"/api/voices/{pid}").status_code == 200
        assert c.get("/api/voices/9999").status_code == 404
        assert c.delete(f"/api/voices/{pid}").status_code == 204
        assert c.get(f"/api/voices/{pid}").status_code == 404
        assert c.get("/api/voices").json()["total"] == 0
