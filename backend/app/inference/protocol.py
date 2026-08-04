"""
AI Voice Clone Studio — Inference protocols.

CONTRACT MODULE. Wave 0.

This file is the seam between the API process (no torch, no CUDA) and the
worker processes (torch, CUDA, one dependency set each). B2 codes against
`SchedulerProtocol` and must never import `scheduler.py` itself.

THE STRUCTURAL INVARIANT
------------------------
    `import torch` must not be reachable from `app.main`.

Runtimes live in separate OS processes behind a SYNCHRONOUS, BLOCKING, BORING
interface. There is no coroutine anywhere in a worker, so no worker code can
accidentally block an event loop — there is no event loop to block.

Subprocesses rather than a thread pool, for three reasons a thread pool cannot
give:

  1. Dependency isolation. F5, Chatterbox and VoxCPM pin transformers/torchaudio
     stacks with a real chance of not co-resolving. A subprocess can run a
     *different interpreter*; a thread cannot. This alone decides it.
  2. VRAM actually returns. `del model; empty_cache()` leaves fragmentation and
     a ~500MB CUDA context behind. SIGKILL returns everything, deterministically.
  3. Crash isolation. A CUDA illegal-access kills one worker, not the API.

IPC is cheap here: audio is already on disk, so messages are a few hundred bytes
of JSON over line-delimited stdio. No waveform is ever serialized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .spec import ModelSpec, ModelState

__all__ = [
    "WireOp",
    "WireRequest",
    "WireResponse",
    "SynthRequest",
    "SynthResult",
    "ModelStatus",
    "WorkerHandle",
    "SchedulerProtocol",
]


# ── Wire protocol: line-delimited JSON over the worker's stdin/stdout ─────────


class WireOp(StrEnum):
    """Operations a worker understands. One request, one response, in order."""

    #: Load a checkpoint. If another spec of the same runtime is loaded, the
    #: worker swaps checkpoints (seconds) rather than restarting (tens of).
    LOAD = "load"
    #: Generate audio. Writes to `output_path` and returns metadata only.
    SYNTH = "synth"
    #: Drop the current checkpoint but keep the process (and its CUDA context).
    UNLOAD = "unload"
    #: Liveness plus current residency. Must never touch the GPU.
    PING = "ping"
    #: Graceful exit. The scheduler follows with SIGKILL after a grace period,
    #: because a wedged CUDA call cannot be interrupted any other way.
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class WireRequest:
    """One line of JSON written to a worker's stdin."""

    #: Monotonic per-worker. Responses are matched on it; a mismatch means the
    #: stream desynchronized and the worker must be killed, not resynced.
    id: int
    op: WireOp
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WireResponse:
    """One line of JSON read from a worker's stdout."""

    id: int
    ok: bool
    result: dict[str, Any] = field(default_factory=dict)
    #: Populated when ok is False. `code` maps to an app exception; `traceback`
    #: is captured for logs and never returned to a client.
    error_code: str | None = None
    error_message: str | None = None
    traceback: str | None = None


# ── Application-level request/result ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SynthRequest:
    """
    A fully-resolved synthesis job.

    "Fully-resolved" is the point: routing has already run, the transform has
    already been applied, and `text` is the FINAL string in the FINAL script
    that the model will see. A worker never routes, never transliterates and
    never falls back — it renders what it is handed or it fails.
    """

    model_id: str
    text: str
    reference_audio: Path
    output_path: Path
    #: Required when the chosen spec sets `needs_reference_text`.
    reference_text: str | None = None
    #: Validated against the spec's declared `params` before it reaches here.
    params: dict[str, Any] = field(default_factory=dict)
    sample_rate: int = 44100


@dataclass(frozen=True, slots=True)
class SynthResult:
    """What a worker produced. Audio itself is on disk at `output_path`."""

    output_path: Path
    duration_sec: float
    gen_time_sec: float
    sample_rate: int
    #: The spec that actually rendered this. Compared against the RoutePlan by
    #: an assertion in the service layer: if these ever disagree, something
    #: fell back silently, which is the one regression that must never return.
    model_id: str
    #: Seconds spent loading, if this request paid a cold or warm start. Feeds
    #: the catalog's est_load_sec back toward reality.
    load_time_sec: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def rtf(self) -> float | None:
        """Real-time factor. < 1.0 is faster than realtime."""
        if self.duration_sec <= 0:
            return None
        return self.gen_time_sec / self.duration_sec


@dataclass(frozen=True, slots=True)
class ModelStatus:
    """Residency of one spec, for `GET /api/models`."""

    spec: ModelSpec
    state: ModelState
    #: How long this specific request would likely wait, given current
    #: residency: 0 when resident, a checkpoint swap when warm, a full process
    #: start when cold. This is what the UI shows *before* the click.
    est_wait_sec: float
    vram_mb_in_use: int | None = None
    last_used_at: float | None = None


# ── Worker and scheduler interfaces ──────────────────────────────────────────


@runtime_checkable
class WorkerHandle(Protocol):
    """
    A live worker process, as the scheduler sees it.

    Every method here is called while holding the GPU slot (see
    SchedulerProtocol). Implementations must be safe to SIGKILL at any moment;
    nothing here may hold state that matters after death.
    """

    @property
    def runtime(self) -> str:
        """The RuntimeKind value this process serves."""
        ...

    @property
    def loaded_model_id(self) -> str | None:
        """Currently loaded checkpoint, or None."""
        ...

    @property
    def pid(self) -> int | None:
        """OS pid, or None once dead."""
        ...

    @property
    def is_alive(self) -> bool:
        """False once the process has exited or been killed."""
        ...

    async def call(
        self, op: WireOp, payload: dict[str, Any], *, timeout: float
    ) -> WireResponse:
        """
        Send one request and await its response.

        On timeout the implementation MUST kill the process rather than
        abandoning the request: a wedged CUDA kernel cannot be cancelled, and a
        half-consumed stdio stream cannot be trusted afterward.
        """
        ...

    async def kill(self, *, grace_sec: float = 5.0) -> None:
        """SHUTDOWN, then SIGKILL after `grace_sec`. Idempotent."""
        ...


@runtime_checkable
class SchedulerProtocol(Protocol):
    """
    What B2 (the API layer) is allowed to depend on.

    B2 imports THIS, never `scheduler.InferenceScheduler`. That is what lets the
    entire API test suite run on a CPU-only machine in ~30s against
    `tests.fakes.FakeScheduler`.

    THE CONCURRENCY INVARIANT, which the whole design rests on:

        Eviction happens only inside `_ensure_ready()`, which is only ever
        called while holding the single GPU-slot semaphore.

    Nobody can evict without the slot; nobody can infer without the slot.
    Therefore unload-during-inference is UNREPRESENTABLE — not merely guarded.
    That is one sentence of reasoning instead of a lock hierarchy, and it is
    why no implementation may grow a second eviction path.
    """

    async def synthesize(self, request: SynthRequest) -> SynthResult:
        """
        Run one synthesis to completion.

        Raises:
            QueueFullError: admission bound reached. Surfaces as 503 — a bounded
                queue that rejects beats an unbounded one that OOMs.
            ModelNotFoundError: `model_id` is not in the catalog.
            VRAMExhaustedError: does not fit even with everything else evicted.
            WorkerCrashedError: the worker died mid-request.
            GenerationTimeoutError: exceeded its deadline; the worker was killed.

        Implementations MUST wrap the worker call in `asyncio.shield`. If an
        HTTP client disconnects mid-generation, cancelling here would release
        the GPU slot while a CUDA kernel is still running — which is precisely
        the race this design exists to eliminate. The only early exit is a hard
        timeout followed by SIGKILL.
        """
        ...

    async def status(self) -> tuple[ModelStatus, ...]:
        """Residency of every catalog spec. Cheap; must not touch the GPU."""
        ...

    async def warm(self, model_id: str) -> None:
        """
        Pre-load a spec so the next request is RESIDENT.

        Takes the same slot as `synthesize`, so an explicit warm queues behind
        in-flight work rather than racing it.
        """
        ...

    async def shutdown(self) -> None:
        """Kill every worker. Idempotent. Called from the app lifespan."""
        ...
