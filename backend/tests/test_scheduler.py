"""
Scheduler tests — the highest-value tests in the suite, and NONE needs a GPU.

That is the payoff of the process-isolation design: `FakeWorker` sits behind the
same protocol a real subprocess does, so concurrency, eviction, and the GPU-slot
invariant are all exercised on a CPU-only machine in milliseconds.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from app.exceptions import ModelNotFoundError, QueueFullError, VRAMExhaustedError
from app.inference.catalog import build_catalog
from app.inference.protocol import SynthRequest, WireOp
from app.inference.scheduler import InferenceScheduler, SchedulerConfig
from app.inference.spec import License, ModelSpec, ModelState, RuntimeKind
from tests.fakes import FakeWorker


def _spec(spec_id: str, runtime: RuntimeKind, vram: int = 4000) -> ModelSpec:
    return ModelSpec(
        id=spec_id, display_name=spec_id, runtime=runtime, license=License.MIT,
        hf_repo="x/y", hf_revision="a" * 40, languages=(),
        vram_mb=vram, est_load_sec=10.0,
    )


A = _spec("a", RuntimeKind.F5, 6000)
B = _spec("b", RuntimeKind.CHATTERBOX, 6000)
C = _spec("c", RuntimeKind.VOXCPM, 6000)
#: Same runtime as A — loading it is a checkpoint SWAP, not a process start.
A2 = _spec("a2", RuntimeKind.F5, 6000)

CATALOG = build_catalog((A, A2, B, C))
#: Budget fits 2 of 3 workers, so eviction is exercised constantly. Admission is
#: generous by default — `test_queue_bounded` narrows it deliberately.
CONFIG = SchedulerConfig(budget_mb=13_000, max_workers=2, admission_limit=32)


def _make_scheduler(
    config: SchedulerConfig | None = None, **kw
) -> tuple[InferenceScheduler, dict[RuntimeKind, FakeWorker]]:
    """Scheduler wired to FakeWorkers, plus a handle on every worker created."""
    made: dict[RuntimeKind, FakeWorker] = {}

    async def factory(runtime: RuntimeKind) -> FakeWorker:
        worker = FakeWorker(runtime.value, **kw)
        await worker.start()
        made[runtime] = worker
        return worker

    return InferenceScheduler(CATALOG, factory, config or CONFIG), made


def _req(model_id: str, tmp: Path, text: str = "hello there") -> SynthRequest:
    return SynthRequest(
        model_id=model_id, text=text,
        reference_audio=tmp / "ref.wav", output_path=tmp / f"{model_id}.wav",
    )


# ── The five that matter ─────────────────────────────────────────────────────


async def test_no_unload_during_inference(tmp_path: Path) -> None:
    """
    THE use-after-free. In the predecessor's ONE_ACTIVE_MODEL mode, request B
    could unload_model() while request A was mid-inference — a use-after-free on
    CUDA memory that presents as a random illegal-access crash.

    Here, forcing constant eviction (3 runtimes, room for 2) must never overlap
    a kill with a live synth.
    """
    sched, made = _make_scheduler(load_delay_sec=0.01, synth_delay_sec=0.02)
    await asyncio.gather(*[
        sched.synthesize(_req(m, tmp_path))
        for m in ("a", "b", "c", "a", "b", "c", "a")
    ])
    assert made, "no workers were created"
    for worker in made.values():
        assert not worker.unload_during_synth, f"{worker.runtime} evicted mid-inference"
        assert worker.max_concurrent <= 1, "two requests held the GPU at once"


async def test_exclusive_gpu_empties_the_gpu_and_never_overlaps_a_synth(
    tmp_path: Path,
) -> None:
    """
    Phase B's transliterator needs the whole card (~19 GB Gemma vs a 24 GB
    GPU), so `exclusive_gpu()` evicts EVERY worker rather than the LRU few
    `_make_room_for` would.

    This is the second eviction call site in the codebase and the amendment
    to the module docstring's invariant, so the property that matters is
    asserted directly: it holds the same slot, therefore no synthesis can be
    in flight while it evicts, therefore nothing is unloaded mid-inference.
    """
    sched, made = _make_scheduler(load_delay_sec=0.01, synth_delay_sec=0.02)

    inside_exclusive = False
    saw_synth_during_exclusive = False

    async def hammer() -> None:
        nonlocal saw_synth_during_exclusive
        for model in ("a", "b", "c", "a", "b"):
            await sched.synthesize(_req(model, tmp_path))
            if inside_exclusive:
                saw_synth_during_exclusive = True

    async def take_the_gpu() -> None:
        nonlocal inside_exclusive
        await asyncio.sleep(0.03)  # let some synthesis get going first
        async with sched.exclusive_gpu("transliterate"):
            inside_exclusive = True
            assert sched._workers == {}, "exclusive_gpu left a worker resident"
            await asyncio.sleep(0.05)
            inside_exclusive = False

    await asyncio.gather(hammer(), take_the_gpu())

    assert not saw_synth_during_exclusive, "a synthesis completed while the GPU was exclusive"
    for worker in made.values():
        assert not worker.unload_during_synth, f"{worker.runtime} evicted mid-inference"
        assert worker.max_concurrent <= 1, "two requests held the GPU at once"


async def test_synthesis_recovers_after_exclusive_gpu(tmp_path: Path) -> None:
    """
    Eviction is the whole point, so the audio models come back COLD. That is
    the documented price of load-convert-unload, not a broken scheduler —
    the next request must still succeed, paying a fresh load.
    """
    sched, made = _make_scheduler(load_delay_sec=0.01, synth_delay_sec=0.001)
    await sched.synthesize(_req("a", tmp_path))
    first_worker = made[RuntimeKind.F5]

    async with sched.exclusive_gpu("transliterate"):
        assert sched._workers == {}

    await sched.synthesize(_req("a", tmp_path))

    # `made` is keyed by runtime, so the replacement overwrote the original
    # entry — identity is what proves a fresh worker was spawned, not a load
    # count that the overwrite discarded.
    second_worker = made[RuntimeKind.F5]
    assert second_worker is not first_worker, "no new worker was spawned after eviction"
    assert second_worker.load_calls == 1, "the fresh worker did not load the model"
    assert first_worker.kill_calls >= 1, "the evicted worker was never killed"


async def test_no_double_load(tmp_path: Path) -> None:
    """10 concurrent requests for one model must load it exactly once."""
    sched, made = _make_scheduler(load_delay_sec=0.02, synth_delay_sec=0.001)
    await asyncio.gather(*[sched.synthesize(_req("a", tmp_path)) for _ in range(10)])
    assert made[RuntimeKind.F5].load_calls == 1


async def test_queue_bounded(tmp_path: Path) -> None:
    """
    Excess concurrency is REJECTED, not queued unboundedly. A 503 beats an OOM.
    """
    sched, _ = _make_scheduler(
        SchedulerConfig(budget_mb=13_000, max_workers=2, admission_limit=8),
        synth_delay_sec=0.05,
    )
    results = await asyncio.gather(
        *[sched.synthesize(_req("a", tmp_path)) for _ in range(64)],
        return_exceptions=True,
    )
    rejected = [r for r in results if isinstance(r, QueueFullError)]
    assert rejected, "nothing was rejected — the queue is unbounded"
    assert len(rejected) + len([r for r in results if not isinstance(r, Exception)]) == 64
    assert rejected[0].http_status == 503


async def test_event_loop_never_blocks(tmp_path: Path) -> None:
    """
    A 10ms heartbeat must keep ticking throughout. If any scheduler path did
    blocking work on the event loop, the gap would blow past 50ms.
    """
    sched, _ = _make_scheduler(load_delay_sec=0.02, synth_delay_sec=0.02)
    gaps: list[float] = []
    stop = False

    async def heartbeat() -> None:
        last = time.perf_counter()
        while not stop:
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    await asyncio.gather(*[
        sched.synthesize(_req(m, tmp_path)) for m in ("a", "b", "a", "c")
    ])
    stop = True
    await beat

    assert gaps
    assert max(gaps) < 0.05, f"event loop stalled for {max(gaps) * 1000:.0f}ms"


async def test_client_disconnect_does_not_release_slot_early(tmp_path: Path) -> None:
    """
    Cancelling the caller must NOT abandon a running CUDA kernel.

    asyncio.shield means the underlying worker call runs to completion even
    though the awaiting task was cancelled. Without it, the `async with` would
    release the slot while the kernel was still live and the next request would
    enter — the exact race this design eliminates.
    """
    sched, made = _make_scheduler(synth_delay_sec=0.15)
    task = asyncio.create_task(sched.synthesize(_req("a", tmp_path)))
    await asyncio.sleep(0.05)  # let it reach the worker call
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    worker = made[RuntimeKind.F5]
    # Give the shielded call time to finish on its own.
    await asyncio.sleep(0.25)
    assert worker.in_flight == 0, "a kernel was abandoned mid-flight"
    assert not worker.unload_during_synth


# ── Eviction and budget ──────────────────────────────────────────────────────


async def test_evicts_least_recently_used(tmp_path: Path) -> None:
    """Budget fits 2 of 3 workers, so the third must evict the coldest."""
    sched, made = _make_scheduler()
    await sched.synthesize(_req("a", tmp_path))
    await sched.synthesize(_req("b", tmp_path))
    await sched.synthesize(_req("c", tmp_path))   # must evict 'a', the LRU

    assert made[RuntimeKind.F5].kill_calls == 1
    assert made[RuntimeKind.VOXCPM].is_alive


async def test_same_runtime_swaps_checkpoint_without_respawning(tmp_path: Path) -> None:
    """
    A WARM swap costs seconds; a runtime switch costs tens. The scheduler must
    not kill and respawn a process just to change checkpoint.
    """
    sched, made = _make_scheduler()
    await sched.synthesize(_req("a", tmp_path))
    await sched.synthesize(_req("a2", tmp_path))

    worker = made[RuntimeKind.F5]
    assert worker.kill_calls == 0, "respawned instead of swapping"
    assert worker.load_calls == 2
    assert worker.loaded_model_id == "a2"


async def test_spec_larger_than_budget_raises(tmp_path: Path) -> None:
    big = _spec("big", RuntimeKind.F5, 99_000)
    sched = InferenceScheduler(
        build_catalog((big,)),
        lambda r: _unused(),  # never reached
        SchedulerConfig(budget_mb=8000, max_workers=2),
    )
    with pytest.raises(VRAMExhaustedError):
        await sched.synthesize(_req("big", tmp_path))


async def _unused():  # pragma: no cover - factory must never be called
    raise AssertionError("worker factory should not run")


async def test_unknown_model_raises(tmp_path: Path) -> None:
    sched, _ = _make_scheduler()
    with pytest.raises(ModelNotFoundError):
        await sched.synthesize(_req("nope", tmp_path))


# ── Status and shutdown ──────────────────────────────────────────────────────


async def test_status_reports_residency_without_taking_the_slot(tmp_path: Path) -> None:
    """
    The UI polls status. If it queued behind a 60s generation the UI would look
    hung, so status must never touch the slot.
    """
    sched, _ = _make_scheduler(synth_delay_sec=0.2)
    task = asyncio.create_task(sched.synthesize(_req("a", tmp_path)))
    await asyncio.sleep(0.05)

    started = time.perf_counter()
    statuses = await sched.status()
    assert time.perf_counter() - started < 0.05, "status blocked on the GPU slot"
    assert len(statuses) == len(CATALOG.specs)
    await task

    by_id = {s.spec.id: s for s in await sched.status()}
    assert by_id["a"].state is ModelState.RESIDENT
    assert by_id["a"].est_wait_sec == 0.0
    # Same runtime, different checkpoint -> WARM, cheaper than COLD.
    assert by_id["a2"].state is ModelState.WARM
    assert by_id["b"].state is ModelState.COLD


async def test_warm_preloads(tmp_path: Path) -> None:
    sched, made = _make_scheduler()
    await sched.warm("a")
    assert made[RuntimeKind.F5].loaded_model_id == "a"
    assert (await sched.status())[0].state is ModelState.RESIDENT


async def test_shutdown_kills_every_worker_and_is_idempotent(tmp_path: Path) -> None:
    sched, made = _make_scheduler()
    await sched.synthesize(_req("a", tmp_path))
    await sched.synthesize(_req("b", tmp_path))
    await sched.shutdown()
    await sched.shutdown()
    assert all(not w.is_alive for w in made.values())


async def test_dead_worker_is_replaced_not_reused(tmp_path: Path) -> None:
    """A crashed worker must not linger in the budget as if it held VRAM."""
    sched, made = _make_scheduler()
    await sched.synthesize(_req("a", tmp_path))
    made[RuntimeKind.F5]._alive = False  # simulate an out-of-band death

    await sched.synthesize(_req("a", tmp_path))
    assert made[RuntimeKind.F5].is_alive, "a fresh worker should have replaced it"


async def test_worker_error_surfaces_as_app_error(tmp_path: Path) -> None:
    sched, _ = _make_scheduler(crash_on=WireOp.SYNTH)
    from app.exceptions import AppError

    with pytest.raises(AppError):
        await sched.synthesize(_req("a", tmp_path))


async def test_lora_local_path_only_sent_when_the_spec_declares_one(
    tmp_path: Path,
) -> None:
    """
    `_load_into` must add `lora_local_path` to the LOAD payload for a spec
    that carries one and omit the key entirely for one that doesn't (every
    catalog spec today, since voxcpm2_urdu_lora was withdrawn 2026-08-15) —
    not send it as null/empty. A separate,
    local catalog is used here rather than the module-level CATALOG/A/B/C so
    this doesn't perturb the VRAM-budget and eviction-order assumptions the
    rest of this file's tests make about that shared fixture.
    """
    plain = _spec("plain", RuntimeKind.VOXCPM, 6000)
    lora = ModelSpec(
        id="lora", display_name="lora", runtime=RuntimeKind.VOXCPM,
        license=License.MIT, hf_repo="x/y", hf_revision="a" * 40, languages=(),
        vram_mb=6000, est_load_sec=10.0, lora_local_path="/fake/lora-dir",
        lora_hf_repo="someone/lora-repo", lora_hf_revision="b" * 40,
    )
    catalog = build_catalog((plain, lora))

    made: dict[RuntimeKind, FakeWorker] = {}

    async def factory(runtime: RuntimeKind) -> FakeWorker:
        worker = FakeWorker(runtime.value, load_delay_sec=0.01)
        await worker.start()
        made[runtime] = worker
        return worker

    sched = InferenceScheduler(catalog, factory, CONFIG)

    await sched.synthesize(_req("plain", tmp_path))
    await sched.synthesize(_req("lora", tmp_path))

    load_payloads = [
        payload for op, payload in made[RuntimeKind.VOXCPM].calls if op is WireOp.LOAD
    ]
    assert len(load_payloads) == 2, "same runtime -> WARM swap, not a new worker"
    for key in ("lora_local_path", "lora_hf_repo", "lora_hf_revision"):
        assert key not in load_payloads[0]
    assert load_payloads[1]["lora_local_path"] == "/fake/lora-dir"
    assert load_payloads[1]["lora_hf_repo"] == "someone/lora-repo"
    assert load_payloads[1]["lora_hf_revision"] == "b" * 40
