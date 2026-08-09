"""
AI Voice Clone Studio — Async job queue.

`POST /api/generate` writes a row to the `jobs` table and returns 202 instead
of blocking on synthesis. A per-kind asyncio worker pool (`JobRunner`) claims
rows and calls the existing `InferenceScheduler.synthesize` unchanged — this
package never imports torch, and `app/inference/scheduler.py` is never
modified: the jobs table IS the queue, so no FIFO object goes into the
scheduler and its eviction-under-slot invariant stays intact.

See `docs/ARCHITECTURE.md` "Jobs" section for the full design and the golden-
rule risk register (why `route` is decided once at enqueue and never
recomputed at claim time; why the orphan rule ties every file to a job row;
why a `running` row found at startup is reaped rather than resumed).
"""

from __future__ import annotations

from .runner import JobRunner
from .types import JobKind, JobProblem, JobRecord, JobStatus, job_record_from_row

__all__ = [
    "JobKind",
    "JobStatus",
    "JobProblem",
    "JobRecord",
    "job_record_from_row",
    "JobRunner",
]
