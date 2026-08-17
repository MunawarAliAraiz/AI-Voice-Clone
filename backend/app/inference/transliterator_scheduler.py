"""
AI Voice Clone Studio — the Roman-Urdu → Perso-Arabic transliterator scheduler.

Runs in the API process. NO TORCH HERE — same invariant as `scheduler.py` and
`analyzer_scheduler.py`, enforced by the same
`tests/test_contracts.py::test_no_torch_outside_runtimes`. The Gemma stack
lives only in `runtimes/gemma_transliterator.py`, in a worker subprocess this
class spawns and talks to over the unmodified wire protocol.

IT IS NOW SHAPED LIKE `AnalyzerScheduler`, AND IT WAS NOT
------------------------------------------------------------
Until 2026-08-17 this was LOAD-CONVERT-UNLOAD: spawn, load ~19 GB, convert,
kill the worker, every single time. That was correct for a 24 GB card, where
Gemma cannot coexist with VoxCPM (5.4 GB) and OmniVoice (4.7 GB).

**The first real GPU run killed the design.** Measured on an A40:

    generation   2.7 - 5.1 s
    load       327 -> 237 -> 188 -> 150 s   (falling as the page cache warms)
    peak       19221 MiB

97% of every conversion was reloading weights it had just discarded, and a
transcript of 45 chunks would have cost over two hours to convert four minutes
of text. The owner moved the design target to ~32 GB so this can stay resident;
at ~29.3 GB for all three models plus headroom that is the smallest card that
does. `inference/capacity.py` refuses a smaller one at startup rather than
letting it thrash.

So: the worker is held between calls and idle-killed, exactly as the analyzer
is. The one number that differs is the idle window, and it differs for a
measured reason — the analyzer reloads in seconds, this reloads in 150-330 s,
so its window is far longer. Killing this worker is expensive enough that it
should happen because the GPU is genuinely wanted elsewhere, not because
someone paused to read.

WHAT REPLACED `exclusive_gpu()`
--------------------------------
`reserve_slot()`. The load still takes the main scheduler's GPU slot, so no
synthesis can begin into a 19 GB allocation spike — but it EVICTS NOTHING,
because there is now room for everyone. Evicting would discard warm audio
models to make space that already exists, and cost a 176 s re-warm to save
nothing. That withdrawal restores golden rule 3 to a single eviction call site;
`scheduler.py`'s module docstring carries the full argument.

WHY IT IS NOT A `RuntimeKind` AND NOT IN THE CATALOG
-----------------------------------------------------
Exactly the analyzer's reasoning. `resolve()` must never be able to route a
TTS request here, and `TransformKind` is untouched: the output of this is
EDITABLE TEXT that a human reads and corrects before generating, not a
routing transform applied behind their back. Golden rules 4 and 5 are about
what happens without the user's knowledge; this feature's entire shape is
that the user sees and approves the result.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path

from ..exceptions import TransliteratorUnavailableError
from .protocol import TransliterateResult, WireOp
from .worker_client import WorkerProcess

__all__ = [
    "TransliteratorScheduler",
    "GEMMA_TRANSLITERATOR_MODEL_ID",
    "GEMMA_TRANSLITERATOR_HF_REPO",
    "GEMMA_TRANSLITERATOR_HF_REVISION",
]

logger = logging.getLogger("app.inference.transliterator_scheduler")

#: Not a `ModelSpec` / `CATALOG` entry on purpose — see the module docstring.
GEMMA_TRANSLITERATOR_MODEL_ID = "gemma-4-31b-it-transliterator"

#: **THE 4-BIT REPO, AND IT MUST BE THIS ONE.** `unsloth/…-unsloth-bnb-4bit` is
#: the checkpoint A3 run 3 passed on (`eval/run_a3_full_chain.py` names it), and
#: the only form of this model that has ever run here.
#:
#: This said `google/gemma-4-31B-it` — Google's full-precision release — for
#: most of a day, and it was wrong three times over. It downloads **59 GB**
#: rather than 19. It is not the checkpoint the gate was run against, so using
#: it would have quietly invalidated the one listening gate this feature has.
#: And it could not have loaded anyway: `GemmaTransliteratorBackend.load()`
#: takes its `dtype=torch.bfloat16` branch when a checkpoint carries no
#: `quantization_config`, which for 31B parameters is ~62 GB of VRAM — over the
#: A40 it was downloaded onto, and nearly triple the 24 GB card this is
#: designed for.
#:
#: The error was made while FIXING a golden-rule-7 violation: the revision was
#: the literal string `"main"` under a comment claiming it was pinned, and the
#: fix resolved a real sha — for the wrong repository. Pinning the wrong thing
#: precisely is not compliance with rule 7. Check the repo id against
#: `eval/`'s driver, not just the revision against the API.
GEMMA_TRANSLITERATOR_HF_REPO = "unsloth/gemma-4-31B-it-unsloth-bnb-4bit"
#: Pinned per golden rule 7; resolved 2026-08-17 from
#: `https://huggingface.co/api/models/unsloth/gemma-4-31B-it-unsloth-bnb-4bit`
#: -> `sha`. Not gated, so no token is needed.
GEMMA_TRANSLITERATOR_HF_REVISION = "8e256fc6d63003fc0ca8c91b976e6dcc38433385"

_WORKER_RUNTIME = "gemma_transliterator"

#: MEASURED, not guessed: 327 s cold on a MooseFS network volume, falling to
#: 150 s as the page cache warmed. The old 600 s allowed for a "78.4 s" figure
#: that a real run did not reproduce, so the margin it was assumed to have was
#: mostly imaginary. Kept generous anyway — killing a load that was nearly done
#: means paying for the whole thing again.
_LOAD_TIMEOUT_SEC = 900.0
#: Per CHUNK, not per batch. Generation measured 2.7–5.1 s for a sentence.
_CONVERT_TIMEOUT_SEC = 180.0

#: How long an idle Gemma keeps its ~19 GB before the worker is killed.
#:
#: 30 minutes, against `AnalyzerScheduler`'s 5, and the ratio is a measurement
#: rather than a preference: that one reloads in seconds, this one in
#: 150–330 s. An idle timer exists to return VRAM nobody is using; when
#: reclaiming costs five minutes to undo, it should fire because the GPU is
#: genuinely wanted elsewhere, not because someone paused to read a draft.
_IDLE_UNLOAD_SEC = 1800.0


class TransliteratorScheduler:
    """
    Holds one Gemma worker between calls, and idle-kills it.

    `convert()` and `convert_many()` are serialized against each other by
    `_worker_lock`, which is held across the wire calls and not merely around
    the start — the analyzer shipped with the narrow version of exactly this
    lock and produced a desynchronized stream, which the protocol treats as
    unrecoverable by design.
    """

    def __init__(
        self,
        *,
        python_executable: str,
        inference_scheduler,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        hf_repo: str = GEMMA_TRANSLITERATOR_HF_REPO,
        hf_revision: str = GEMMA_TRANSLITERATOR_HF_REVISION,
        load_timeout_sec: float = _LOAD_TIMEOUT_SEC,
        convert_timeout_sec: float = _CONVERT_TIMEOUT_SEC,
        idle_unload_sec: float = _IDLE_UNLOAD_SEC,
    ) -> None:
        self._python = python_executable
        self._scheduler = inference_scheduler
        self._env = env
        self._cwd = cwd
        self._hf_repo = hf_repo
        self._hf_revision = hf_revision
        self._load_timeout_sec = load_timeout_sec
        self._convert_timeout_sec = convert_timeout_sec
        self._idle_unload_sec = idle_unload_sec

        self._worker: WorkerProcess | None = None
        self._loaded = False
        self._pending_load_time_sec = 0.0
        self._worker_lock = asyncio.Lock()
        self._idle_task: asyncio.Task[None] | None = None
        self._last_activity = time.monotonic()
        self._shutting_down = False

    async def convert(
        self,
        *,
        text: str,
        instruction: str = "",
        source_script: str = "latin",
        target_script: str = "perso_arabic",
    ) -> TransliterateResult:
        """One passage. Kept as its own shape because most callers have one
        thing to convert and should not have to unwrap a list."""
        results = await self.convert_many(
            texts=[text],
            instruction=instruction,
            source_script=source_script,
            target_script=target_script,
        )
        return results[0]

    async def convert_many(
        self,
        *,
        texts: list[str],
        instruction: str = "",
        source_script: str = "latin",
        target_script: str = "perso_arabic",
    ) -> list[TransliterateResult]:
        """
        Convert several passages against ONE residency.

        Even resident, a per-chunk round trip is cheap and this saves little.
        The case it exists for is a COLD start: 45 chunks of a transcript would
        otherwise be 45 loads at 150–330 s each. Here they are one.

        Returns RAW output per item, in order. Validation lives in
        `domain/transliterate.py` and is applied by the caller — "is this a
        transliteration or an answer" is a pure question that should not need a
        GPU to test.

        ONE FAILING CHUNK RAISES AND THE CALLER DECIDES. The per-chunk-status
        policy lives in `jobs/handlers/transliterate.py`, because whether a
        transcript with one bad chunk is a failure is a product decision, not a
        fact about the wire.
        """
        if not self._python:
            raise TransliteratorUnavailableError(
                "no interpreter configured for the gemma_transliterator runtime "
                "(set VCS_GEMMA_TRANSLITERATOR_PYTHON)"
            )
        if not texts:
            return []

        async with self._worker_lock:
            load_sec = await self._ensure_ready()
            self._touch()
            out: list[TransliterateResult] = []
            try:
                for text in texts:
                    assert self._worker is not None
                    response = await self._worker.call(
                        WireOp.TRANSLITERATE,
                        {
                            "text": text,
                            "instruction": instruction,
                            "source_script": source_script,
                            "target_script": target_script,
                        },
                        timeout=self._convert_timeout_sec,
                    )
                    if not response.ok:
                        raise TransliteratorUnavailableError(
                            f"transliteration failed: {response.error_message or 'unknown'}"
                        )
                    out.append(
                        TransliterateResult(
                            text=str(response.result.get("text") or ""),
                            gen_time_sec=float(response.result.get("gen_time_sec", 0.0)),
                            # Only the FIRST item is charged for the load,
                            # because only the first one caused it. A caller
                            # summing these gets the real elapsed cost rather
                            # than N times a load that happened once.
                            load_time_sec=load_sec if not out else 0.0,
                        )
                    )
            except Exception:
                # A failed wire call may have left the stream desynchronized,
                # and the protocol's rule is kill-never-resync. Drop the worker
                # so the next call starts clean, at the cost of a reload.
                await self._drop_worker()
                raise
            finally:
                self._touch()
            return out

    async def warm(self) -> float:
        """
        Load Gemma now, so the first real conversion does not pay 150–330 s.

        Called from the app lifespan. Raises if no interpreter is configured,
        which the caller treats as "this deployment has no transliterator"
        rather than as a startup failure — a pod without `.venv-gemma` must
        still serve every other job kind.
        """
        if not self._python:
            raise TransliteratorUnavailableError(
                "no interpreter configured for the gemma_transliterator runtime "
                "(set VCS_GEMMA_TRANSLITERATOR_PYTHON)"
            )
        async with self._worker_lock:
            sec = await self._ensure_ready()
            self._touch()
            return sec

    async def shutdown(self) -> None:
        """Kill the worker if alive, cancel the idle timer. Idempotent."""
        self._shutting_down = True
        if self._idle_task is not None:
            self._idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_task
            self._idle_task = None
        await self._drop_worker()

    # ── internals ────────────────────────────────────────────────────────────

    async def _ensure_ready(self) -> float:
        """Start and load the worker if needed. Caller holds `_worker_lock`."""
        if self._worker is not None and self._worker.is_alive and self._loaded:
            return 0.0
        if self._worker is None or not self._worker.is_alive:
            await self._start_worker()
        if not self._loaded:
            await self._load()
        sec, self._pending_load_time_sec = self._pending_load_time_sec, 0.0
        return sec

    async def _start_worker(self) -> None:
        worker = WorkerProcess(_WORKER_RUNTIME, self._python, env=self._env, cwd=self._cwd)
        try:
            await worker.start()
        except Exception as exc:
            raise TransliteratorUnavailableError(
                f"gemma transliterator worker failed to start: {exc}"
            ) from exc
        self._worker = worker
        self._loaded = False
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_loop())

    async def _load(self) -> None:
        """
        Load ~19 GB, holding the main scheduler's GPU slot for the duration.

        `reserve_slot`, not the `exclusive_gpu` this used to call: the slot
        stops a synthesis beginning into the allocation spike, and NOTHING is
        evicted, because at the ~32 GB design target there is room for all of
        them. See `scheduler.py`'s module docstring for why that withdrew a
        golden rule 3 amendment rather than merely renaming a method.
        """
        assert self._worker is not None
        t0 = time.time()
        async with self._scheduler.reserve_slot("transliterate-load"):
            response = await self._worker.call(
                WireOp.LOAD,
                {
                    "model_id": GEMMA_TRANSLITERATOR_MODEL_ID,
                    "hf_repo": self._hf_repo,
                    "hf_revision": self._hf_revision,
                },
                timeout=self._load_timeout_sec,
            )
        if not response.ok:
            await self._drop_worker()
            raise TransliteratorUnavailableError(
                f"gemma transliterator failed to load: {response.error_message or 'unknown'}"
            )
        self._loaded = True
        self._pending_load_time_sec = float(
            response.result.get("load_time_sec", time.time() - t0)
        )
        logger.info("gemma transliterator loaded in %.1fs", self._pending_load_time_sec)

    async def _drop_worker(self) -> None:
        """
        Kill the worker, never a wire UNLOAD.

        An UNLOAD leaves fragmentation and a CUDA context behind, so the ~19 GB
        does not really come back — which is the entire point of an idle timer.
        Killing the process is what has actually been measured to return it
        (`scripts/phase_b_smoke.py`: 19221 MiB → 0, four times running).
        """
        if self._worker is not None:
            with contextlib.suppress(Exception):
                await self._worker.kill()
            self._worker = None
        self._loaded = False

    def _touch(self) -> None:
        self._last_activity = time.monotonic()

    async def _idle_loop(self) -> None:
        """Kill the worker after `_idle_unload_sec` with no conversions."""
        try:
            while True:
                remaining = self._idle_unload_sec - (time.monotonic() - self._last_activity)
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    continue
                async with self._worker_lock:
                    idle_for = time.monotonic() - self._last_activity
                    if self._worker is not None and idle_for >= self._idle_unload_sec:
                        logger.info(
                            "gemma transliterator idle for %.0fs; killing worker "
                            "to release ~19 GB",
                            idle_for,
                        )
                        await self._drop_worker()
                await asyncio.sleep(self._idle_unload_sec)
        except asyncio.CancelledError:
            return
