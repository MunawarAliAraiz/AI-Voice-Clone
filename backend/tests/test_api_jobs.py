"""
The `/api/jobs` HTTP surface: enqueue shape, polling, queue depth backpressure,
cancellation, and the Recent-view list. `test_api_generate.py` covers the full
generate-to-succeeded flow via `_generate_and_poll`; this file focuses on the
jobs endpoints themselves.
"""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeScheduler


def _client(tmp_path: Path, sched: FakeScheduler | None = None, **settings_kwargs: Any):
    scheduler = sched or FakeScheduler()
    settings = Settings(data_dir=tmp_path, allow_fake_runtime=True, **settings_kwargs)
    return TestClient(create_app(scheduler=scheduler, settings=settings)), scheduler


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


def _poll(c: TestClient, job_id: int, *, max_polls: int = 200) -> dict[str, Any]:
    for _ in range(max_polls):
        r = c.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach a terminal status within {max_polls} polls")


def test_generate_returns_202_with_a_job_id_and_route(tmp_path: Path) -> None:
    client, _sched = _client(tmp_path)
    with client as c:
        pid = _enroll(c, "ur")
        r = c.post(
            "/api/generate",
            json={"profile_id": pid, "text": "aap kaise hain", "language": "ur"},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert isinstance(body["id"], int)
        assert body["kind"] == "synthesize"
        assert body["status"] == "queued"
        # Route is known and rendered from the moment the job is still queued
        # — routing is pure and already ran, before the row was even written.
        assert body["route"]["model_id"] == "voxcpm2"
        assert body["result"] is None
        assert body["error"] is None


def test_queued_job_reports_position_and_eta(tmp_path: Path) -> None:
    client, _sched = _client(tmp_path, FakeScheduler(synth_delay_sec=0.3))
    with client as c:
        pid = _enroll(c)
        first = c.post("/api/generate", json={"profile_id": pid, "text": "hi", "language": "en"})
        assert first.status_code == 202
        second = c.post(
            "/api/generate", json={"profile_id": pid, "text": "there", "language": "en"}
        )
        assert second.status_code == 202
        second_id = second.json()["id"]

        # The first job's 0.3s delay guarantees the second is still queued.
        poll = c.get(f"/api/jobs/{second_id}")
        assert poll.status_code == 200
        body = poll.json()
        assert body["status"] == "queued"
        assert body["position"] is not None and body["position"] >= 1
        assert body["eta_sec"] is not None and body["eta_sec"] > 0

        _poll(c, first.json()["id"])
        _poll(c, second_id)


def test_queue_depth_limit_returns_503_job_queue_full(tmp_path: Path) -> None:
    client, _sched = _client(
        tmp_path, FakeScheduler(synth_delay_sec=1.0), job_queue_limit=2
    )
    with client as c:
        pid = _enroll(c)
        for i in range(2):
            r = c.post(
                "/api/generate", json={"profile_id": pid, "text": f"t{i}", "language": "en"}
            )
            assert r.status_code == 202, r.text

        r3 = c.post("/api/generate", json={"profile_id": pid, "text": "t2", "language": "en"})
        assert r3.status_code == 503
        assert r3.json()["code"] == "JOB_QUEUE_FULL"


def test_cancel_queued_job_204(tmp_path: Path) -> None:
    client, _sched = _client(tmp_path, FakeScheduler(synth_delay_sec=1.0))
    with client as c:
        pid = _enroll(c)
        first = c.post("/api/generate", json={"profile_id": pid, "text": "a", "language": "en"})
        second = c.post("/api/generate", json={"profile_id": pid, "text": "b", "language": "en"})
        second_id = second.json()["id"]

        cancel = c.delete(f"/api/jobs/{second_id}")
        assert cancel.status_code == 204
        assert c.get(f"/api/jobs/{second_id}").json()["status"] == "cancelled"

        _poll(c, first.json()["id"])


def test_retry_reenqueues_a_cancelled_job_with_the_same_text_and_route(
    tmp_path: Path,
) -> None:
    """
    `JOB_INTERRUPTED` — what a server restart leaves behind — tells the user to
    "Re-submit it", and there was no way to do so without retyping the text
    from the row still displaying it.

    The retry reuses the ORIGINAL route rather than calling `resolve()` again.
    Golden rule 8 is explicit that re-resolving at claim time is rule 4's bug
    wearing a queue, and a retry button is the same bug wearing a different
    hat: the job would come back on a model the user was never shown.
    """
    client, _sched = _client(tmp_path, FakeScheduler(synth_delay_sec=1.0))
    with client as c:
        pid = _enroll(c)
        first = c.post("/api/generate", json={"profile_id": pid, "text": "a", "language": "en"})
        second = c.post(
            "/api/generate", json={"profile_id": pid, "text": "b", "language": "en"}
        )
        original = second.json()
        assert c.delete(f"/api/jobs/{original['id']}").status_code == 204

        retry = c.post(f"/api/jobs/{original['id']}/retry")
        assert retry.status_code == 202, retry.text
        clone = retry.json()

        assert clone["id"] != original["id"], "retry must be a NEW row"
        assert clone["status"] == "queued"
        assert clone["input_text"] == original["input_text"]
        assert clone["route"]["model_id"] == original["route"]["model_id"]

        # History stays truthful: the cancelled attempt is still cancelled.
        assert c.get(f"/api/jobs/{original['id']}").json()["status"] == "cancelled"

        _poll(c, first.json()["id"])
        _poll(c, clone["id"])


def test_retry_writes_to_a_fresh_output_path(tmp_path: Path) -> None:
    """
    The orphan rule (`app/jobs/runner.py`): no file is written whose path is
    not already recorded on a job row. Reusing the original's path would put
    two rows on one file — and the startup reaper may already have swept it.
    """
    client, _sched = _client(tmp_path)
    with client as c:
        pid = _enroll(c)
        first = c.post("/api/generate", json={"profile_id": pid, "text": "a", "language": "en"})
        job_id = first.json()["id"]
        done = _poll(c, job_id)
        assert done["status"] == "succeeded"

        retry = c.post(f"/api/jobs/{job_id}/retry")
        assert retry.status_code == 202
        clone = _poll(c, retry.json()["id"])
        assert clone["status"] == "succeeded"
        # Two successes, two distinct history rows, so two distinct files.
        assert clone["result"]["id"] != done["result"]["id"]


def test_retry_of_a_queued_job_is_409(tmp_path: Path) -> None:
    """A queued job is not stuck, it is waiting. Duplicating it would put two
    generations of the same text on a GPU that runs one at a time."""
    client, _sched = _client(tmp_path, FakeScheduler(synth_delay_sec=1.0))
    with client as c:
        pid = _enroll(c)
        first = c.post("/api/generate", json={"profile_id": pid, "text": "a", "language": "en"})
        second = c.post("/api/generate", json={"profile_id": pid, "text": "b", "language": "en"})
        second_id = second.json()["id"]

        r = c.post(f"/api/jobs/{second_id}/retry")
        assert r.status_code == 409
        assert r.json()["code"] == "JOB_NOT_RETRYABLE"

        c.delete(f"/api/jobs/{second_id}")
        _poll(c, first.json()["id"])


def test_retry_unknown_job_404(tmp_path: Path) -> None:
    client, _sched = _client(tmp_path)
    with client as c:
        assert c.post("/api/jobs/99999/retry").status_code == 404


def test_cancel_running_job_409(tmp_path: Path) -> None:
    client, _sched = _client(tmp_path, FakeScheduler(synth_delay_sec=1.0))
    with client as c:
        pid = _enroll(c)
        first = c.post("/api/generate", json={"profile_id": pid, "text": "a", "language": "en"})
        first_id = first.json()["id"]

        # Give the worker a moment to claim it before trying to cancel.
        deadline = time.monotonic() + 2.0
        status = None
        while time.monotonic() < deadline:
            status = c.get(f"/api/jobs/{first_id}").json()["status"]
            if status == "running":
                break
            time.sleep(0.01)
        assert status == "running", f"job never reached running (last status: {status})"

        cancel = c.delete(f"/api/jobs/{first_id}")
        assert cancel.status_code == 409
        assert cancel.json()["code"] == "JOB_NOT_CANCELLABLE"

        _poll(c, first_id)


def test_cancel_unknown_job_404(tmp_path: Path) -> None:
    client, _sched = _client(tmp_path)
    with client as c:
        assert c.delete("/api/jobs/999999").status_code == 404
        assert c.get("/api/jobs/999999").status_code == 404


def test_jobs_list_is_the_recent_view(tmp_path: Path) -> None:
    client, _sched = _client(tmp_path)
    with client as c:
        pid = _enroll(c)
        r = c.post("/api/generate", json={"profile_id": pid, "text": "hi", "language": "en"})
        job = _poll(c, r.json()["id"])
        assert job["status"] == "succeeded"

        lst = c.get("/api/jobs").json()
        assert lst["total"] == 1 and len(lst["items"]) == 1
        item = lst["items"][0]
        assert item["id"] == job["id"]
        assert item["status"] == "succeeded"
        # The list never computes position/eta — only the single-job poll does.
        assert item["position"] is None
        assert item["eta_sec"] is None
        assert item["result"]["id"] == job["result"]["id"]


async def test_fake_runtime_job_carries_the_header_and_flag(tmp_path: Path) -> None:
    """
    Golden rule 1: fake audio is never silent about being fake.

    Exercised directly against `build_job_status_response`, not through HTTP +
    real routing: `test_contracts.py` asserts the shipped `CATALOG` structurally
    excludes `RuntimeKind.FAKE` specs, so `resolve()` can never route a real
    request there — a genuine fake-runtime job only happens via a real worker
    subprocess under `VCS_ALLOW_FAKE_RUNTIME` (covered at the scheduler level
    by `test_scheduler_integration.py`). What this test covers, and must
    cover, is that the response builder honors a stored `is_fake` flag exactly
    the way `app/jobs/handlers/synthesize.py` writes it.
    """
    from fastapi import Response

    from app.api.routers.jobs import build_job_status_response
    from app.db.database import Database
    from app.jobs.types import JobKind, JobRecord, JobStatus

    db = Database(tmp_path / "test.db")
    await db.connect()
    profile = await db.create_profile(
        name="v", audio_path=tmp_path / "ref.wav", language="en", transcript=None,
        duration_sec=2.0, sample_rate=44100, peak_dbfs=-3.0, is_clipped=False,
    )
    gen = await db.create_generation(
        profile_id=profile["id"], input_text="hi", language="en",
        output_path=str(tmp_path / "out.wav"), output_format="wav",
        duration_sec=1.0, gen_time_sec=0.1, model_id="fake", transform="none",
        is_lossy=False, source_script="latin", route_rationale="t", resolved_text="hi",
    )
    job = JobRecord(
        id=1, kind=JobKind.SYNTHESIZE, status=JobStatus.SUCCEEDED,
        params={},
        route={
            "model_id": "fake", "model_display_name": "Fake", "transform": "none",
            "lossy": False, "rationale": "t", "source_script": "latin", "alternatives": [],
        },
        history_id=gen["id"],
        result={
            "history_id": gen["id"], "duration_sec": 1.0, "gen_time_sec": 0.1, "rtf": 0.1,
            "language": "en", "created_at": gen["created_at"], "is_fake": True,
        },
        error=None, profile_id=profile["id"], priority=0, cancel_requested=False,
        attempt=1, queued_at=gen["created_at"], started_at=gen["created_at"],
        finished_at=gen["created_at"], updated_at=gen["created_at"],
    )
    settings = Settings(data_dir=tmp_path, allow_fake_runtime=True)
    response = Response()

    body = await build_job_status_response(job, db, settings, FakeScheduler(), response)

    assert body.is_fake is True
    assert response.headers.get("X-Fake-Audio") == "true"
    await db.close()


def test_a_retry_records_which_job_it_retried(tmp_path: Path) -> None:
    """
    The retry endpoint creates a NEW row and leaves the original visible, which
    is right -- history stays truthful. But nothing LINKED the two, so a
    settled row kept offering "Try again" after it had already been retried,
    and four clicks produced four identical queued jobs with no way to tell
    they were one attempt.

    `retry_of_job_id` is that link, and it is what lets the UI show "Retried"
    where the button was.
    """
    client, _sched = _client(tmp_path, FakeScheduler(synth_delay_sec=1.0))
    with client as c:
        pid = _enroll(c)
        first = c.post("/api/generate", json={"profile_id": pid, "text": "a", "language": "en"})
        second = c.post(
            "/api/generate", json={"profile_id": pid, "text": "b", "language": "en"}
        )
        original = second.json()
        assert c.delete(f"/api/jobs/{original['id']}").status_code == 204

        clone = c.post(f"/api/jobs/{original['id']}/retry").json()
        assert clone["id"] != original["id"], "a retry must be its own row"
        assert clone["retry_of_job_id"] == original["id"]

        # A first attempt retries nothing, so the original stays null -- that
        # is how the UI tells "offer the button" from "already retried".
        assert c.get(f"/api/jobs/{original['id']}").json()["retry_of_job_id"] is None

        _poll(c, first.json()["id"])
        _poll(c, clone["id"])
