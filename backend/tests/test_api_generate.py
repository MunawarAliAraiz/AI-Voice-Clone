"""
The core flow: enroll a voice -> generate -> fetch the signed media URL, plus
script detection. Against FakeScheduler, so no torch and no GPU — but the real
routing runs, so the route chip and the post-transform text are asserted.
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


def _client(tmp_path: Path) -> tuple[TestClient, FakeScheduler]:
    sched = FakeScheduler()
    settings = Settings(data_dir=tmp_path, allow_fake_runtime=True)
    return TestClient(create_app(scheduler=sched, settings=settings)), sched


def _wav_bytes(dur: float = 1.5, sr: int = 16000) -> bytes:
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    buf = io.BytesIO()
    sf.write(buf, (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr, format="WAV")
    return buf.getvalue()


def _enroll(c: TestClient, language: str = "ur") -> int:
    r = c.post(
        "/api/voices",
        files={"file": ("ref.wav", _wav_bytes(), "audio/wav")},
        data={"name": "v", "language": language, "consent": "true"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_generate_and_media(tmp_path: Path) -> None:
    client, sched = _client(tmp_path)
    with client as c:
        pid = _enroll(c, "ur")
        r = c.post("/api/generate", json={
            "profile_id": pid, "text": "aap kaise hain", "language": "ur",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        # Roman Urdu -> VoxCPM2 directly, no transform (transliteration dropped).
        assert body["route"]["model_id"] == "voxcpm2"
        assert body["route"]["transform"] == "none"
        assert body["route"]["lossy"] is False
        assert body["route"]["source_script"] == "latin"
        assert body["audio_url"].startswith("/api/media/history/") and "?t=" in body["audio_url"]

        # The worker was handed the post-transform text (== input here, NONE).
        assert sched.requests[-1].text == "aap kaise hain"
        assert sched.requests[-1].model_id == "voxcpm2"

        # The signed media URL serves the (placeholder) audio.
        media = c.get(body["audio_url"])
        assert media.status_code == 200
        assert media.content == b"FAKE-NOT-AUDIO"

        # A tampered token is refused.
        bad = c.get(body["audio_url"].split("?t=")[0] + "?t=deadbeef.9999999999")
        assert bad.status_code == 403
        assert bad.json()["code"] == "INVALID_MEDIA_TOKEN"


def test_generate_unknown_profile_and_bad_params(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        assert c.post("/api/generate", json={
            "profile_id": 999, "text": "hi", "language": "en",
        }).status_code == 404

        pid = _enroll(c, "en")
        r = c.post("/api/generate", json={
            "profile_id": pid, "text": "hello", "language": "en",
            "params": {"not_a_real_knob": 1},
        })
        assert r.status_code == 422
        assert r.json()["code"] == "INVALID_PARAMS"


def test_history_records_the_generation(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        pid = _enroll(c, "ur")
        gen = c.post("/api/generate", json={
            "profile_id": pid, "text": "aap kaise hain", "language": "ur",
        }).json()

        lst = c.get("/api/history").json()
        assert lst["total"] == 1 and len(lst["items"]) == 1
        item = lst["items"][0]
        assert item["id"] == gen["id"]
        assert item["input_text"] == "aap kaise hain"
        assert item["profile_name"] == "v"
        assert item["route"]["model_id"] == "voxcpm2"  # route survives on the row
        assert item["audio_url"].startswith("/api/media/history/")

        one = c.get(f"/api/history/{gen['id']}")
        assert one.status_code == 200 and one.json()["id"] == gen["id"]
        assert c.get("/api/history/9999").status_code == 404


def test_detect_script(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        r = c.post("/api/detect-script", json={"text": "aap kaise hain", "language": "ur"})
        assert r.status_code == 200
        body = r.json()
        assert body["script"] == "latin"
        assert body["routable"] is True
        assert body["would_route_to"]["model_id"] == "voxcpm2"
        assert body["is_rtl"] is False  # Roman Urdu is Latin, not RTL
