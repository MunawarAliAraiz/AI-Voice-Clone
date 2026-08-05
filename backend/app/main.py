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

import contextlib
import os
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.deps import ApiKeyMiddleware
from .api.errors import install_exception_handlers
from .api.routers import health, models, system, voice
from .config import Settings, get_settings
from .db import Database
from .inference.protocol import SchedulerProtocol

__all__ = ["create_app"]


def create_app(
    *,
    scheduler: SchedulerProtocol | None = None,
    db: Database | None = None,
    settings: Settings | None = None,
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
        try:
            yield
        finally:
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

    install_exception_handlers(app)

    app.include_router(health.router, prefix="/api")
    app.include_router(models.router, prefix="/api")
    app.include_router(models.languages_router, prefix="/api")
    app.include_router(system.router, prefix="/api")
    app.include_router(voice.router, prefix="/api")
    _assert_no_duplicate_routes(app)

    # Middleware order is load-bearing. add_middleware makes the LAST-added the
    # OUTERMOST, so: API-key added FIRST (innermost), CORS added LAST (outermost).
    # CORS outermost answers preflight OPTIONS before the API-key check can 403 it
    # — the exact bug that failed every cross-origin preflight once a key was set.
    app.add_middleware(ApiKeyMiddleware, api_key=settings.api_key)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
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
