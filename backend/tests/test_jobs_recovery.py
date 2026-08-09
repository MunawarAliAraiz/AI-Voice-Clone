"""
`JobRunner.reap_stale()` — the startup recovery path. A 'running' row found at
boot is dead by definition (one uvicorn worker; see `app/jobs/runner.py`),
'queued' rows either survive or expire by age, and old terminal rows are
pruned by retention.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.db.database import Database
from app.inference.catalog import CATALOG
from app.jobs import JobKind, JobRunner
from tests.fakes import FakeScheduler

_ROUTE = {
    "model_id": "voxcpm2", "model_display_name": "VoxCPM 2", "transform": "none",
    "lossy": False, "rationale": "test", "source_script": "latin", "alternatives": [],
}


async def _setup(tmp_path: Path, sched: FakeScheduler | None = None, **settings_kwargs):
    db = Database(tmp_path / "test.db")
    await db.connect()
    settings = Settings(data_dir=tmp_path, **settings_kwargs)
    scheduler = sched or FakeScheduler(catalog=CATALOG)
    profile = await db.create_profile(
        name="v", audio_path=tmp_path / "ref.wav", language="en", transcript=None,
        duration_sec=2.0, sample_rate=44100, peak_dbfs=-3.0, is_clipped=False,
    )
    runner = JobRunner(db, scheduler, CATALOG, settings)
    await runner.reap_stale()  # nothing to reap yet; matches real startup order
    return runner, db, scheduler, settings, profile["id"]


def _synth_params(tmp_path: Path, name: str) -> dict:
    return {
        "text": "hi", "input_text": "hi", "language": "en",
        "reference_audio": str(tmp_path / "ref.wav"), "reference_text": None,
        "output_path": str(tmp_path / f"{name}.wav"), "output_format": "wav",
        "sample_rate": 44100, "params": {}, "speed": 1.0,
    }


async def _backdate(db: Database, row_id: int, column: str, *, seconds_ago: float) -> None:
    """Set a `jobs` timestamp column into the past, computed SQLite-side so
    the comparison in `expire_queued`/`delete_jobs_older_than` (which also
    computes its cutoff SQLite-side) is apples-to-apples."""
    await db._c.execute(
        f"UPDATE jobs SET {column} = strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?) WHERE id = ?",
        (f"-{seconds_ago} seconds", row_id),
    )
    await db._c.commit()


async def test_reap_running_marks_failed_and_unlinks_true_orphan(tmp_path: Path) -> None:
    _runner, db, sched, settings, _pid = await _setup(tmp_path)
    stale_out = tmp_path / "stale.wav"
    stale_out.write_bytes(b"partial")
    row = await db.create_job(
        kind="synthesize", params_json=json.dumps({"output_path": str(stale_out)}),
        route_json=None, profile_id=None,
    )
    claimed = await db.claim_next_job("synthesize")
    assert claimed["status"] == "running"

    # A fresh runner, as at a real restart — sharing the same db/scheduler.
    runner2 = JobRunner(db, sched, CATALOG, settings)
    await runner2.reap_stale()

    reaped = await db.get_job(row["id"])
    assert reaped["status"] == "failed"
    assert reaped["error_code"] == "JOB_INTERRUPTED"
    assert not stale_out.exists()
    await db.close()


async def test_reap_does_not_delete_a_file_a_history_row_still_references(tmp_path: Path) -> None:
    """
    The narrow race this guards: a handler can finish its real work (audio
    written, `generation_history` row created) and still be cut off before
    its OWN job row is updated to 'succeeded'. `job.history_id is None` would
    be true here even though the file is legitimately referenced — the reaper
    must check the history table itself, not the job row's own bookkeeping.
    """
    _runner, db, sched, settings, pid = await _setup(tmp_path)
    referenced_out = tmp_path / "referenced.wav"
    referenced_out.write_bytes(b"real audio bytes")
    await db.create_generation(
        profile_id=pid, input_text="hi", language="en", output_path=str(referenced_out),
        output_format="wav", duration_sec=1.0, gen_time_sec=0.1, model_id="voxcpm2",
        transform="none", is_lossy=False, source_script="latin", route_rationale="t",
        resolved_text="hi",
    )
    row = await db.create_job(
        kind="synthesize", params_json=json.dumps({"output_path": str(referenced_out)}),
        route_json=None, profile_id=pid,
    )
    await db.claim_next_job("synthesize")  # 'running'; history_id never set on THIS row

    runner2 = JobRunner(db, sched, CATALOG, settings)
    await runner2.reap_stale()

    reaped = await db.get_job(row["id"])
    assert reaped["status"] == "failed"  # the job row is still correctly reaped
    assert referenced_out.exists()  # but the file survives: history references it
    await db.close()


async def test_queued_job_survives_a_restart(tmp_path: Path) -> None:
    runner, db, sched, settings, pid = await _setup(tmp_path, job_queue_max_age_sec=3600)
    job = await runner.enqueue(
        JobKind.SYNTHESIZE, params=_synth_params(tmp_path, "a"), route=_ROUTE, profile_id=pid
    )
    runner2 = JobRunner(db, sched, CATALOG, settings)
    await runner2.reap_stale()
    row = await db.get_job(job.id)
    assert row["status"] == "queued"
    await db.close()


async def test_stale_queued_job_expires(tmp_path: Path) -> None:
    runner, db, sched, settings, pid = await _setup(tmp_path, job_queue_max_age_sec=60)
    job = await runner.enqueue(
        JobKind.SYNTHESIZE, params=_synth_params(tmp_path, "a"), route=_ROUTE, profile_id=pid
    )
    await _backdate(db, job.id, "queued_at", seconds_ago=120)

    runner2 = JobRunner(db, sched, CATALOG, settings)
    await runner2.reap_stale()
    row = await db.get_job(job.id)
    assert row["status"] == "failed"
    assert row["error_code"] == "JOB_EXPIRED"
    await db.close()


async def test_terminal_jobs_are_deleted_past_retention_but_queued_never_is(
    tmp_path: Path,
) -> None:
    sched = FakeScheduler(catalog=CATALOG)
    # job_queue_max_age_sec set high so a deliberately-old QUEUED job below is
    # governed only by retention (which never touches non-terminal rows), not
    # by expiry — isolating the one behavior this test targets.
    runner, db, sched, settings, pid = await _setup(
        tmp_path, sched, job_retention_hours=24, job_queue_max_age_sec=999_999
    )
    await runner.start()
    done_job = await runner.enqueue(
        JobKind.SYNTHESIZE, params=_synth_params(tmp_path, "a"), route=_ROUTE, profile_id=pid
    )
    await runner.wait_idle()
    assert (await db.get_job(done_job.id))["status"] == "succeeded"
    await runner.stop(drain_timeout_sec=1.0)
    await _backdate(db, done_job.id, "finished_at", seconds_ago=25 * 3600)

    still_queued = await db.create_job(
        kind="synthesize", params_json="{}", route_json=None, profile_id=None
    )
    await _backdate(db, still_queued["id"], "queued_at", seconds_ago=25 * 3600)

    runner2 = JobRunner(db, sched, CATALOG, settings)
    await runner2.reap_stale()

    assert await db.get_job(done_job.id) is None  # deleted: terminal + past retention
    survivor = await db.get_job(still_queued["id"])
    assert survivor is not None
    assert survivor["status"] == "queued"  # never a retention-deletion candidate
    await db.close()
