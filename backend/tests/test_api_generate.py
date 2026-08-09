"""
The core flow: enroll a voice -> generate -> fetch the signed media URL, plus
script detection. Against FakeScheduler, so no torch and no GPU — but the real
routing runs, so the route chip and the post-transform text are asserted.

`POST /api/generate` is async now: it returns 202 with a job id, not a
completed generation. `_generate_and_poll` posts, then polls
`GET /api/jobs/{id}` to a terminal status — with a BOUNDED loop, never
`while True`, because `TestClient` blocks the calling thread per request and
an unbounded spin would hang CI on a real regression rather than fail loudly.
A validation failure (404/422) still returns directly from the POST, exactly
as before: routing and param validation run BEFORE any job row is created.
"""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeScheduler


def _client(tmp_path: Path, **settings_kwargs: Any) -> tuple[TestClient, FakeScheduler]:
    sched = FakeScheduler()
    settings = Settings(data_dir=tmp_path, allow_fake_runtime=True, **settings_kwargs)
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


def _generate_and_poll(
    c: TestClient, body: dict[str, Any], *, max_polls: int = 200
) -> httpx.Response:
    """
    POST /api/generate, then poll the job to a terminal status.

    A validation failure (ProfileNotFoundError 404, InvalidParamsError 422)
    happens before any job row exists, so the POST response itself is
    returned unchanged in that case — nothing to poll.
    """
    r = c.post("/api/generate", json=body)
    if r.status_code != 202:
        return r
    job_id = r.json()["id"]
    for _ in range(max_polls):
        poll = c.get(f"/api/jobs/{job_id}")
        assert poll.status_code == 200, poll.text
        if poll.json()["status"] in ("succeeded", "failed", "cancelled"):
            return poll
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach a terminal status within {max_polls} polls")


def test_generate_and_media(tmp_path: Path) -> None:
    client, sched = _client(tmp_path)
    with client as c:
        pid = _enroll(c, "ur")
        r = _generate_and_poll(c, {
            "profile_id": pid, "text": "aap kaise hain", "language": "ur",
        })
        assert r.status_code == 200, r.text
        job = r.json()
        assert job["status"] == "succeeded"
        # Roman Urdu -> VoxCPM2 directly, no transform (transliteration dropped).
        # `route` is on the job itself, not just the nested result — it was
        # known and rendered from the moment the job was still 'queued'.
        assert job["route"]["model_id"] == "voxcpm2"
        assert job["route"]["transform"] == "none"
        assert job["route"]["lossy"] is False
        assert job["route"]["source_script"] == "latin"
        result = job["result"]
        assert result["route"]["model_id"] == "voxcpm2"
        assert result["audio_url"].startswith("/api/media/history/")
        assert "?t=" in result["audio_url"]

        # The worker was handed the post-transform text (== input here, NONE).
        assert sched.requests[-1].text == "aap kaise hain"
        assert sched.requests[-1].model_id == "voxcpm2"

        # The signed media URL serves the (placeholder) audio.
        media = c.get(result["audio_url"])
        assert media.status_code == 200
        assert media.content == b"FAKE-NOT-AUDIO"

        # Playback must be `inline`. Mobile browsers honour `attachment` on a
        # media element and refuse to play — the phone symptom was a dead play
        # button next to a working download button.
        assert media.headers["content-disposition"].startswith("inline;")
        assert media.headers["content-type"] == "audio/wav"

        # `?download=1` is the explicit opt-in the download button uses, because
        # the HTML `download` attribute is ignored cross-origin.
        dl = c.get(result["audio_url"] + "&download=1")
        assert dl.status_code == 200
        assert dl.headers["content-disposition"].startswith("attachment;")

        # A tampered token is refused.
        bad = c.get(result["audio_url"].split("?t=")[0] + "?t=deadbeef.9999999999")
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

        # Routing and param validation run BEFORE a job row is created — a
        # rejected request must never leave a queued job behind.
        assert c.get("/api/jobs").json()["total"] == 0


def test_history_records_the_generation(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        pid = _enroll(c, "ur")
        gen = _generate_and_poll(c, {
            "profile_id": pid, "text": "aap kaise hain", "language": "ur",
        }).json()
        assert gen["status"] == "succeeded"
        history_id = gen["result"]["id"]

        lst = c.get("/api/history").json()
        assert lst["total"] == 1 and len(lst["items"]) == 1
        item = lst["items"][0]
        assert item["id"] == history_id
        assert item["input_text"] == "aap kaise hain"
        assert item["profile_name"] == "v"
        assert item["route"]["model_id"] == "voxcpm2"  # route survives on the row
        assert item["audio_url"].startswith("/api/media/history/")

        one = c.get(f"/api/history/{history_id}")
        assert one.status_code == 200 and one.json()["id"] == history_id
        assert c.get("/api/history/9999").status_code == 404

        # favorite toggle
        fav = c.patch(f"/api/history/{history_id}", json={"is_favorite": True})
        assert fav.status_code == 200 and fav.json()["is_favorite"] is True
        assert c.get(f"/api/history/{history_id}").json()["is_favorite"] is True

        # delete: 204, gone from list, 404 thereafter
        assert c.delete(f"/api/history/{history_id}").status_code == 204
        assert c.get("/api/history").json()["total"] == 0
        assert c.get(f"/api/history/{history_id}").status_code == 404
        assert c.delete("/api/history/9999").status_code == 404


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
