"""
AI Voice Clone Studio — Job handler registry.

Adding a job kind is: one `JobKind` enum member (`app/jobs/types.py`), one
handler module here, one entry in `HANDLERS`, and one entry in
`Settings.job_concurrency`'s per-kind map if it needs a different pool size
than SYNTHESIZE's default of 1. Nothing in the runner, the DB layer, or the
`GET /api/jobs/{id}` polling endpoint changes — that is the whole point of
`kind` as an extension point.

A handler receives a `JobContext` (the same dependencies routers get via
`Depends`) and the claimed `JobRecord`, and returns a `JobOutcome` — or raises
an `AppError`, which the runner stores as the job's RFC 9457 problem document.
Handlers must not call `resolve()` — routing already ran once, at enqueue,
and its `RoutePlan` is on `job.route`. Recomputing it at claim time would be
routing-by-residency: the exact defect this codebase's rewrite exists to
prevent (see `app/domain/routing.py`).
"""

from __future__ import annotations

from ..types import JobContext, JobHandler, JobKind, JobOutcome
from .analyze_llm import run_analyze_llm
from .synthesize import run_synthesize

__all__ = ["JobContext", "JobOutcome", "JobHandler", "HANDLERS"]

HANDLERS: dict[JobKind, JobHandler] = {
    JobKind.SYNTHESIZE: run_synthesize,
    JobKind.ANALYZE_LLM: run_analyze_llm,
}
