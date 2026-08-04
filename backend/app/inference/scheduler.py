"""
AI Voice Clone Studio — The inference scheduler.

STUB. Wave 0 fixes the signatures and the invariant. B1 implements in Wave 2.
Do not import torch here — this module runs in the API process.

THE INVARIANT
-------------
    Eviction happens ONLY inside `_ensure_ready()`, which is ONLY ever called
    while holding `self._slot`.

Nobody can evict without the slot. Nobody can infer without the slot. Therefore
unload-during-inference is UNREPRESENTABLE, rather than merely guarded by a lock
someone can forget to take. That is one sentence of reasoning instead of a lock
hierarchy, and it is the whole reason this class exists.

The predecessor had zero locks of any kind (`grep asyncio.Lock|Semaphore|Queue`
returned nothing), and in its default ONE_ACTIVE_MODEL mode request B could call
`unload_model()` while request A was mid-inference — a use-after-free on CUDA
memory that presents as a random illegal-access crash.

If you find yourself adding a second place that evicts, stop: the invariant is
gone and so is the guarantee.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .catalog import ModelCatalog
from .protocol import ModelStatus, SynthRequest, SynthResult, WorkerHandle
from .spec import ModelSpec, RuntimeKind

__all__ = ["SchedulerConfig", "InferenceScheduler"]


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    """
    Capacity limits.

    The defaults are derived for a 24 GB RTX A5000:

        24564 MB total
        -  ~500 MB driver/display
        -  ~500 MB CUDA context per worker
        - ~2000 MB fragmentation headroom
        = ~16000 MB usable

    Hence budget_mb=16000, max_workers=2. Re-derive these for other hardware;
    do not scale them by intuition.
    """

    budget_mb: int = 16_000
    max_workers: int = 2
    #: Concurrent requests admitted before rejecting with QueueFullError. A
    #: bounded queue that 503s beats an unbounded one that OOMs the box.
    admission_limit: int = 8
    #: Floor for a synthesis deadline. The real budget scales with text length —
    #: see `_timeout_for`.
    base_timeout_sec: float = 120.0
    #: Added per 100 characters of resolved text.
    timeout_per_100_chars_sec: float = 20.0
    load_timeout_sec: float = 300.0
    #: SIGTERM-to-SIGKILL grace when killing a worker.
    kill_grace_sec: float = 5.0


class InferenceScheduler:
    """
    Owns every GPU worker process and serializes all access to the GPU.

    Exactly one instance per process. This is why the app must run with ONE
    uvicorn worker: N uvicorn workers would mean N schedulers, each believing it
    owns all 24 GB, and N x VRAM in practice.

    Implements `SchedulerProtocol`. B2 depends on the protocol, never on this
    class, which is what lets the API test suite run with no GPU.
    """

    def __init__(self, catalog: ModelCatalog, config: SchedulerConfig | None = None) -> None:
        self._catalog = catalog
        self._config = config or SchedulerConfig()

        #: THE GPU. Exactly one holder at a time. Every inference and every
        #: eviction happens under this.
        self._slot = asyncio.Semaphore(1)

        #: Bounded admission. Acquired WITHOUT blocking; failure to acquire
        #: immediately is a 503, not a wait.
        self._admission = asyncio.Semaphore(self._config.admission_limit)

        #: Live workers by runtime. LRU order for eviction.
        self._workers: dict[RuntimeKind, WorkerHandle] = {}

    # ── SchedulerProtocol ────────────────────────────────────────────────────

    async def synthesize(self, request: SynthRequest) -> SynthResult:
        """
        Run one synthesis to completion.

        The shape B1 must preserve:

            async with _bounded(self._admission):        # -> QueueFullError
                async with self._slot:                   # <-- invariant lives here
                    worker = await self._ensure_ready(spec)
                    resp = await asyncio.shield(
                        worker.call("synth", ..., timeout=self._timeout_for(spec, text))
                    )
                    return SynthResult(**resp.result)

        `asyncio.shield` is not optional. If the HTTP client disconnects
        mid-generation, an unshielded await would be cancelled, the `async with`
        would release the slot, and another request would enter while a CUDA
        kernel from the abandoned one is still running — exactly the race this
        class exists to prevent. The only early exit is a hard timeout, which
        SIGKILLs the worker, because a wedged CUDA call cannot be interrupted
        any other way.

        Raises: QueueFullError, ModelNotFoundError, VRAMExhaustedError,
            WorkerCrashedError, GenerationTimeoutError, GenerationError.
        """
        raise NotImplementedError("Wave 2 / B1")

    async def status(self) -> tuple[ModelStatus, ...]:
        """
        Residency of every catalog spec.

        Must not acquire the slot and must not touch the GPU: the UI polls this,
        and a status call that queues behind a 60s generation makes the UI look
        hung. Report from in-memory bookkeeping only.
        """
        raise NotImplementedError("Wave 2 / B1")

    async def warm(self, model_id: str) -> None:
        """
        Pre-load a spec. Takes the slot, so it queues behind in-flight work
        rather than racing it.
        """
        raise NotImplementedError("Wave 2 / B1")

    async def shutdown(self) -> None:
        """Kill every worker. Idempotent. Called from the app lifespan."""
        raise NotImplementedError("Wave 2 / B1")

    # ── Internals ────────────────────────────────────────────────────────────

    async def _ensure_ready(self, spec: ModelSpec) -> WorkerHandle:
        """
        Return a worker with `spec` loaded, evicting others if needed.

        PRECONDITION: the caller holds `self._slot`. This is the ONLY function
        permitted to evict. B1 must assert the precondition rather than trust it
        — an assertion here is the cheapest possible guard on the invariant, and
        `test_no_unload_during_inference` exists to prove it holds.

        Three cases, cheapest first:
          RESIDENT — the runtime's worker already has this checkpoint. Return it.
          WARM     — the runtime has a worker with a different checkpoint. Swap
                     in-process (~1-3s), do not restart.
          COLD     — no worker for this runtime. Evict LRU until `spec.vram_mb`
                     fits the budget, then spawn (~20-60s).

        Eviction is SIGKILL, not a graceful unload: `del model; empty_cache()`
        leaves fragmentation and a ~500 MB CUDA context behind, so the VRAM the
        budget thinks it reclaimed does not actually come back.
        """
        raise NotImplementedError("Wave 2 / B1")

    async def _spawn(self, runtime: RuntimeKind) -> WorkerHandle:
        """
        Start a worker subprocess for `runtime`.

        Uses that runtime's own interpreter when configured. Being able to give
        each runtime a different interpreter is the reason this is a subprocess
        rather than a thread — F5, Chatterbox, and VoxCPM pin transformers and
        torchaudio stacks with a real chance of not co-resolving in one env.
        """
        raise NotImplementedError("Wave 2 / B1")

    async def _evict(self, runtime: RuntimeKind) -> None:
        """
        Kill a runtime's worker and reclaim its VRAM.

        PRECONDITION: the caller holds `self._slot`. Reachable only from
        `_ensure_ready`.
        """
        raise NotImplementedError("Wave 2 / B1")

    def _free_vram_mb(self) -> int:
        """
        Free VRAM, via NVML / `torch.cuda.mem_get_info()`.

        NEVER `total_memory - memory_allocated()`. That sees only the calling
        process's own tensors, and the models live in OTHER processes — so it
        reports 24 GB free while workers hold 20 GB. `gpu_manager.py:103-104`
        does exactly this today, and every OOM decision downstream inherits the
        wrong number.

        In the API process this reads NVML directly (pynvml, not torch) so the
        no-torch invariant survives.

        MEASURED TRAP (R1b, Wave 1): a post-hoc NVML/nvidia-smi reading badly
        UNDER-reports the transient peak. Sampling 20s after an F5 inference
        finished showed ~1092 MiB; concurrent sampling at 200ms during the same
        run caught 5845 MiB. Allocator caches are released back between
        requests, so the number you get after the fact is not the number that
        determines whether the next model fits.

        Consequence for admission control: size decisions must use the spec's
        recorded `vram_mb` (measured under load), NOT a live free-VRAM reading
        taken between requests. A live reading is useful for detecting
        *external* processes, not for predicting peak.
        """
        raise NotImplementedError("Wave 2 / B1")

    def _timeout_for(self, spec: ModelSpec, text: str) -> float:
        """
        Deadline for one synthesis, scaled by text length.

        A fixed timeout is wrong in both directions: it kills long-but-healthy
        generations and lets a wedged short one hold the slot for minutes.
        """
        raise NotImplementedError("Wave 2 / B1")
