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

AMENDED 2026-08-17 — ONE second eviction call site, and the reasoning
--------------------------------------------------------------------
`exclusive_gpu()` evicts, and it is not `_ensure_ready`. That is a real
amendment to the paragraph above, made deliberately rather than by accident,
so read this before adding a third.

What the invariant actually buys is in the sentence "unload-during-inference
is UNREPRESENTABLE, rather than merely guarded by a lock someone can forget to
take." That property is untouched: `exclusive_gpu` holds `self._slot` across
its entire body, and evicts through the same `_evict` behind the same
`_slot_held` assertion. No inference can be in flight while it runs, because
inference needs the slot it is holding. The two-call-site version of the rule
is weaker than the one-call-site version only in that it is one more place to
read — not in what it permits.

What forced it: Phase B's transliterator is Gemma-4-31B at ~19 GB against a
24 GB card, and it can never be co-resident with the audio models. It needs
the GPU emptied, and `_make_room_for` cannot express "empty it" — it evicts
until a spec fits a budget that is deliberately sized for co-residency
(16 GB), so a 19 GB spec fails its first check outright. The alternative was
to special-case the budget arithmetic for one model, which hides the
exception inside the sizing logic every other model depends on. A named
method that says what it does is the more honest place for it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..exceptions import (
    GenerationTimeoutError,
    ModelNotFoundError,
    QueueFullError,
    VRAMExhaustedError,
    WorkerCrashedError,
)
from .catalog import ModelCatalog
from .protocol import ModelStatus, SynthRequest, SynthResult, WireOp, WorkerHandle
from .spec import ModelSpec, ModelState, RuntimeKind

logger = logging.getLogger("app.inference.scheduler")

__all__ = ["SchedulerConfig", "InferenceScheduler"]

#: Builds a worker for a runtime. Injected so tests can supply FakeWorker and
#: the whole scheduler suite runs with no GPU and no subprocess.
WorkerFactory = Callable[[RuntimeKind], Awaitable[WorkerHandle]]


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

    def __init__(
        self,
        catalog: ModelCatalog,
        worker_factory: WorkerFactory,
        config: SchedulerConfig | None = None,
    ) -> None:
        self._catalog = catalog
        self._make_worker = worker_factory
        self._config = config or SchedulerConfig()

        #: THE GPU. Exactly one holder at a time. Every inference and every
        #: eviction happens under this.
        self._slot = asyncio.Semaphore(1)

        #: Bounded admission. Acquired WITHOUT blocking; failure to acquire
        #: immediately is a 503, not a wait.
        self._admission = asyncio.Semaphore(self._config.admission_limit)

        #: Live workers by runtime.
        self._workers: dict[RuntimeKind, WorkerHandle] = {}

        #: Monotonic use counter for LRU. A counter rather than a clock: it is
        #: deterministic, so eviction order is testable without freezing time.
        self._tick = 0
        self._last_used: dict[RuntimeKind, int] = {}

        #: True only while a task holds `_slot`. Used by the assertion in
        #: `_ensure_ready` that keeps the eviction invariant honest.
        self._slot_held = False
        self._shutting_down = False

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
        spec = self._require(request.model_id)

        # Non-blocking: a full queue rejects immediately rather than piling up
        # unbounded waiters behind a 60s generation.
        if self._admission.locked() or not self._try_acquire(self._admission):
            raise QueueFullError(limit=self._config.admission_limit)

        try:
            async with self._hold_slot():
                worker = await self._ensure_ready(spec)
                payload = {
                    "model_id": spec.id,
                    "text": request.text,
                    "reference_audio": str(request.reference_audio),
                    "reference_text": request.reference_text,
                    "output_path": str(request.output_path),
                    "params": request.params,
                    "sample_rate": request.sample_rate,
                }
                timeout = self._timeout_for(spec, request.text)

                # shield() is NOT optional. If the HTTP client disconnects
                # mid-generation, an unshielded await would be cancelled, the
                # `async with` would release the slot, and the next request
                # would enter while this CUDA kernel is still running. That is
                # the exact race this class exists to prevent. The only early
                # exit is the timeout below, which kills the process.
                try:
                    response = await asyncio.shield(
                        worker.call(WireOp.SYNTH, payload, timeout=timeout)
                    )
                except TimeoutError as exc:
                    await self._evict(spec.runtime)
                    raise GenerationTimeoutError(spec.id, timeout) from exc

                if not response.ok:
                    if not worker.is_alive:
                        self._forget(spec.runtime)
                        raise WorkerCrashedError(spec.id)
                    raise self._error_from(spec, response)

                self._tick += 1
                self._last_used[spec.runtime] = self._tick
                result = response.result
                return SynthResult(
                    output_path=request.output_path,
                    duration_sec=float(result["duration_sec"]),
                    gen_time_sec=float(result["gen_time_sec"]),
                    sample_rate=int(result.get("sample_rate", request.sample_rate)),
                    model_id=spec.id,
                    load_time_sec=float(result.get("load_time_sec", 0.0)),
                )
        finally:
            self._admission.release()

    async def status(self) -> tuple[ModelStatus, ...]:
        """
        Residency of every catalog spec.

        Must not acquire the slot and must not touch the GPU: the UI polls this,
        and a status call that queues behind a 60s generation makes the UI look
        hung. Report from in-memory bookkeeping only.
        """
        out: list[ModelStatus] = []
        for spec in self._catalog.specs:
            worker = self._workers.get(spec.runtime)
            if worker is not None and worker.loaded_model_id == spec.id:
                state, wait = ModelState.RESIDENT, 0.0
            elif worker is not None and worker.is_alive:
                # Same runtime is live with a different checkpoint: a swap costs
                # seconds, not a process start. The UI shows that difference.
                state, wait = ModelState.WARM, min(spec.est_load_sec, 5.0)
            else:
                state, wait = ModelState.COLD, spec.est_load_sec
            out.append(
                ModelStatus(
                    spec=spec,
                    state=state,
                    est_wait_sec=wait,
                    vram_mb_in_use=spec.vram_mb if state is ModelState.RESIDENT else None,
                    last_used_at=self._last_used.get(spec.runtime),
                )
            )
        return tuple(out)

    async def warm(self, model_id: str) -> None:
        """
        Pre-load a spec. Takes the slot, so it queues behind in-flight work
        rather than racing it.
        """
        spec = self._require(model_id)
        async with self._hold_slot():
            await self._ensure_ready(spec)

    @contextlib.asynccontextmanager
    async def exclusive_gpu(self, reason: str):
        """
        Hold the GPU slot and hand back an EMPTY GPU, for one non-audio model
        too large to coexist with the audio ones.

        Built for exactly one caller: `TransliteratorScheduler`. Gemma-4-31B
        is ~19 GB at 4-bit against a 24 GB card, and VoxCPM (7.3 GB) plus
        OmniVoice (4.7 GB) are both warmed at startup. There is no arrangement
        in which they are simultaneously resident, so "make room" here means
        "make ALL the room" — this evicts every worker rather than the LRU few
        `_make_room_for` would.

        WHY THIS IS NOT A SECOND WAY TO BREAK GOLDEN RULE 3
        ---------------------------------------------------
        Rule 3 says eviction happens only inside `_ensure_ready`, only under
        the slot. This is a second CALL SITE, and that is a real amendment —
        recorded in CLAUDE.md rather than smuggled in here. What the rule
        exists to guarantee is untouched: the point is that unloading a model
        while it is being inferred on must be UNREPRESENTABLE, and it still
        is, because this holds the same semaphore for the entire window and
        goes through the same `_evict` with the same assertion. Nothing new
        can evict; one more thing can ask the existing evictor to.

        Why not let `AnalyzerScheduler`'s pattern handle it: that one spawns
        its worker outside this scheduler entirely and gets away with it only
        because Qwen2.5-3B's ~6 GB fits in the slack. Doing that at 19 GB is
        precisely the two-schedulers-unaware-of-each-other failure that
        `analyzer_scheduler.py`'s own docstring warns about.

        The cost is honest and belongs to the caller: audio models come back
        COLD afterwards, so the next generation pays a full load. That is the
        price of the load-convert-unload shape, not a bug in it.
        """
        async with self._hold_slot():
            evicted = [str(r) for r in self._workers]
            logger.info(
                "exclusive GPU requested (%s); evicting %d worker(s): %s",
                reason, len(evicted), ", ".join(evicted) or "none",
            )
            for runtime in list(self._workers):
                await self._evict(runtime)
            try:
                yield
            finally:
                # Nothing to restore. Audio workers reload lazily through
                # `_ensure_ready`'s COLD path on the next request, which is
                # the same path a fresh process takes.
                logger.info("exclusive GPU released (%s)", reason)

    async def shutdown(self) -> None:
        """Kill every worker. Idempotent. Called from the app lifespan."""
        self._shutting_down = True
        workers = list(self._workers.values())
        self._workers.clear()
        self._last_used.clear()
        for worker in workers:
            with contextlib.suppress(Exception):
                await worker.kill(grace_sec=self._config.kill_grace_sec)

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
        # The invariant, asserted rather than trusted. This is the cheapest
        # possible guard on the one property the whole design rests on.
        assert self._slot_held, "_ensure_ready called without holding the GPU slot"

        worker = self._workers.get(spec.runtime)

        # RESIDENT — nothing to do.
        if worker is not None and worker.is_alive and worker.loaded_model_id == spec.id:
            return worker

        # A dead worker is bookkeeping we must drop before sizing the budget.
        if worker is not None and not worker.is_alive:
            self._forget(spec.runtime)
            worker = None

        # WARM — right runtime, wrong checkpoint. Swap in-process.
        if worker is not None:
            await self._load_into(worker, spec)
            return worker

        # COLD — evict until this spec fits, then spawn.
        await self._make_room_for(spec)
        worker = await self._make_worker(spec.runtime)
        self._workers[spec.runtime] = worker
        self._tick += 1
        self._last_used[spec.runtime] = self._tick
        try:
            await self._load_into(worker, spec)
        except Exception:
            # Never leave a half-loaded worker holding VRAM the budget believes
            # is in use by a working model.
            await self._evict(spec.runtime)
            raise
        return worker

    async def _load_into(self, worker: WorkerHandle, spec: ModelSpec) -> None:
        """Load `spec` into an existing worker. Caller holds the slot."""
        payload = {"model_id": spec.id, "hf_repo": spec.hf_repo, "hf_revision": spec.hf_revision}
        # Only present for specs carrying a LoRA adapter (currently
        # voxcpm2_urdu_lora) — omitted entirely for every other spec, so
        # runtimes that don't know about it never see the keys.
        if spec.lora_local_path is not None:
            payload["lora_local_path"] = spec.lora_local_path
        if spec.lora_hf_repo is not None:
            payload["lora_hf_repo"] = spec.lora_hf_repo
            payload["lora_hf_revision"] = spec.lora_hf_revision
        response = await worker.call(
            WireOp.LOAD, payload, timeout=self._config.load_timeout_sec,
        )
        if not response.ok:
            raise self._error_from(spec, response)

    async def _make_room_for(self, spec: ModelSpec) -> None:
        """
        Evict least-recently-used runtimes until `spec` fits the budget.

        Sizing uses each resident spec's RECORDED `vram_mb`, not a live free-VRAM
        reading — see `_free_vram_mb` for why a live reading cannot predict peak.
        """
        if spec.vram_mb > self._config.budget_mb:
            raise VRAMExhaustedError(spec.id, spec.vram_mb, self._config.budget_mb)

        while (
            self._reserved_mb() + spec.vram_mb > self._config.budget_mb
            or len(self._workers) >= self._config.max_workers
        ):
            if not self._workers:
                raise VRAMExhaustedError(spec.id, spec.vram_mb, self._config.budget_mb)
            victim = min(self._workers, key=lambda r: self._last_used.get(r, 0))
            await self._evict(victim)

    def _reserved_mb(self) -> int:
        """VRAM attributed to live workers, from recorded per-spec figures."""
        total = 0
        for runtime, worker in self._workers.items():
            loaded = worker.loaded_model_id
            spec = self._catalog.get(loaded) if loaded else None
            total += spec.vram_mb if spec else self._largest_vram(runtime)
        return total

    def _largest_vram(self, runtime: RuntimeKind) -> int:
        """Worst-case VRAM for a runtime whose checkpoint we cannot identify."""
        specs = self._catalog.by_runtime(runtime)
        return max((s.vram_mb for s in specs), default=0)

    async def _spawn(self, runtime: RuntimeKind) -> WorkerHandle:
        """
        Start a worker subprocess for `runtime`.

        Uses that runtime's own interpreter when configured. Being able to give
        each runtime a different interpreter is the reason this is a subprocess
        rather than a thread — F5, Chatterbox, and VoxCPM pin transformers and
        torchaudio stacks with a real chance of not co-resolving in one env.
        """
        return await self._make_worker(runtime)

    async def _evict(self, runtime: RuntimeKind) -> None:
        """
        Kill a runtime's worker and reclaim its VRAM.

        PRECONDITION: the caller holds `self._slot`. Reachable only from
        `_make_room_for`, from the failure path of a partially-loaded spawn, and
        from a generation timeout — all three hold the slot. `shutdown()`
        bypasses it deliberately: by then nothing may take the slot again.
        """
        assert self._slot_held or self._shutting_down, (
            "_evict called without holding the GPU slot"
        )
        worker = self._workers.pop(runtime, None)
        self._last_used.pop(runtime, None)
        if worker is not None:
            with contextlib.suppress(Exception):
                await worker.kill(grace_sec=self._config.kill_grace_sec)

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
        return max(0, self._config.budget_mb - self._reserved_mb())

    def _timeout_for(self, spec: ModelSpec, text: str) -> float:
        """
        Deadline for one synthesis, scaled by text length.

        A fixed timeout is wrong in both directions: it kills long-but-healthy
        generations and lets a wedged short one hold the slot for minutes.
        """
        chunks = max(1.0, len(text) / 100.0)
        budget = self._config.base_timeout_sec + chunks * self._config.timeout_per_100_chars_sec
        # A slow model needs proportionally longer. est_rtf is measured, so this
        # tracks reality rather than a guess.
        if spec.est_rtf:
            budget *= max(1.0, spec.est_rtf / 0.5)
        return budget

    # ── Slot plumbing ────────────────────────────────────────────────────────

    @contextlib.asynccontextmanager
    async def _hold_slot(self):
        """
        Acquire the GPU slot and record that we hold it.

        The flag exists purely so `_ensure_ready` and `_evict` can ASSERT the
        invariant rather than trust it. asyncio.Semaphore has no "am I the
        holder" query, and the whole design rests on this one property.
        """
        async with self._slot:
            self._slot_held = True
            try:
                yield
            finally:
                self._slot_held = False

    @staticmethod
    def _try_acquire(sem: asyncio.Semaphore) -> bool:
        """Acquire without waiting. Returns False if it would have blocked."""
        if sem.locked():
            return False
        # Semaphore.acquire() returns immediately when the counter is positive,
        # so this cannot block given the check above.
        sem._value -= 1
        return True

    def _require(self, model_id: str) -> ModelSpec:
        spec = self._catalog.get(model_id)
        if spec is None:
            raise ModelNotFoundError(model_id, tuple(s.id for s in self._catalog.specs))
        return spec

    def _forget(self, runtime: RuntimeKind) -> None:
        """Drop bookkeeping for a worker that died on its own."""
        self._workers.pop(runtime, None)
        self._last_used.pop(runtime, None)

    def _error_from(self, spec: ModelSpec, response) -> Exception:
        """Map a worker error response onto an app exception."""
        from ..exceptions import GenerationError, ModelLoadError

        code = response.error_code or ""
        detail = response.error_message or "worker reported no detail"
        if code == "WORKER_CRASHED":
            return WorkerCrashedError(spec.id)
        if code in {"MODEL_LOAD_FAILED", "LOAD_FAILED"}:
            return ModelLoadError(spec.id, detail)
        return GenerationError(spec.id, detail)
