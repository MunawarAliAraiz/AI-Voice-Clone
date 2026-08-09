"""
`JobRunner` semantics — claim/run/finish, concurrency, cancellation, drain.
Against `FakeScheduler` and a real `Database` on `tmp_path`. No HTTP, no GPU.

Determinism comes from `await runner.wait_idle()` and injected delays, never
a bare `asyncio.sleep` retry loop in the test itself.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import Settings
from app.db.database import Database
from app.exceptions import GenerationTimeoutError
from app.inference.catalog import CATALOG
from app.jobs import JobKind, JobRunner
from tests.fakes import FakeScheduler


class _ConcurrencyTrackingScheduler(FakeScheduler):
    """Counts overlapping `synthesize()` calls — proves concurrency=1 is real,
    not just declared."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.active = 0
        self.peak = 0

    async def synthesize(self, request):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            return await super().synthesize(request)
        finally:
            self.active -= 1


async def _setup(tmp_path: Path, sched: FakeScheduler | None = None, **settings_kwargs):
    """Real Database + real voice_profiles row (generation_history.profile_id
    is NOT NULL with an FK), a JobRunner wired to it, and the sched used."""
    db = Database(tmp_path / "test.db")
    await db.connect()
    settings = Settings(data_dir=tmp_path, **settings_kwargs)
    scheduler = sched or FakeScheduler(catalog=CATALOG)
    profile = await db.create_profile(
        name="v", audio_path=tmp_path / "ref.wav", language="en", transcript=None,
        duration_sec=2.0, sample_rate=44100, peak_dbfs=-3.0, is_clipped=False,
    )
    runner = JobRunner(db, scheduler, CATALOG, settings)
    await runner.reap_stale()
    return runner, db, scheduler, settings, profile["id"]


def _synth_params(tmp_path: Path, name: str, text: str = "hello world") -> dict:
    return {
        "text": text, "input_text": text, "language": "en",
        "reference_audio": str(tmp_path / "ref.wav"), "reference_text": None,
        "output_path": str(tmp_path / f"{name}.wav"), "output_format": "wav",
        "sample_rate": 44100, "params": {}, "speed": 1.0,
    }


_ROUTE = {
    "model_id": "voxcpm2", "model_display_name": "VoxCPM 2", "transform": "none",
    "lossy": False, "rationale": "test", "source_script": "latin", "alternatives": [],
}


async def test_job_row_is_running_before_synthesis_starts(tmp_path: Path) -> None:
    """The claim happens BEFORE the handler is invoked — a poller can never
    observe a job that has started synthesizing while its row still says
    'queued'."""
    seen_status: list[str] = []

    class _AssertingScheduler(FakeScheduler):
        async def synthesize(self, request):
            row = await db.get_job(job.id)
            seen_status.append(row["status"])
            return await super().synthesize(request)

    sched = _AssertingScheduler(catalog=CATALOG)
    runner, db, sched, _settings, pid = await _setup(tmp_path, sched)
    await runner.start()

    job = await runner.enqueue(
        JobKind.SYNTHESIZE, params=_synth_params(tmp_path, "a"), route=_ROUTE, profile_id=pid
    )
    await runner.wait_idle()

    assert seen_status == ["running"]
    finished = await db.get_job(job.id)
    assert finished["status"] == "succeeded"
    await runner.stop(drain_timeout_sec=1.0)
    await db.close()


async def test_one_synthesis_at_a_time(tmp_path: Path) -> None:
    sched = _ConcurrencyTrackingScheduler(catalog=CATALOG, synth_delay_sec=0.02)
    runner, db, sched, _settings, pid = await _setup(tmp_path, sched)
    await runner.start()

    for i in range(5):
        await runner.enqueue(
            JobKind.SYNTHESIZE, params=_synth_params(tmp_path, f"j{i}"), route=_ROUTE,
            profile_id=pid,
        )
    await runner.wait_idle()

    assert sched.peak == 1
    assert len(sched.requests) == 5
    await runner.stop(drain_timeout_sec=1.0)
    await db.close()


async def test_fifo_order(tmp_path: Path) -> None:
    sched = FakeScheduler(catalog=CATALOG, synth_delay_sec=0.01)
    runner, db, sched, _settings, pid = await _setup(tmp_path, sched)
    await runner.start()

    ids = []
    for i in range(4):
        job = await runner.enqueue(
            JobKind.SYNTHESIZE, params=_synth_params(tmp_path, f"j{i}", text=f"text-{i}"),
            route=_ROUTE, profile_id=pid,
        )
        ids.append(job.id)
    await runner.wait_idle()

    assert ids == sorted(ids)
    assert [r.text for r in sched.requests] == [f"text-{i}" for i in range(4)]
    await runner.stop(drain_timeout_sec=1.0)
    await db.close()


async def test_failed_job_stores_the_problem_document(tmp_path: Path) -> None:
    exc = GenerationTimeoutError("voxcpm2", 120.0)
    sched = FakeScheduler(catalog=CATALOG, raise_on_synthesize=exc)
    runner, db, sched, _settings, pid = await _setup(tmp_path, sched)
    await runner.start()

    job = await runner.enqueue(
        JobKind.SYNTHESIZE, params=_synth_params(tmp_path, "a"), route=_ROUTE, profile_id=pid
    )
    await runner.wait_idle()

    row = await db.get_job(job.id)
    assert row["status"] == "failed"
    assert row["error_code"] == exc.code == "GENERATION_TIMEOUT"
    assert row["error_status"] == exc.http_status == 504
    assert row["error_detail"] == exc.detail
    await runner.stop(drain_timeout_sec=1.0)
    await db.close()


async def test_unexpected_exception_becomes_internal_error(tmp_path: Path) -> None:
    secret = RuntimeError("secret /path/to/thing")
    sched = FakeScheduler(catalog=CATALOG, raise_on_synthesize=secret)
    runner, db, sched, _settings, pid = await _setup(tmp_path, sched)
    await runner.start()

    job = await runner.enqueue(
        JobKind.SYNTHESIZE, params=_synth_params(tmp_path, "a"), route=_ROUTE, profile_id=pid
    )
    await runner.wait_idle()

    row = await db.get_job(job.id)
    assert row["status"] == "failed"
    assert row["error_code"] == "INTERNAL_ERROR"
    assert row["error_status"] == 500
    # The exception's own message — a filesystem path here — must never reach
    # a client. Only the generic detail is stored; the real message is logged.
    assert "secret" not in (row["error_detail"] or "")
    assert "/path/to/thing" not in (row["error_detail"] or "")
    await runner.stop(drain_timeout_sec=1.0)
    await db.close()


async def test_cancel_removes_a_queued_job(tmp_path: Path) -> None:
    """No worker started — the job stays 'queued' until we cancel it directly."""
    runner, db, _sched, _settings, pid = await _setup(tmp_path)
    job = await runner.enqueue(
        JobKind.SYNTHESIZE, params=_synth_params(tmp_path, "a"), route=_ROUTE, profile_id=pid
    )
    cancelled = await runner.cancel(job.id)
    assert cancelled.status.value == "cancelled"
    row = await db.get_job(job.id)
    assert row["status"] == "cancelled"
    await db.close()


async def test_cancel_running_job_raises_not_cancellable(tmp_path: Path) -> None:
    sched = FakeScheduler(catalog=CATALOG, synth_delay_sec=0.2)
    runner, db, sched, _settings, pid = await _setup(tmp_path, sched)
    await runner.start()
    job = await runner.enqueue(
        JobKind.SYNTHESIZE, params=_synth_params(tmp_path, "a"), route=_ROUTE, profile_id=pid
    )
    # Yield once so the worker claims it before we try to cancel.
    await asyncio.sleep(0.05)
    row = await db.get_job(job.id)
    assert row["status"] == "running"

    from app.exceptions import JobNotCancellableError

    try:
        await runner.cancel(job.id)
        raise AssertionError("expected JobNotCancellableError")
    except JobNotCancellableError as exc:
        assert exc.extensions["status"] == "running"

    await runner.wait_idle()
    await runner.stop(drain_timeout_sec=1.0)
    await db.close()


async def test_cancel_unknown_job_raises_not_found(tmp_path: Path) -> None:
    runner, db, _sched, _settings, _pid = await _setup(tmp_path)
    from app.exceptions import JobNotFoundError

    try:
        await runner.cancel(999999)
        raise AssertionError("expected JobNotFoundError")
    except JobNotFoundError:
        pass
    await db.close()


async def test_enqueue_rejects_when_queue_depth_reached(tmp_path: Path) -> None:
    sched = FakeScheduler(catalog=CATALOG, synth_delay_sec=1.0)
    runner, db, sched, _settings, pid = await _setup(tmp_path, sched, job_queue_limit=2)
    # No start(): nothing drains the queue, so depth accumulates deterministically.
    await runner.enqueue(
        JobKind.SYNTHESIZE, params=_synth_params(tmp_path, "a"), route=_ROUTE, profile_id=pid
    )
    await runner.enqueue(
        JobKind.SYNTHESIZE, params=_synth_params(tmp_path, "b"), route=_ROUTE, profile_id=pid
    )
    from app.exceptions import JobQueueFullError

    try:
        await runner.enqueue(
            JobKind.SYNTHESIZE, params=_synth_params(tmp_path, "c"), route=_ROUTE, profile_id=pid
        )
        raise AssertionError("expected JobQueueFullError")
    except JobQueueFullError as exc:
        assert exc.extensions["limit"] == 2
        assert exc.extensions["depth"] == 2
    await db.close()


async def test_drain_waits_for_in_flight_job(tmp_path: Path) -> None:
    sched = FakeScheduler(catalog=CATALOG, synth_delay_sec=0.05)
    runner, db, sched, _settings, pid = await _setup(tmp_path, sched)
    await runner.start()
    job = await runner.enqueue(
        JobKind.SYNTHESIZE, params=_synth_params(tmp_path, "a"), route=_ROUTE, profile_id=pid
    )
    await asyncio.sleep(0.01)  # let it get claimed
    await runner.stop(drain_timeout_sec=2.0)  # generous: the 0.05s job finishes
    row = await db.get_job(job.id)
    assert row["status"] == "succeeded"
    await db.close()


async def test_drain_timeout_leaves_the_row_running(tmp_path: Path) -> None:
    """A drain timeout shorter than the job never marks it failed — the
    startup reaper decides next boot, with the disk in front of it."""
    sched = FakeScheduler(catalog=CATALOG, synth_delay_sec=5.0)
    runner, db, sched, _settings, pid = await _setup(tmp_path, sched)
    await runner.start()
    job = await runner.enqueue(
        JobKind.SYNTHESIZE, params=_synth_params(tmp_path, "a"), route=_ROUTE, profile_id=pid
    )
    await asyncio.sleep(0.01)  # let it get claimed
    await runner.stop(drain_timeout_sec=0.05)  # far shorter than the 5s job
    row = await db.get_job(job.id)
    assert row["status"] == "running"
    await db.close()
