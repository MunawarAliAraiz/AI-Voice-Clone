"""
`POST /api/generate` with `apply_direction: true` — the HTTP surface for
multi-segment ("directed") generation. Against a WAV-writing `FakeScheduler`
subclass (the base one writes `b"FAKE-NOT-AUDIO"` placeholder bytes, which
`concat_wavs_with_pauses` correctly refuses to read as audio).

`apply_direction` defaults to false; `test_api_generate.py`'s existing tests
cover that unchanged single-shot path and are not touched here.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from app.config import Settings
from app.inference.protocol import SynthResult
from app.main import create_app
from tests.fakes import FakeScheduler


class _WavWritingScheduler(FakeScheduler):
    """Writes real (silent) WAV instead of the base class's placeholder bytes,
    so a directed request's join step has real audio to join and measure."""

    async def synthesize(self, request):
        self.requests.append(request)
        if self.raise_on_synthesize is not None:
            raise self.raise_on_synthesize
        dur = 0.3
        n = int(dur * request.sample_rate)
        data = np.full((n,), 0.1, dtype=np.float32)
        Path(request.output_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(request.output_path), data, request.sample_rate, subtype="PCM_16")
        self.resident.add(request.model_id)
        return SynthResult(
            output_path=Path(request.output_path),
            duration_sec=dur,
            gen_time_sec=0.01,
            sample_rate=request.sample_rate,
            model_id=request.model_id,
        )


def _client(tmp_path: Path, **settings_kwargs: Any) -> tuple[TestClient, _WavWritingScheduler]:
    sched = _WavWritingScheduler()
    settings = Settings(data_dir=tmp_path, allow_fake_runtime=True, **settings_kwargs)
    return TestClient(create_app(scheduler=sched, settings=settings)), sched


def _wav_bytes(dur: float = 1.5, sr: int = 16000) -> bytes:
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    buf = io.BytesIO()
    sf.write(buf, (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr, format="WAV")
    return buf.getvalue()


def _enroll(c: TestClient, language: str = "en") -> int:
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
    r = c.post("/api/generate", json=body)
    if r.status_code != 202:
        return r
    job_id = r.json()["id"]
    for _ in range(max_polls):
        poll = c.get(f"/api/jobs/{job_id}")
        assert poll.status_code == 200, poll.text
        if poll.json()["status"] in ("succeeded", "failed", "cancelled"):
            return poll
    raise AssertionError(f"job {job_id} did not reach a terminal status within {max_polls} polls")


def test_directed_generate_makes_multiple_segments(tmp_path: Path) -> None:
    client, sched = _client(tmp_path)
    with client as c:
        pid = _enroll(c, "en")
        r = _generate_and_poll(c, {
            "profile_id": pid,
            "text": "Hello there. This is exciting! Are you coming?",
            "language": "en",
            "apply_direction": True,
        })
        assert r.status_code == 200, r.text
        job = r.json()
        assert job["status"] == "succeeded", job

        # Three sentences -> three segments -> three scheduler calls, not one.
        assert len(sched.requests) == 3
        assert sched.requests[0].text == "Hello there."
        assert sched.requests[1].text == "This is exciting!"
        assert sched.requests[2].text == "Are you coming?"

        result = job["result"]
        assert result["segment_count"] == 3
        # Joined audio is real and playable.
        media = c.get(result["audio_url"])
        assert media.status_code == 200
        assert media.content != b"FAKE-NOT-AUDIO"
        assert len(media.content) > 44  # more than just a WAV header


def test_direction_default_is_single_shot(tmp_path: Path) -> None:
    """apply_direction defaults to false: multi-sentence text still makes
    exactly ONE scheduler call, the pre-existing behavior, unchanged."""
    client, sched = _client(tmp_path)
    with client as c:
        pid = _enroll(c, "en")
        r = _generate_and_poll(c, {
            "profile_id": pid,
            "text": "Hello there. This is exciting!",
            "language": "en",
        })
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "succeeded"
        assert len(sched.requests) == 1
        assert sched.requests[0].text == "Hello there. This is exciting!"
        assert r.json()["result"]["segment_count"] == 1


def test_directed_generate_single_sentence_still_directs(tmp_path: Path) -> None:
    """A single-sentence input still goes through the directed (one-segment)
    path when apply_direction=true — not silently downgraded to single-shot,
    since the capability chip already told the user what this model does."""
    client, sched = _client(tmp_path)
    with client as c:
        pid = _enroll(c, "en")
        r = _generate_and_poll(c, {
            "profile_id": pid,
            "text": "Hello there.",
            "language": "en",
            "apply_direction": True,
        })
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "succeeded"
        assert len(sched.requests) == 1
        assert r.json()["result"]["segment_count"] == 1


def test_directed_generate_route_unaffected(tmp_path: Path) -> None:
    """Direction changes HOW audio is produced, never WHAT is routed to — the
    route chip is identical to the non-directed request."""
    client, _sched = _client(tmp_path)
    with client as c:
        pid = _enroll(c, "en")
        r = _generate_and_poll(c, {
            "profile_id": pid,
            "text": "Hello there. Goodbye now.",
            "language": "en",
            "apply_direction": True,
        })
        assert r.status_code == 200, r.text
        job = r.json()
        assert job["route"]["model_id"] == "voxcpm2"
        assert job["route"]["transform"] == "none"
