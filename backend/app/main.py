"""
AI Voice Clone Studio — API application.

CPU-ONLY. `import torch` must not be reachable from here — this module and
everything it imports is scanned by `test_no_torch_outside_runtimes`. The GPU
lives in worker subprocesses the scheduler spawns; this process only talks to
them over the wire protocol.

`create_app` takes an optional `scheduler` so the whole HTTP surface can be
tested against `FakeScheduler` with no torch and no subprocess.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.deps import ApiKeyMiddleware
from .api.errors import install_exception_handlers
from .api.routers import health, history, jobs, media, models, system, tts, voice
from .config import Settings, get_settings
from .db import Database
from .inference.catalog import CATALOG
from .inference.protocol import SchedulerProtocol
from .jobs import JobRunner

__all__ = ["create_app"]

logger = logging.getLogger(__name__)


def create_app(
    *,
    scheduler: SchedulerProtocol | None = None,
    db: Database | None = None,
    settings: Settings | None = None,
    job_runner: JobRunner | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_dirs()
        # Build the real scheduler/db only if not injected (tests inject fakes /
        # an in-memory db). We tear down only what we own.
        if getattr(app.state, "scheduler", None) is None:
            app.state.scheduler = _build_scheduler(settings)
            app.state.owns_scheduler = True
        if getattr(app.state, "db", None) is None:
            app.state.db = Database(settings.db_path)
            await app.state.db.connect()
            app.state.owns_db = True
        # The job queue always wraps whichever db/scheduler ended up above —
        # real or fake, injected or built — so the API test suite gets a real
        # JobRunner driving a real Database against FakeScheduler, same as
        # every other dependency here. See app/jobs/__init__.py.
        if getattr(app.state, "jobs", None) is None:
            app.state.jobs = JobRunner(app.state.db, app.state.scheduler, CATALOG, settings)
            app.state.owns_jobs = True
        if getattr(app.state, "owns_jobs", False):
            # A 'running' row found now is dead by definition — see
            # JobRunner.reap_stale. Only for a runner built HERE: an injected
            # one (owns_jobs=False) is the caller's to start/stop, same as an
            # injected scheduler or db.
            await app.state.jobs.reap_stale()
            await app.state.jobs.start()

        # Fire-and-forget: kicks off the ~20-60s cold load immediately instead of
        # waiting for the first /generate to pay it. Backgrounded rather than
        # awaited so /api/health answers right away — a slow load is not "the
        # app is broken", the UI's model-status chip already reports COLD/WARM.
        # Stashed on app.state (not just a local) so tests can await it directly
        # instead of racing the event loop to observe it having run.
        warm_task: asyncio.Task[None] | None = None
        if settings.warm_on_startup:
            model_id = settings.warm_on_startup

            async def _warm() -> None:
                try:
                    await app.state.scheduler.warm(model_id)
                except Exception:
                    logger.exception("startup warm of %r failed; will retry on first request", model_id)

            warm_task = asyncio.create_task(_warm())
            app.state.warm_task = warm_task

        try:
            yield
        finally:
            if warm_task is not None and not warm_task.done():
                warm_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await warm_task
            if getattr(app.state, "owns_jobs", False):
                # Never mark in-flight rows failed here — the disk isn't
                # trustworthy mid-shutdown. The next boot's reap_stale
                # decides, with the file (or its absence) in front of it.
                await app.state.jobs.stop(drain_timeout_sec=settings.job_drain_timeout_sec)
            if getattr(app.state, "owns_scheduler", False):
                await app.state.scheduler.shutdown()
            if getattr(app.state, "owns_db", False):
                await app.state.db.close()

    app = FastAPI(title="AI Voice Clone Studio", version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    if scheduler is not None:
        app.state.scheduler = scheduler
        app.state.owns_scheduler = False
    if db is not None:
        app.state.db = db
        app.state.owns_db = False
    if job_runner is not None:
        app.state.jobs = job_runner
        app.state.owns_jobs = False

    install_exception_handlers(app)

    app.include_router(health.router, prefix="/api")
    app.include_router(models.router, prefix="/api")
    app.include_router(models.languages_router, prefix="/api")
    app.include_router(system.router, prefix="/api")
    app.include_router(voice.router, prefix="/api")
    app.include_router(tts.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(media.router, prefix="/api")
    app.include_router(history.router, prefix="/api")
    _assert_no_duplicate_routes(app)

    # Middleware order is load-bearing. add_middleware makes the LAST-added the
    # OUTERMOST, so: API-key added FIRST (innermost), CORS added LAST (outermost).
    # CORS outermost answers preflight OPTIONS before the API-key check can 403 it
    # — the exact bug that failed every cross-origin preflight once a key was set.
    app.add_middleware(ApiKeyMiddleware, api_key=settings.api_key)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # No cookies or HTTP auth are used — the client authenticates with an
        # X-API-Key header, which needs no credentials mode. Leaving this on
        # bought nothing and changed how a wildcard behaves: Starlette answers
        # wildcard+credentials by echoing the requesting Origin, which allows
        # every site individually. Off is both accurate and safer.
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


def _build_scheduler(settings: Settings) -> SchedulerProtocol:
    """Construct the real InferenceScheduler. Imported lazily so a fake-injected
    app (and its tests) never even imports the scheduler implementation."""
    from .inference.catalog import CATALOG
    from .inference.factory import make_worker_factory
    from .inference.scheduler import InferenceScheduler, SchedulerConfig

    env = dict(os.environ)
    if settings.allow_fake_runtime:
        env["VCS_ALLOW_FAKE_RUNTIME"] = "1"
    factory = make_worker_factory(
        interpreters=settings.interpreters(), env=env, cwd=settings.worker_cwd
    )
    return InferenceScheduler(
        CATALOG, factory,
        SchedulerConfig(budget_mb=settings.budget_mb, max_workers=settings.max_workers),
    )


def _assert_no_duplicate_routes(app: FastAPI) -> None:
    """A duplicate (method, path) means one route silently shadows another — the
    predecessor's models endpoints were unreachable exactly this way."""
    seen: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None) or ()
        path = getattr(route, "path", "")
        if not methods or not path:
            continue  # skip Starlette internals (mounts, method-less routes)
        for method in methods:
            key = (method, path)
            assert key not in seen, f"duplicate route registered: {key}"
            seen.add(key)


#: Module-level ASGI app for `uvicorn app.main:app`. Construction only wires
#: routes/middleware; the scheduler and db open in the lifespan at startup, so
#: importing this touches neither the GPU nor the filesystem.
app = create_app()
