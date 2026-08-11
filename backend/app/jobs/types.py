"""
AI Voice Clone Studio — Job queue types.

CONTRACT MODULE for the async job queue (Phase 1 of the mobile/perf work). No
I/O, no clock, no torch — this module is pure data shapes shared by the DB
layer, the runner, and the HTTP surface.

`JobStatus` is a closed set of five, and it must never drift from the literal
list inside the `CHECK` clause on the `jobs` table (`db/schema.sql`). Under the
`CREATE TABLE IF NOT EXISTS` scheme an existing table keeps its old constraint
forever — there is no migration path to add a sixth status — so the enum and
the SQL are treated as one fact stated twice, and `test_job_status_set_is_closed`
in `tests/test_contracts.py` asserts they agree.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Settings
    from ..db import Database
    from ..inference.analyzer_scheduler import AnalyzerScheduler
    from ..inference.catalog import ModelCatalog
    from ..inference.protocol import SchedulerProtocol

__all__ = [
    "JobKind",
    "JobStatus",
    "JobProblem",
    "JobRecord",
    "job_record_from_row",
    "JobContext",
    "JobOutcome",
    "JobHandler",
]


class JobKind(StrEnum):
    """
    What a job does. The extension point: adding a kind is one enum member, one
    handler module, and one entry in the runner's per-kind concurrency map —
    nothing in the table, the DB layer, or the polling endpoint changes.
    """

    SYNTHESIZE = "synthesize"
    #: LLM-backed Speech Direction classification (`jobs/handlers/analyze_llm.py`).
    #: No `generation_history` row, no audio, no `route` (never touches
    #: `resolve()`/the audio catalog) — see that handler's docstring.
    ANALYZE_LLM = "analyze_llm"


class JobStatus(StrEnum):
    """Must equal the `CHECK (status IN (...))` literal set in `schema.sql`, verbatim."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


@dataclass(frozen=True, slots=True)
class JobProblem:
    """
    A stored RFC 9457 problem document, decomposed into the `jobs` table's
    `error_*` columns and reassembled on read into exactly what `AppError.to_problem()`
    produces (`app/exceptions.py`) — so the frontend's existing problem+json
    handling (`services/api.ts`) works on a job's `error` field unchanged.
    """

    code: str
    title: str
    status: int
    detail: str
    extensions: dict[str, Any]

    def to_problem(self, instance: str | None = None) -> dict[str, Any]:
        problem: dict[str, Any] = {
            "type": f"https://voiceclone.local/problems/{self.code.lower().replace('_', '-')}",
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "code": self.code,
        }
        if instance:
            problem["instance"] = instance
        problem.update(self.extensions)
        return problem


@dataclass(frozen=True, slots=True)
class JobRecord:
    """
    One row of the `jobs` table, as read back from the DB layer.

    `params` and `result` are opaque outside the kind's own handler — each kind
    owns a pydantic model for its `params` shape and re-validates at claim time,
    so params written by an older build fail loudly rather than being coerced.
    `route` is decided once, at enqueue, by the pure `resolve()` — never
    recomputed at claim time (that would be routing-by-residency, the exact
    defect this codebase's rewrite exists to prevent).
    """

    id: int
    kind: JobKind
    status: JobStatus
    params: dict[str, Any]
    route: dict[str, Any] | None
    history_id: int | None
    result: dict[str, Any] | None
    error: JobProblem | None
    profile_id: int | None
    priority: int
    cancel_requested: bool
    attempt: int
    queued_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str


def job_record_from_row(row: Mapping[str, Any]) -> JobRecord:
    """
    Decode one `jobs` row (as `Database` returns it — a plain mapping,
    `aiosqlite.Row` included) into a `JobRecord`. The one place `params_json` /
    `route_json` / `result_json` / `error_extensions_json` get parsed, shared
    by the runner and the HTTP layer so both see the same shape.
    """
    error: JobProblem | None = None
    if row["error_code"]:
        error = JobProblem(
            code=row["error_code"],
            title=row["error_title"] or "",
            status=row["error_status"] or 500,
            detail=row["error_detail"] or "",
            extensions=json.loads(row["error_extensions_json"] or "{}"),
        )
    return JobRecord(
        id=row["id"],
        kind=JobKind(row["kind"]),
        status=JobStatus(row["status"]),
        params=json.loads(row["params_json"]) if row["params_json"] else {},
        route=json.loads(row["route_json"]) if row["route_json"] else None,
        history_id=row["history_id"],
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        error=error,
        profile_id=row["profile_id"],
        priority=row["priority"],
        cancel_requested=bool(row["cancel_requested"]),
        attempt=row["attempt"],
        queued_at=row["queued_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        updated_at=row["updated_at"],
    )


@dataclass(frozen=True, slots=True)
class JobContext:
    """
    The dependencies a handler needs — the same ones routers get via
    `Depends`. Lives here (not in `handlers/__init__.py`) so a handler module
    can import it without importing the handler registry that imports the
    handler module — `handlers/__init__.py` builds `HANDLERS` by importing
    each handler function; if `JobContext` lived there too, every handler
    module would need to import back from the package that imports it.
    """

    db: Database
    scheduler: SchedulerProtocol
    catalog: ModelCatalog
    settings: Settings
    #: The Qwen Speech-Direction analyzer's own scheduler (`analyzer_scheduler
    #: .AnalyzerScheduler`) — a structurally separate worker kind from the
    #: audio `scheduler`, never the same object. Only `analyze_llm.py` reads
    #: this; every other handler ignores it.
    analyzer: AnalyzerScheduler


@dataclass(frozen=True, slots=True)
class JobOutcome:
    """
    What a handler produced. `history_id` is the typed ref for kinds that
    write a `generation_history` row; `result` is for kinds that don't — it
    exists now because `jobs.result_json` cannot be added to the table later.
    """

    history_id: int | None = None
    result: dict[str, Any] | None = None


#: A handler receives the shared dependencies and the claimed row, and either
#: returns what it produced or raises an `AppError`, which the runner stores
#: as the job's RFC 9457 problem document.
JobHandler = Callable[["JobContext", "JobRecord"], Awaitable["JobOutcome"]]
