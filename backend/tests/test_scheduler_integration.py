"""
Integration test: the REAL scheduler driving a REAL worker subprocess.

The unit suites cover each half separately — `test_scheduler` runs the scheduler
against `FakeWorker`, `test_worker_client` runs `WorkerProcess` against a real
subprocess by hand. This closes the seam between them: `InferenceScheduler` ->
`make_worker_factory` -> `WorkerProcess` -> a real `app.inference.worker`
subprocess (the gated `fake` backend, so no torch and no GPU). If the wire
protocol, residency tracking, or the GPU-slot handshake were subtly wrong, only
this test would catch it.
"""

from __future__ import annotations

import asyncio
import os
import sys
import wave
from pathlib import Path

from app.inference.catalog import build_catalog
from app.inference.factory import make_worker_factory
from app.inference.protocol import SynthRequest
from app.inference.scheduler import InferenceScheduler, SchedulerConfig
from app.inference.spec import License, ModelSpec, ModelState, RuntimeKind

BACKEND_DIR = Path(__file__).resolve().parents[1]

if sys.platform == "win32" and sys.version_info < (3, 14):  # create_subprocess_exec needs the Proactor loop
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

FAKE_SPEC = ModelSpec(
    id="fake", display_name="Fake", runtime=RuntimeKind.FAKE, license=License.MIT,
    hf_repo="x/y", hf_revision="a" * 40, languages=(), vram_mb=4000, est_load_sec=1.0,
)
CATALOG = build_catalog((FAKE_SPEC,))


def _scheduler() -> InferenceScheduler:
    factory = make_worker_factory(
        interpreters={RuntimeKind.FAKE: sys.executable},
        env=dict(os.environ, VCS_ALLOW_FAKE_RUNTIME="1"),
        cwd=BACKEND_DIR,
    )
    return InferenceScheduler(
        CATALOG, factory, SchedulerConfig(budget_mb=8000, max_workers=1)
    )


async def test_end_to_end_synthesis_through_real_worker(tmp_path: Path) -> None:
    sched = _scheduler()
    try:
        out = tmp_path / "out.wav"
        result = await sched.synthesize(
            SynthRequest(
                model_id="fake", text="hello world",
                reference_audio=tmp_path / "ref.wav", output_path=out,
                params={"dur_sec": 0.4}, sample_rate=24000,
            )
        )
        # The scheduler asserts the rendering spec matches the plan — proof no
        # silent fallback happened across the process boundary.
        assert result.model_id == "fake"
        assert result.sample_rate == 24000
        assert out.exists()
        with wave.open(str(out), "rb") as f:
            assert f.getframerate() == 24000

        # Residency is visible without touching the worker again.
        statuses = {s.spec.id: s.state for s in await sched.status()}
        assert statuses["fake"] is ModelState.RESIDENT
    finally:
        await sched.shutdown()


async def test_warm_then_resident(tmp_path: Path) -> None:
    sched = _scheduler()
    try:
        await sched.warm("fake")
        statuses = {s.spec.id: s.state for s in await sched.status()}
        assert statuses["fake"] is ModelState.RESIDENT
    finally:
        await sched.shutdown()


async def test_shutdown_is_idempotent() -> None:
    sched = _scheduler()
    await sched.warm("fake")
    await sched.shutdown()
    await sched.shutdown()  # second shutdown must not raise
