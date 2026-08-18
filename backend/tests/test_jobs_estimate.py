"""
Pure position/ETA math (`app/jobs/estimate.py`). Literal inputs, literal
expected output — no DB, no scheduler, no event loop.
"""

from __future__ import annotations

from app.domain.language import Script
from app.inference.protocol import ModelStatus
from app.inference.spec import LanguageSupport, License, ModelSpec, ModelState, RuntimeKind
from app.jobs.estimate import (
    _job_cost_seconds,
    estimate_remaining_for_running,
    estimate_synth_seconds,
    estimate_wait_seconds,
    queue_position,
)
from app.jobs.types import JobKind, JobRecord, JobStatus

_SPEC_A = ModelSpec(
    id="voxcpm2", display_name="VoxCPM 2", runtime=RuntimeKind.VOXCPM, license=License.MIT,
    hf_repo="x/y", hf_revision="a" * 40,
    languages=(LanguageSupport("en", Script.LATIN, verified=True),),
    vram_mb=1000, est_load_sec=120.0, est_rtf=0.5,
)
_SPEC_B = ModelSpec(
    id="f5_openbible_urdu", display_name="F5 Urdu", runtime=RuntimeKind.F5, license=License.MIT,
    hf_repo="x/y", hf_revision="b" * 40,
    languages=(LanguageSupport("ur", Script.ARABIC, verified=True),),
    vram_mb=1000, est_load_sec=60.0, est_rtf=0.8,
)


def _job(
    id_: int, model_id: str, text: str, *,
    status: JobStatus = JobStatus.QUEUED, started_at: str | None = None,
) -> JobRecord:
    return JobRecord(
        id=id_, kind=JobKind.SYNTHESIZE, status=status,
        params={"text": text}, route={"model_id": model_id},
        history_id=None, result=None, error=None, profile_id=None,
        priority=0, cancel_requested=False, attempt=0,
        queued_at="2026-01-01T00:00:00Z", started_at=started_at,
        finished_at=None, updated_at="2026-01-01T00:00:00Z",
    )


def _translit_job(id_: int, texts: list[str]) -> JobRecord:
    """A transliterate job: `texts`, and NO route (it never calls resolve())."""
    return JobRecord(
        id=id_, kind=JobKind.TRANSLITERATE, status=JobStatus.QUEUED,
        params={"texts": texts, "target": "roman"}, route=None,
        history_id=None, result=None, error=None, profile_id=None,
        priority=0, cancel_requested=False, attempt=0,
        queued_at="2026-01-01T00:00:00Z", started_at=None,
        finished_at=None, updated_at="2026-01-01T00:00:00Z",
    )


def test_estimate_synth_seconds_floors_at_minimum() -> None:
    assert estimate_synth_seconds(0, 0.5) >= 1.0
    assert estimate_synth_seconds(1, None) >= 1.0


def test_estimate_synth_seconds_scales_with_length_and_rtf() -> None:
    short = estimate_synth_seconds(15, 1.0)
    long = estimate_synth_seconds(150, 1.0)
    assert long > short
    faster = estimate_synth_seconds(150, 0.5)
    slower = estimate_synth_seconds(150, 2.0)
    assert faster < slower


def test_queue_position_no_running() -> None:
    assert queue_position(queued_ahead=0, has_running=False) == 0
    assert queue_position(queued_ahead=3, has_running=False) == 3


def test_queue_position_with_running_adds_one() -> None:
    assert queue_position(queued_ahead=0, has_running=True) == 1
    assert queue_position(queued_ahead=2, has_running=True) == 3


def test_wait_seconds_same_resident_model_pays_no_load_cost() -> None:
    """Both jobs need voxcpm2, which is already RESIDENT — neither should pay a load cost."""
    statuses = (
        ModelStatus(spec=_SPEC_A, state=ModelState.RESIDENT, est_wait_sec=0.0),
    )
    queued = [_job(1, "voxcpm2", "hello world"), _job(2, "voxcpm2", "another one")]
    eta = estimate_wait_seconds(statuses, None, queued, now=1000.0)
    # Second job's ETA is exactly the first's plus its own synth time — no gap
    # for a load cost, because nothing evicted voxcpm2 in between.
    assert eta[2] - eta[1] == estimate_synth_seconds(len("another one"), 0.5)


def test_wait_seconds_charges_cold_load_exactly_once_per_model_switch() -> None:
    """Job 3 switches to a COLD model; only it should pay the 60s load cost."""
    statuses = (
        ModelStatus(spec=_SPEC_A, state=ModelState.RESIDENT, est_wait_sec=0.0),
        ModelStatus(spec=_SPEC_B, state=ModelState.COLD, est_wait_sec=60.0),
    )
    queued = [
        _job(1, "voxcpm2", "hello world"),
        _job(2, "voxcpm2", "another one"),
        _job(3, "f5_openbible_urdu", "salam"),
    ]
    eta = estimate_wait_seconds(statuses, None, queued, now=1000.0)
    assert eta[1] < eta[2] < eta[3]
    assert eta[3] - eta[2] >= 60.0
    # A fourth job on the SAME now-resident f5 model pays nothing extra for load.
    eta_with_fourth = estimate_wait_seconds(
        statuses, None, [*queued, _job(4, "f5_openbible_urdu", "shukriya")], now=1000.0
    )
    assert eta_with_fourth[4] - eta_with_fourth[3] == estimate_synth_seconds(len("shukriya"), 0.8)


def test_wait_seconds_empty_queue_is_empty_dict() -> None:
    statuses = (ModelStatus(spec=_SPEC_A, state=ModelState.RESIDENT, est_wait_sec=0.0),)
    assert estimate_wait_seconds(statuses, None, [], now=0.0) == {}


def test_wait_seconds_accounts_for_a_running_job_ahead() -> None:
    statuses = (ModelStatus(spec=_SPEC_A, state=ModelState.RESIDENT, est_wait_sec=0.0),)
    running = _job(
        0, "voxcpm2", "x" * 150, status=JobStatus.RUNNING, started_at="2026-01-01T00:00:00Z"
    )
    queued = [_job(1, "voxcpm2", "hello")]
    now = _epoch(2026, 1, 1, 0, 0, 3)
    eta = estimate_wait_seconds(statuses, running, queued, now=now)
    # Job 1's ETA must be strictly greater than its own synth time alone —
    # it has to wait for whatever's left of the running job first.
    assert eta[1] > estimate_synth_seconds(len("hello"), 0.5)


def test_remaining_for_running_decreases_as_time_passes_and_floors_at_zero() -> None:
    statuses = (ModelStatus(spec=_SPEC_A, state=ModelState.RESIDENT, est_wait_sec=0.0),)
    running = _job(
        0, "voxcpm2", "x" * 150, status=JobStatus.RUNNING, started_at="2026-01-01T00:00:00Z"
    )
    remaining_early = estimate_remaining_for_running(
        running, statuses, now=_epoch(2026, 1, 1, 0, 0, 1)
    )
    remaining_late = estimate_remaining_for_running(
        running, statuses, now=_epoch(2026, 1, 1, 0, 0, 1) + 10_000
    )
    assert remaining_late < remaining_early
    assert remaining_late == 0.0  # floored, never negative


def test_remaining_for_running_with_no_started_at_is_full_estimate() -> None:
    statuses = (ModelStatus(spec=_SPEC_A, state=ModelState.RESIDENT, est_wait_sec=0.0),)
    running = _job(0, "voxcpm2", "hello", status=JobStatus.RUNNING, started_at=None)
    remaining = estimate_remaining_for_running(running, statuses, now=1_000_000.0)
    assert remaining == estimate_synth_seconds(len("hello"), 0.5)


def _epoch(year: int, month: int, day: int, hour: int, minute: int, second: int) -> float:
    import datetime

    return datetime.datetime(
        year, month, day, hour, minute, second, tzinfo=datetime.UTC
    ).timestamp()


def test_a_transliterate_job_is_priced_on_its_texts_not_its_missing_text() -> None:
    """
    `_job_text_len` read `params["text"]`, which transliterate jobs stopped
    carrying when batching moved them to `params["texts"]`. Every one of them
    therefore measured zero characters and got the floor ETA — a 23-part,
    seven-minute conversion advertised as three seconds, which is worse than
    showing no estimate at all.
    """
    job = _translit_job(1, ["x" * 558 for _ in range(23)])
    seconds = _job_cost_seconds(job, {})

    # The real run of this shape took 420.8 s. Checked against a measurement
    # rather than against itself.
    assert 380 < seconds < 460, seconds


def test_the_estimate_accounts_for_PER_CHUNK_cost_not_just_length() -> None:
    """
    The bug a single per-character rate produced: 229 characters spread over 3
    chunks took 25.7 s, and a length-only model predicted 7.5. Every chunk
    prefills the whole system prompt and exemplar set, so chunk COUNT is a
    first-class term — the same characters in one chunk are much cheaper than
    in ten.
    """
    one_big = _translit_job(1, ["x" * 300])
    ten_small = _translit_job(2, ["x" * 30 for _ in range(10)])
    assert _job_cost_seconds(ten_small, {}) > _job_cost_seconds(one_big, {}) * 2

    # And the three-chunk measurement itself: 3 chunks / 229 chars -> 25.7 s.
    measured = _translit_job(3, ["x" * 76, "x" * 76, "x" * 77])
    assert 20 < _job_cost_seconds(measured, {}) < 32


def test_a_transliterate_job_is_not_priced_like_synthesis() -> None:
    """
    A transliterate job has no `route`, so the synthesis path would price it
    through a model id that does not exist and land on the floor no matter how
    large it is. KIND decides the cost model, not route.
    """
    small = _translit_job(1, ["x" * 100])
    large = _translit_job(2, ["x" * 10_000])
    assert _job_cost_seconds(large, {}) > _job_cost_seconds(small, {}) * 20


def test_a_synthesize_job_still_uses_the_speech_model() -> None:
    """The transliterate branch must not have changed synthesis pricing."""
    job = _job(1, "voxcpm2", "x" * 300)
    assert _job_cost_seconds(job, {"voxcpm2": None}) == estimate_synth_seconds(300, None)
