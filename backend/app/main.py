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
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.deps import ApiKeyMiddleware
from .api.errors import install_exception_handlers
from .api.routers import (
    direction,
    health,
    history,
    jobs,
    media,
    models,
    pronunciations,
    system,
    text,
    transcript,
    tts,
    voice,
)
from .config import Settings, get_settings
from .db import Database
from .inference.catalog import CATALOG
from .inference.protocol import SchedulerProtocol
from .jobs import JobRunner

if TYPE_CHECKING:
    from .inference.analyzer_scheduler import AnalyzerScheduler

__all__ = ["create_app"]

logger = logging.getLogger(__name__)


#: Short and boring on purpose: this is thrown away, and a long one would hold
#: startup open for no benefit.
_WARM_SYNTH_TEXT = "سلام، یہ ایک آواز کی جانچ ہے۔"


async def _warm_synth(app: FastAPI, settings: Settings, model_id: str) -> None:
    """
    One throwaway synthesis, immediately deleted.

    Loading weights is not enough for OmniVoice: it lazily loads an embedded
    Whisper sub-model on the FIRST `synth()` call when no `ref_text` is given
    (measured in the Phase 1 pod smoke test), so without this the first real
    generation still stalls for tens of seconds.

    Golden rule 1 is not in play — this audio goes to a temp file, is deleted in
    a `finally`, and never reaches a user, a `generation_history` row or a
    `jobs` row.

    A synthesis needs a reference clip and a warm-up cannot invent one, so with
    no voice profiles this logs and returns rather than guessing.
    """
    from .inference.protocol import SynthRequest

    profiles = await app.state.db.list_profiles()
    if not profiles:
        logger.info(
            "skipping warm synth for %r: no voice profiles to use as a reference", model_id
        )
        return

    out_path = Path(settings.data_dir) / "tmp" / f"warm-{model_id}.wav"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        await app.state.scheduler.synthesize(
            SynthRequest(
                model_id=model_id,
                text=_WARM_SYNTH_TEXT,
                reference_audio=Path(profiles[0]["audio_path"]),
                output_path=out_path,
                reference_text=profiles[0]["transcript"],
                sample_rate=settings.default_sample_rate,
            )
        )
        logger.info("warm synth for %r done; first real generation will be fast", model_id)
    except Exception:
        # Never fatal. A model that cannot warm-synth still works on a real
        # request, which is where that error belongs.
        logger.exception("warm synth for %r failed; continuing", model_id)
    finally:
        with contextlib.suppress(OSError):
            out_path.unlink(missing_ok=True)


def create_app(
    *,
    scheduler: SchedulerProtocol | None = None,
    analyzer: AnalyzerScheduler | None = None,
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
        if getattr(app.state, "analyzer", None) is None:
            app.state.analyzer = _build_analyzer(settings)
            app.state.owns_analyzer = True
        # THE TRANSLITERATOR IS OPTIONAL, AND ITS ABSENCE IS EXPLAINED RATHER
        # THAN CRASHED ON. It is now RESIDENT (~19 GB), so unlike every other
        # component here it can be genuinely unaffordable on a given card.
        # Two ways it ends up unavailable, and both leave the rest of the app
        # completely intact:
        #
        #   no VCS_GEMMA_TRANSLITERATOR_PYTHON  -> nothing was provisioned
        #   the card is too small               -> check_capacity says so
        #
        # In both cases `app.state.transliterator` is None and
        # `app.state.transliterator_reason` carries a sentence the UI renders
        # next to a disabled Convert button. Starting up and then refusing one
        # feature is the honest behaviour; refusing to start at all would take
        # voice enrollment, Speech Direction and every generation down with it
        # over a feature the deployment may never use.
        # `hasattr`, not `is None`: a test (or a deployment) may inject None
        # DELIBERATELY to mean "there is no transliterator here, and this is
        # the reason". `is None` cannot tell that apart from "nobody set it",
        # and would overwrite the injected reason with a computed one.
        if not hasattr(app.state, "transliterator"):
            app.state.transliterator, app.state.transliterator_reason = (
                _build_transliterator(settings, app.state.scheduler)
            )
            app.state.owns_transliterator = app.state.transliterator is not None
        if getattr(app.state, "db", None) is None:
            app.state.db = Database(settings.db_path)
            await app.state.db.connect()
            app.state.owns_db = True
        # The job queue always wraps whichever db/scheduler/analyzer ended up
        # above — real or fake, injected or built — so the API test suite
        # gets a real JobRunner driving a real Database against
        # FakeScheduler, same as every other dependency here. See
        # app/jobs/__init__.py.
        if getattr(app.state, "jobs", None) is None:
            app.state.jobs = JobRunner(
                app.state.db, app.state.scheduler, CATALOG, settings,
                analyzer=app.state.analyzer,
                transliterator=app.state.transliterator,
            )
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
        if settings.warm_model_ids:
            model_ids = settings.warm_model_ids

            async def _warm() -> None:
                # Sequential, not gathered: `warm()` takes the scheduler's GPU
                # slot, so concurrent warms would serialize anyway — and in
                # order means a failure names the model that failed.
                for model_id in model_ids:
                    try:
                        await app.state.scheduler.warm(model_id)
                    except Exception:
                        logger.exception(
                            "startup warm of %r failed; will retry on first request",
                            model_id,
                        )
                        continue
                    if settings.warm_synth_on_startup:
                        await _warm_synth(app, settings, model_id)

            warm_task = asyncio.create_task(_warm())
            app.state.warm_task = warm_task

        # SEPARATE TASK, NOT APPENDED TO THE AUDIO WARM — and the difference is
        # not tidiness. `warm_on_startup` is unset on a default deployment, so
        # folding this into the block above meant Gemma silently never warmed,
        # and every first conversion paid the full 150-330 s load. Caught on the
        # pod: the API was up and answering with VRAM sitting at 0.
        #
        # They are independent concerns anyway. Audio warming is a nicety (a
        # cold audio model costs ~20-60 s); the transliterator is the one that
        # costs five minutes, so if anything it wants warming MORE — not only
        # when someone happened to also list audio models.
        translit_task: asyncio.Task[None] | None = None
        if getattr(app.state, "transliterator", None) is not None:
            translit_task = asyncio.create_task(_warm_transliterator(app))
            app.state.transliterator_warm_task = translit_task

        try:
            yield
        finally:
            for task in (warm_task, translit_task):
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            if getattr(app.state, "owns_jobs", False):
                # Never mark in-flight rows failed here — the disk isn't
                # trustworthy mid-shutdown. The next boot's reap_stale
                # decides, with the file (or its absence) in front of it.
                await app.state.jobs.stop(drain_timeout_sec=settings.job_drain_timeout_sec)
            if getattr(app.state, "owns_scheduler", False):
                await app.state.scheduler.shutdown()
            if getattr(app.state, "owns_analyzer", False):
                await app.state.analyzer.shutdown()
            # Only what we built. It now holds a ~19 GB worker that must not
            # outlive the app — but a test-injected double is the caller's, the
            # same rule the scheduler, db and analyzer above all follow.
            if getattr(app.state, "owns_transliterator", False):
                await app.state.transliterator.shutdown()
            if getattr(app.state, "owns_db", False):
                await app.state.db.close()

    app = FastAPI(title="AI Voice Clone Studio", version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    if scheduler is not None:
        app.state.scheduler = scheduler
        app.state.owns_scheduler = False
    if analyzer is not None:
        app.state.analyzer = analyzer
        app.state.owns_analyzer = False
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
    app.include_router(direction.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(media.router, prefix="/api")
    app.include_router(history.router, prefix="/api")
    app.include_router(pronunciations.router, prefix="/api")
    app.include_router(text.router, prefix="/api")
    app.include_router(transcript.router, prefix="/api")
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


def _build_analyzer(settings: Settings) -> AnalyzerScheduler:
    """Construct the real AnalyzerScheduler. Imported lazily, same reason as
    `_build_scheduler`: a fake-injected app (and its tests) never even
    imports the analyzer scheduler implementation."""
    from .inference.analyzer_scheduler import AnalyzerScheduler

    env = dict(os.environ)
    return AnalyzerScheduler(
        python_executable=settings.qwen_analyzer_python,
        env=env,
        cwd=settings.worker_cwd,
        idle_unload_sec=settings.qwen_analyzer_idle_unload_sec,
    )


def _build_transliterator(settings: Settings, scheduler):
    """
    Build the real `TransliteratorScheduler`, or explain why not.

    Returns `(scheduler_or_None, reason_or_None)`. A `None` scheduler is a
    normal, supported state — the app runs without script conversion and says
    why, rather than failing to start over one optional feature.

    Two refusals, both surfaced verbatim to the user:

      1. **No interpreter.** `.venv-gemma` was never provisioned. The message
         names the env var, because the fix is a deployment change and a bare
         "unavailable" sends someone to look at the model instead.
      2. **The card is too small.** Since 2026-08-17 Gemma is RESIDENT rather
         than loaded per call, so it permanently occupies ~19.2 GB alongside
         the audio budget and the analyzer. `check_capacity` adds those up.
         An unknown card (no `nvidia-smi`) counts as "fits" — a machine that
         cannot be measured must not have a feature disabled on a guess.

    It takes the audio `scheduler` because loading holds that scheduler's GPU
    slot (`reserve_slot`), so nothing can start a synthesis into a 19 GB
    allocation spike. It has to be the SAME slot, not a second one that knows
    nothing about the first.
    """
    if not settings.gemma_transliterator_python:
        return None, (
            "Script conversion is not set up on this server. It needs the "
            "Gemma environment (VCS_GEMMA_TRANSLITERATOR_PYTHON and "
            ".venv-gemma). Everything else works without it."
        )

    from .inference.capacity import check_capacity

    report = check_capacity(
        budget_mb=settings.budget_mb,
        transliterator_reserve_mb=settings.gemma_transliterator_reserve_mb,
        analyzer_reserve_mb=(
            settings.qwen_analyzer_reserve_mb if settings.qwen_analyzer_python else 0
        ),
    )
    if not report.fits:
        logger.warning("script conversion disabled: %s", report.reason())
        return None, report.reason()

    from .inference.transliterator_scheduler import TransliteratorScheduler

    return TransliteratorScheduler(
        python_executable=settings.gemma_transliterator_python,
        inference_scheduler=scheduler,
        env=dict(os.environ),
        cwd=settings.worker_cwd,
        idle_unload_sec=settings.gemma_transliterator_idle_unload_sec,
    ), None


async def _warm_transliterator(app: FastAPI) -> None:
    """
    Load Gemma at startup, so the first conversion costs ~5 s and not ~330 s.

    Backgrounded with the audio warms and never fatal: a failed warm leaves the
    scheduler to load lazily on the first request, which is exactly the
    behaviour before this existed. Warming is a latency optimisation, not a
    correctness one.
    """
    transliterator = getattr(app.state, "transliterator", None)
    if transliterator is None:
        return
    try:
        sec = await transliterator.warm()
        logger.info("script conversion warmed in %.0fs", sec)
    except Exception:
        logger.exception("startup warm of the transliterator failed; will load on first use")


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
