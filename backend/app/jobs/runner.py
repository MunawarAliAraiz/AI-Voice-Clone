"""
AI Voice Clone Studio — the job runner.

A per-`JobKind` asyncio worker pool. Claims rows from the `jobs` table
(itself the queue — see `app/jobs/__init__.py`) and calls the registered
handler. Never imports torch; never touches `app/inference/scheduler.py`.

THE ORPHAN RULE
----------------
    No file is ever written whose path is not already recorded in a job row.

`output_path` is chosen and stored in `params_json` at ENQUEUE time, before
any synthesis starts — so a caller-side disconnect (nothing awaits the HTTP
request past 202) can never separate "file exists" from "we know about it".

THE REAPING RULE
-----------------
    A 'running' row found at startup is dead by definition.

This app runs exactly one uvicorn worker (N workers = N schedulers = N x
VRAM — see `SchedulerConfig`'s docstring), so the only process that could
have been running a 'running' job just started. `reap_stale()` fails every
such row and, via `Database.find_generation_by_output_path`, unlinks its file
ONLY if no `generation_history` row anywhere references that exact path —
not by trusting the job row's own `history_id` link, which can be unset by a
narrow shutdown race even after the handler's work (audio + history row) has
genuinely completed.

NO AUTOMATIC RETRIES, EVER. `attempt` is recorded, never re-queued — silently
re-running a 21-minute generation is the same species of surprise as a silent
routing fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..exceptions import (
    AppError,
    JobExpiredError,
    JobInterruptedError,
    JobNotCancellableError,
    JobNotFoundError,
    JobQueueFullError,
)
from .handlers import HANDLERS, JobContext
from .types import JobHandler, JobKind, JobRecord, JobStatus, job_record_from_row

if TYPE_CHECKING:
    from ..config import Settings
    from ..db import Database
    from ..inference.analyzer_scheduler import AnalyzerScheduler
    from ..inference.catalog import ModelCatalog
    from ..inference.protocol import SchedulerProtocol
    from ..inference.transliterator_scheduler import TransliteratorScheduler

__all__ = ["JobRunner"]

logger = logging.getLogger(__name__)


class JobRunner:
    """
    Owns one asyncio pool per `JobKind`. `SYNTHESIZE` defaults to concurrency
    1, matching the scheduler's single GPU slot — a second concurrent synth
    task would just block holding an admission permit while making eviction
    thrash between two models possible.
    """

    def __init__(
        self,
        db: Database,
        scheduler: SchedulerProtocol,
        catalog: ModelCatalog,
        settings: Settings,
        *,
        analyzer: AnalyzerScheduler | None = None,
        transliterator: TransliteratorScheduler | None = None,
        concurrency: Mapping[JobKind, int] | None = None,
        poll_interval_sec: float = 0.25,
    ) -> None:
        self._db = db
        self._scheduler = scheduler
        self._catalog = catalog
        self._settings = settings
        #: The Qwen analyzer's own scheduler — optional so every existing
        #: caller (tests that only ever enqueue SYNTHESIZE jobs, in
        #: particular) keeps working unchanged. `None` is only a problem if
        #: an ANALYZE_LLM job is actually claimed with no analyzer wired in,
        #: which `main.py` never does in production.
        self._analyzer = analyzer
        #: The Gemma transliterator's scheduler. Optional for the same reason
        #: as the analyzer: a deployment without `.venv-gemma` runs every other
        #: kind, and a TRANSLITERATE job claimed with none wired fails with an
        #: error naming the setting instead of the app refusing to boot.
        self._transliterator = transliterator
        self._poll_interval_sec = poll_interval_sec

        self._concurrency: dict[JobKind, int] = dict(
            concurrency
            or {
                JobKind.SYNTHESIZE: max(1, settings.job_concurrency),
                # One fixed model, one worker subprocess (see
                # `AnalyzerScheduler`'s docstring for why this is not the
                # audio scheduler's multi-model eviction problem) — a single
                # concurrent classification matches that shape.
                JobKind.ANALYZE_LLM: 1,
                # 1, and it could not be anything else: a conversion holds the
                # audio scheduler's GPU slot while loading, so a second concurrent one
                # would simply queue behind the first having already paid to
                # evict every audio model.
                JobKind.TRANSLITERATE: 1,
            }
        )
        #: Wakes a parked worker loop the moment `enqueue()` inserts a row of
        #: its kind, so the common case doesn't wait out `poll_interval_sec`.
        #: One event per kind, one worker per kind by default (see the class
        #: docstring) — a kind ever configured with concurrency > 1 would need
        #: its own wake-fanout; not needed while SYNTHESIZE is the only kind.
        self._wake: dict[JobKind, asyncio.Event] = {k: asyncio.Event() for k in self._concurrency}
        #: In-memory "queued or running" counts, maintained ONLY by this
        #: runner (every write to the table that changes queued/running state
        #: goes through `enqueue`, `_run_one`, or `cancel`) — so `wait_idle()`
        #: is a pure asyncio.Event wait with no polling sleep, no DB query.
        self._pending: dict[JobKind, int] = dict.fromkeys(self._concurrency, 0)
        self._idle: dict[JobKind, asyncio.Event] = {k: asyncio.Event() for k in self._concurrency}
        for event in self._idle.values():
            event.set()

        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def reap_stale(self) -> None:
        """Run once, after `db.connect()` and before `start()`. See the class docstring."""
        reaped = await self._db.reap_running(
            error_code=JobInterruptedError.code,
            error_title=JobInterruptedError.title,
            error_status=JobInterruptedError.http_status,
            error_detail=JobInterruptedError().detail,
        )
        for row in reaped:
            await self._maybe_unlink_orphan(job_record_from_row(row))

        expired = await self._db.expire_queued(
            max_age_sec=self._settings.job_queue_max_age_sec,
            error_code=JobExpiredError.code,
            error_title=JobExpiredError.title,
            error_status=JobExpiredError.http_status,
            error_detail=JobExpiredError().detail,
        )
        deleted = await self._db.delete_jobs_older_than(
            retention_hours=self._settings.job_retention_hours
        )
        if reaped or expired or deleted:
            logger.info(
                "job queue startup: reaped=%d expired=%d retention-deleted=%d",
                len(reaped), expired, deleted,
            )
        await self._sync_pending()

    async def _maybe_unlink_orphan(self, job: JobRecord) -> None:
        # Kind-agnostic on purpose: any future file-producing job kind that
        # stores its output path under the same `output_path` params key gets
        # this cleanup for free, no runner change required.
        output_path = job.params.get("output_path")
        if not output_path:
            return
        if await self._db.find_generation_by_output_path(str(output_path)) is not None:
            return  # a real history row references this file — never touch it
        with contextlib.suppress(OSError):
            Path(str(output_path)).unlink(missing_ok=True)  # noqa: ASYNC240 (one-shot unlink)

    async def _sync_pending(self) -> None:
        """One-time reconciliation against the DB — called at boot only (after
        `reap_stale`, and again defensively at `start`), never in the hot
        claim loop. Keeps `wait_idle()` free of any DB query or sleep."""
        for kind in self._concurrency:
            n = await self._db.count_pending(kind.value)
            self._pending[kind] = n
            (self._idle[kind].set if n == 0 else self._idle[kind].clear)()

    async def start(self) -> None:
        await self._sync_pending()
        for kind, n in self._concurrency.items():
            if kind not in HANDLERS:
                continue  # no handler registered yet for this kind; nothing to run
            for _ in range(max(1, n)):
                self._tasks.append(asyncio.create_task(self._worker_loop(kind)))

    async def stop(self, *, drain_timeout_sec: float) -> None:
        """
        Wait up to `drain_timeout_sec` for in-flight work, then cancel.

        Never marks an in-flight row 'failed' here — the disk isn't
        trustworthy mid-shutdown. The startup reaper decides next boot, with
        the file (or its absence) in front of it.
        """
        self._stopping = True
        for event in self._wake.values():
            event.set()  # wake parked loops so they observe _stopping promptly
        if not self._tasks:
            return
        _done, pending = await asyncio.wait(self._tasks, timeout=drain_timeout_sec)
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    async def wait_idle(self) -> None:
        """Test seam: resolves once no tracked kind has a queued or running job."""
        await asyncio.gather(*(e.wait() for e in self._idle.values()))

    # ── enqueue / cancel ─────────────────────────────────────────────────────

    async def enqueue(
        self,
        kind: JobKind,
        *,
        params: dict[str, Any],
        route: dict[str, Any] | None,
        profile_id: int | None,
        priority: int = 0,
        retry_of_job_id: int | None = None,
    ) -> JobRecord:
        """
        Write the job row and return it, still 'queued'. Backpressure is queue
        DEPTH (`job_queue_limit`), not the scheduler's in-memory admission
        semaphore — once this runner is the only caller of `synthesize()`,
        that semaphore is structurally uncontended, so the meaningful bound
        moved here, to a much larger, durable limit on the durable table.

        Still accepted while `stop()` is draining: the row just sits 'queued'
        for the next boot, which is simpler and strictly better than
        rejecting a request the server is about to restart anyway.
        """
        depth = await self._db.count_pending(kind.value)
        if depth >= self._settings.job_queue_limit:
            raise JobQueueFullError(limit=self._settings.job_queue_limit, depth=depth)

        row = await self._db.create_job(
            kind=kind.value,
            params_json=json.dumps(params),
            route_json=json.dumps(route) if route is not None else None,
            profile_id=profile_id,
            priority=priority,
            retry_of_job_id=retry_of_job_id,
        )
        self._pending[kind] = self._pending.get(kind, 0) + 1
        self._idle[kind].clear()
        if kind in self._wake:
            self._wake[kind].set()
        return job_record_from_row(row)

    async def cancel(self, job_id: int) -> JobRecord:
        """Cancel a still-'queued' job. Raises JobNotFoundError / JobNotCancellableError otherwise.

        The status check and the cancel attempt cannot be one atomic step:
        the pool may claim the job between them. If that happens the caller
        re-reads the row's now-current status rather than trusting the stale
        read above.
        """
        current = await self._db.get_job(job_id)
        if current is None:
            raise JobNotFoundError(job_id)
        current_record = job_record_from_row(current)
        if current_record.status is not JobStatus.QUEUED:
            raise JobNotCancellableError(job_id, current_record.status.value)

        row = await self._db.cancel_queued_job(job_id)
        if row is None:
            # Lost the race: the pool claimed it between our read and the
            # cancel attempt. Re-read to report the NOW-current status.
            latest = await self._db.get_job(job_id)
            status = job_record_from_row(latest).status.value if latest else "unknown"
            raise JobNotCancellableError(job_id, status)

        record = job_record_from_row(row)
        self._pending[record.kind] = max(0, self._pending.get(record.kind, 0) - 1)
        if self._pending[record.kind] == 0:
            self._idle[record.kind].set()
        return record

    # ── worker loop ──────────────────────────────────────────────────────────

    async def _worker_loop(self, kind: JobKind) -> None:
        handler = HANDLERS[kind]
        wake = self._wake[kind]
        while not self._stopping:
            row = await self._db.claim_next_job(kind.value)
            if row is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), timeout=self._poll_interval_sec)
                wake.clear()
                continue
            job = job_record_from_row(row)
            await self._run_one(kind, job, handler)

    async def _run_one(self, kind: JobKind, job: JobRecord, handler: JobHandler) -> None:
        ctx = JobContext(
            db=self._db,
            scheduler=self._scheduler,
            catalog=self._catalog,
            settings=self._settings,
            analyzer=self._analyzer,
            transliterator=self._transliterator,
        )
        try:
            outcome = await handler(ctx, job)
        except AppError as exc:
            await self._db.fail_job(
                job.id,
                error_code=exc.code,
                error_title=exc.title,
                error_status=exc.http_status,
                error_detail=exc.detail,
                error_extensions_json=json.dumps(exc.extensions),
            )
        except Exception:
            # Never store raw exception text — a torch stack trace or
            # filesystem path must not reach a client. Log it here instead,
            # matching the generic-exception handler at api/errors.py.
            logger.exception("job %s (%s) failed with an unexpected error", job.id, kind.value)
            await self._db.fail_job(
                job.id,
                error_code="INTERNAL_ERROR",
                error_title="Internal error",
                error_status=500,
                error_detail="An unexpected error occurred.",
                error_extensions_json="{}",
            )
        else:
            await self._db.finish_job(
                job.id,
                history_id=outcome.history_id,
                result_json=json.dumps(outcome.result) if outcome.result is not None else None,
            )
        finally:
            self._pending[kind] = max(0, self._pending.get(kind, 0) - 1)
            if self._pending[kind] == 0:
                self._idle[kind].set()
