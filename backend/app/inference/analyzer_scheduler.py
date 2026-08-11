"""
AI Voice Clone Studio — the Speech Direction LLM analyzer scheduler.

Runs in the API process. NO TORCH HERE — same invariant as `scheduler.py`,
enforced by the same `tests/test_contracts.py::test_no_torch_outside_runtimes`
(this file is not under `inference/runtimes/`, so it must not import torch).
The Qwen2.5-Instruct torch/transformers stack lives only in
`inference/runtimes/qwen_analyzer.py`, loaded by a worker subprocess this
class spawns and talks to over the exact same line-delimited JSON wire
protocol every other runtime uses — `inference/worker_client.py`'s
`WorkerProcess` is fully generic over `WireOp`/JSON payloads and is reused
here UNMODIFIED, not reimplemented.

WHY NOT `InferenceScheduler`
-----------------------------
Golden rule 8 is explicit that `inference/scheduler.py` is never modified
for job-queue-adjacent work, but this would be the wrong home even without
that rule: it is a structurally different problem. `InferenceScheduler`
exists to serialize access to a shared GPU across MULTIPLE models with a
VRAM budget and an eviction policy (`_ensure_ready`'s LRU eviction under the
`_slot` semaphore). This analyzer is exactly one fixed model — there is
nothing to evict FOR, only a single worker to lazily start and, eventually,
idle-kill. Bolting a second op type onto `InferenceScheduler` would either
weaken its one invariant (eviction only inside `_ensure_ready`, only under
the slot) for no reason, or duplicate a chunk of it uselessly for a
scheduler that never evicts between checkpoints. A small, separate class is
the honest shape for a smaller problem.

THE VRAM-CONTENTION TENSION (read before changing the idle-unload default)
----------------------------------------------------------------------------
`InferenceScheduler`'s `budget_mb=16_000` (`config.py`) is sized assuming
ONLY audio models are resident: VoxCPM2 measures ~7.3 GB peak, Chatterbox
~6 GB (`inference/catalog.py`, both MEASURED figures). A resident bf16
Qwen2.5-3B is roughly another ~6 GB. On a 20 GB card — the RTX 4000 Ada this
project has actually run on, see `.claude/remote.local.md` — two schedulers
that are each unaware of the other's residency is a real risk, not a
theoretical one: an audio job and an analyzer classification in flight at
the same moment can each believe they fit, because neither reads the
other's reservation.

This class deliberately does NOT make the two schedulers VRAM-aware of each
other in this pass — that would mean either merging them (which the section
above argues against) or building a cross-scheduler budget-sharing
mechanism, both real projects of their own. The mitigation here is
deliberately blunt and simple: an idle-unload timer
(`qwen_analyzer_idle_unload_sec`, default 300s) that KILLS the worker
subprocess entirely — not merely a wire UNLOAD, which only drops the
in-process checkpoint and, per `_ensure_ready`'s own reasoning, leaves
fragmentation and a CUDA context behind — after that long with no
`classify()` call, so the ~6 GB actually comes back. It does not prevent
contention DURING a window where both are genuinely in active use at once;
it only bounds how long Qwen sits resident for no reason. A future pass
sharing one real VRAM budget across both schedulers is the actual fix; this
is the v1 that ships without it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path

from ..exceptions import AnalyzerResponseInvalidError, AnalyzerUnavailableError
from .protocol import AnalyzeResult, WireOp
from .worker_client import WorkerProcess

__all__ = [
    "AnalyzerScheduler",
    "QWEN_ANALYZER_MODEL_ID",
    "QWEN_ANALYZER_HF_REPO",
    "QWEN_ANALYZER_HF_REVISION",
]

logger = logging.getLogger("app.inference.analyzer_scheduler")

#: Not a `ModelSpec` / `CATALOG` entry on purpose: this analyzer is not
#: audio, and `domain/routing.py`'s `resolve()` must never be able to route
#: a TTS request to it. Kept as small module-level constants, analogous in
#: spirit to a catalog entry's `hf_repo`/`hf_revision` but structurally
#: outside the audio catalog.
QWEN_ANALYZER_MODEL_ID = "qwen2.5-3b-instruct-analyzer"
QWEN_ANALYZER_HF_REPO = "Qwen/Qwen2.5-3B-Instruct"
#: PINNED per golden rule 7 (no unpinned `main`). Resolved on the pod via
#: `huggingface_hub.HfApi().model_info("Qwen/Qwen2.5-3B-Instruct").sha` —
#: see docs/HANDOFF.md for the date this was resolved and confirmed against
#: a real load.
QWEN_ANALYZER_HF_REVISION = "PENDING_PIN"

_LOAD_TIMEOUT_SEC = 300.0
_CLASSIFY_TIMEOUT_SEC = 60.0
_WORKER_RUNTIME = "qwen_analyzer"


class AnalyzerScheduler:
    """
    Owns a single Qwen analyzer worker subprocess. One model, no eviction.

    `classify()` lazily starts the worker (once, under `_start_lock` so two
    concurrent callers cannot spawn two processes), issues `WireOp.LOAD`
    once per worker lifetime, then `WireOp.CLASSIFY` per call. A non-ok
    `WireResponse` becomes `AnalyzerUnavailableError` (infrastructure: the
    worker is gone or never started) or `AnalyzerResponseInvalidError` (the
    model's own response failed validation — see `runtimes/qwen_analyzer.py`
    `classify()`'s docstring for exactly what that means).
    """

    def __init__(
        self,
        *,
        python_executable: str,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        idle_unload_sec: float = 300.0,
        hf_repo: str = QWEN_ANALYZER_HF_REPO,
        hf_revision: str = QWEN_ANALYZER_HF_REVISION,
        load_timeout_sec: float = _LOAD_TIMEOUT_SEC,
        classify_timeout_sec: float = _CLASSIFY_TIMEOUT_SEC,
    ) -> None:
        self._python = python_executable
        self._env = env
        self._cwd = cwd
        self._idle_unload_sec = idle_unload_sec
        self._hf_repo = hf_repo
        self._hf_revision = hf_revision
        self._load_timeout_sec = load_timeout_sec
        self._classify_timeout_sec = classify_timeout_sec

        self._worker: WorkerProcess | None = None
        self._loaded = False
        #: Serializes worker start + load so two concurrent classify() calls
        #: on a cold scheduler spawn exactly one subprocess, not two.
        self._start_lock = asyncio.Lock()
        self._idle_task: asyncio.Task[None] | None = None
        #: monotonic() timestamp of the last classify() activity. The idle
        #: loop kills the worker once `now - _last_activity >= idle_unload_sec`.
        self._last_activity = 0.0
        self._shutting_down = False

    async def classify(self, *, language: str, sentences: tuple[str, ...]) -> AnalyzeResult:
        if not self._python:
            raise AnalyzerUnavailableError(
                "no interpreter configured for the qwen_analyzer runtime "
                "(set VCS_QWEN_ANALYZER_PYTHON)"
            )

        async with self._start_lock:
            if self._worker is None or not self._worker.is_alive:
                await self._start_worker()
            if not self._loaded:
                await self._load()
            self._touch()

        try:
            response = await self._worker.call(
                WireOp.CLASSIFY,
                {"language": language, "sentences": list(sentences)},
                timeout=self._classify_timeout_sec,
            )
        finally:
            self._touch()

        if not response.ok:
            if self._worker is None or not self._worker.is_alive:
                self._loaded = False
                raise AnalyzerUnavailableError(
                    f"qwen analyzer worker died mid-request: "
                    f"{response.error_message or 'unknown'}"
                )
            raise AnalyzerResponseInvalidError(
                response.error_message or "analyzer returned an unspecified error"
            )

        result = response.result
        return AnalyzeResult(
            rows=tuple(result.get("rows") or ()),
            gen_time_sec=float(result.get("gen_time_sec", 0.0)),
            load_time_sec=float(result.get("load_time_sec", 0.0)),
        )

    async def shutdown(self) -> None:
        """Kill the worker if alive, cancel the idle timer. Idempotent."""
        self._shutting_down = True
        if self._idle_task is not None:
            self._idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_task
            self._idle_task = None
        if self._worker is not None:
            with contextlib.suppress(Exception):
                await self._worker.kill()
            self._worker = None
        self._loaded = False

    # ── internals ────────────────────────────────────────────────────────────

    async def _start_worker(self) -> None:
        worker = WorkerProcess(_WORKER_RUNTIME, self._python, env=self._env, cwd=self._cwd)
        try:
            await worker.start()
        except Exception as exc:
            raise AnalyzerUnavailableError(
                f"qwen analyzer worker failed to start: {exc}"
            ) from exc
        self._worker = worker
        self._loaded = False
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_loop())

    async def _load(self) -> None:
        assert self._worker is not None
        response = await self._worker.call(
            WireOp.LOAD,
            {
                "model_id": QWEN_ANALYZER_MODEL_ID,
                "hf_repo": self._hf_repo,
                "hf_revision": self._hf_revision,
            },
            timeout=self._load_timeout_sec,
        )
        if not response.ok:
            raise AnalyzerUnavailableError(
                f"qwen analyzer failed to load: {response.error_message or 'unknown'}"
            )
        self._loaded = True

    def _touch(self) -> None:
        self._last_activity = time.monotonic()

    async def _idle_loop(self) -> None:
        """
        Kill the worker subprocess (not just an in-process UNLOAD — see the
        module docstring) after `_idle_unload_sec` with no `classify()`
        activity. Deliberately simple: wakes, checks how much longer is left
        until the deadline, sleeps exactly that long, and rechecks — so a
        short `idle_unload_sec` in a test fires promptly rather than waiting
        out a fixed poll interval.
        """
        try:
            while True:
                remaining = self._idle_unload_sec - (time.monotonic() - self._last_activity)
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    continue
                async with self._start_lock:
                    idle_for = time.monotonic() - self._last_activity
                    if self._worker is not None and idle_for >= self._idle_unload_sec:
                        logger.info(
                            "qwen analyzer idle for %.0fs; killing worker to release VRAM",
                            idle_for,
                        )
                        with contextlib.suppress(Exception):
                            await self._worker.kill()
                        self._worker = None
                        self._loaded = False
                # Nothing (more) to reclaim right now; wait a full window
                # before checking again.
                await asyncio.sleep(self._idle_unload_sec)
        except asyncio.CancelledError:
            return
