"""
AI Voice Clone Studio — Queue position and ETA math.

PURE. No I/O, no clock read, no scheduler mutation — every input is a value
the caller already has (a `status()` snapshot, the running/queued job rows,
and `now` as a plain float). That purity is what lets this live outside
`app/domain/` (which forbids importing inference types at all) while still
being trivially unit-testable with literal inputs and literal expected output.

Position comes straight from the jobs table (`COUNT(*) WHERE status='queued'
AND id < :id`, done in `Database.count_ahead`) — no FIFO object goes into
`app/inference/scheduler.py`. ETA is the one piece of real math: it walks the
queue in enqueue order, simulating a ONE-element resident set to mirror the
scheduler's single GPU slot, because `ModelStatus.est_wait_sec` is the load
cost given CURRENT residency, and only the first queued job needing a given
model pays it — summing `est_wait_sec` naively across the whole queue would
double-count every load after the first.

This is a UI estimate, not a promise. Wherever it is rendered, it must be
labelled as one — never as a countdown that can only be wrong.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from ..inference.protocol import ModelStatus
from .types import JobKind, JobRecord

__all__ = [
    "estimate_synth_seconds",
    "estimate_remaining_for_running",
    "estimate_wait_seconds",
    "queue_position",
]

#: Rough average speaking rate used only to turn "characters of text" into
#: "seconds of audio" for the estimate. Deliberately crude — see the module
#: docstring: this is a UI estimate, not a promise, and text length is at best
#: a proxy for audio duration across scripts and languages.
_CHARS_PER_SEC_SPEECH = 15.0
_MIN_SYNTH_SEC = 1.0
_DEFAULT_RTF = 0.5


def estimate_synth_seconds(text_len: int, est_rtf: float | None) -> float:
    """
    Expected wall-clock synthesis time for `text_len` characters at `est_rtf`.

    Deliberately the EXPECTED case, not `InferenceScheduler._timeout_for`'s
    generous worst-case deadline — an ETA should track what usually happens,
    a timeout should track what could happen before the worker gets killed.
    """
    audio_duration_sec = max(1.0, text_len / _CHARS_PER_SEC_SPEECH)
    rtf = est_rtf if est_rtf else _DEFAULT_RTF
    return max(_MIN_SYNTH_SEC, audio_duration_sec * rtf)


def _parse_job_timestamp(ts: str) -> float:
    """Seconds since epoch for a `strftime('%Y-%m-%dT%H:%M:%SZ')` job timestamp."""
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()


def _job_model_id(job: JobRecord) -> str | None:
    route = job.route or {}
    model_id = route.get("model_id")
    return str(model_id) if model_id else None


#: Transliteration cost, as TWO terms — a fixed cost per chunk plus a rate per
#: character. Both solved from real jobs on the pod:
#:
#:     job 11   23 chunks   12,839 chars   420.8 s
#:     job 21    3 chunks      229 chars    25.7 s
#:
#: A single per-character rate was tried first and was badly wrong, because it
#: was fitted to job 11 alone. It predicted 7.5 s for job 21, which took 25.7 —
#: the UI then advertised "3 s" for a job that ran for half a minute, which is
#: worse than showing nothing. One data point cannot distinguish a fixed cost
#: from a proportional one; two can.
#:
#: The ~7 s per chunk is not mysterious. Every chunk is its own generation with
#: the whole system prompt and exemplar set prefilled ahead of it, so a batch of
#: 23 pays that 23 times. It is also why a 596-character part costs ~19 s while
#: 596 characters spread over three parts costs ~33.
#:
#: FITTED TO EXACTLY TWO POINTS. It reproduces both by construction, which is
#: not evidence — it is arithmetic. Treat it as a working model, and re-solve if
#: a real job misses it badly.
_TRANSLITERATE_SEC_PER_CHUNK = 7.03
_TRANSLITERATE_SEC_PER_CHAR = 0.0202
_MIN_TRANSLITERATE_SEC = 3.0


def _job_text_len(job: JobRecord) -> int:
    """
    Characters of input, across BOTH job param shapes.

    `texts` (a list) is what transliterate jobs carry since batching landed;
    `text` is what synthesize jobs carry. Reading only `text` reported every
    transliterate job as zero characters, so every one of them got an ETA of
    the floor value -- a 23-part, seven-minute conversion advertised as three
    seconds, which is worse than showing nothing.
    """
    params = job.params or {}
    texts = params.get("texts")
    if isinstance(texts, list):
        return sum(len(str(t)) for t in texts)
    text = params.get("text", "")
    return len(str(text)) if text else 0


def _job_chunk_count(job: JobRecord) -> int:
    """How many passages a transliterate job carries. At least 1."""
    texts = (job.params or {}).get("texts")
    return max(1, len(texts)) if isinstance(texts, list) else 1


def estimate_transliterate_seconds(chunks: int, text_len: int) -> float:
    """Expected wall-clock for `chunks` passages totalling `text_len` chars."""
    return max(
        _MIN_TRANSLITERATE_SEC,
        chunks * _TRANSLITERATE_SEC_PER_CHUNK + text_len * _TRANSLITERATE_SEC_PER_CHAR,
    )


def _job_cost_seconds(job: JobRecord, rtf_by_model: dict[str | None, float | None]) -> float:
    """
    Expected wall-clock for one job, dispatched on KIND.

    A transliterate job has no `route` and therefore no model id, so the
    synthesis path would price it at the floor regardless of size.
    """
    if job.kind is JobKind.TRANSLITERATE:
        return estimate_transliterate_seconds(_job_chunk_count(job), _job_text_len(job))
    return estimate_synth_seconds(_job_text_len(job), rtf_by_model.get(_job_model_id(job)))


def estimate_remaining_for_running(
    job: JobRecord, statuses: tuple[ModelStatus, ...], now: float
) -> float:
    """
    Estimated seconds left for a job that is ALREADY 'running' — its own
    expected total synth time minus how long it has been running. Floored at
    0 rather than letting a slower-than-expected job go negative.

    Shared by `estimate_wait_seconds` (for a job still queued behind this one)
    and the single-job status endpoint (for the running job's own ETA, which
    `estimate_wait_seconds` does not itself report — it only returns entries
    for the `queued` sequence).
    """
    rtf_by_model: dict[str | None, float | None] = {s.spec.id: s.spec.est_rtf for s in statuses}
    remaining_total = _job_cost_seconds(job, rtf_by_model)
    elapsed = (
        max(0.0, now - _parse_job_timestamp(job.started_at)) if job.started_at else 0.0
    )
    return max(0.0, remaining_total - elapsed)


def estimate_wait_seconds(
    statuses: tuple[ModelStatus, ...],
    running: JobRecord | None,
    queued: Sequence[JobRecord],
    now: float,
) -> dict[int, float]:
    """
    Per-job "seconds until this job is done", for jobs still `queued`.

    `queued` must already be in enqueue order (ascending `id` — the same order
    the claim query serves them in). Walks it once, keeping a SIMULATED
    resident model: loading model X evicts whatever was resident before it, so
    only the first job in a run that needs a given model pays its load cost —
    exactly what the real scheduler does with its single GPU slot.

    `statuses` is a `InferenceScheduler.status()` snapshot — this function
    never calls the scheduler itself; it is handed the numbers and does math.
    """
    load_cost_by_model = {s.spec.id: s.est_wait_sec for s in statuses}
    rtf_by_model: dict[str | None, float | None] = {s.spec.id: s.spec.est_rtf for s in statuses}

    t = 0.0
    resident: str | None = None

    if running is not None:
        t = estimate_remaining_for_running(running, statuses, now)
        resident = _job_model_id(running)

    out: dict[int, float] = {}
    for job in queued:
        model_id = _job_model_id(job)
        if model_id != resident:
            t += load_cost_by_model.get(model_id, 0.0) if model_id else 0.0
            resident = model_id
        t += _job_cost_seconds(job, rtf_by_model)
        out[job.id] = t
    return out


def queue_position(*, queued_ahead: int, has_running: bool) -> int:
    """
    0-indexed position in the effective queue: the running job (if any) is
    position 0, so a job with nothing queued ahead of it is position
    `1 if has_running else 0`.
    """
    return queued_ahead + (1 if has_running else 0)
