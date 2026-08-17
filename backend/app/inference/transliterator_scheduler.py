"""
AI Voice Clone Studio — the Roman-Urdu → Perso-Arabic transliterator scheduler.

Runs in the API process. NO TORCH HERE — same invariant as `scheduler.py` and
`analyzer_scheduler.py`, enforced by the same
`tests/test_contracts.py::test_no_torch_outside_runtimes`. The Gemma stack
lives only in `runtimes/gemma_transliterator.py`, in a worker subprocess this
class spawns and talks to over the unmodified wire protocol.

WHY THIS IS NOT SHAPED LIKE `AnalyzerScheduler`
------------------------------------------------
`AnalyzerScheduler` keeps its worker alive between calls and idle-unloads it
after five minutes. That works only because Qwen2.5-3B is ~6 GB and fits in
the slack left by `InferenceScheduler`'s 16 GB budget on a 24 GB card — a fact
its own docstring flags as a risk rather than a design.

Gemma-4-31B is ~19 GB at 4-bit. There is no slack. VoxCPM (7.3 GB) and
OmniVoice (4.7 GB) are both warmed at startup, so the arithmetic never works:
this model is resident or they are, never both. Keeping it alive between
calls would mean holding the GPU hostage between two conversions a user might
make an hour apart.

Hence LOAD-CONVERT-UNLOAD, every time:

    async with inference_scheduler.exclusive_gpu("transliterate"):
        spawn worker → LOAD (~78 s) → TRANSLITERATE → kill worker

`exclusive_gpu` holds the main scheduler's GPU slot for that entire window,
so no synthesis can start while Gemma is resident, and it empties the card
first so the 19 GB actually fits. The audio models come back COLD afterwards.

THAT COST IS THE DESIGN, NOT A DEFECT
--------------------------------------
A conversion costs ~78 s of load plus generation, and the next generation
after it pays a fresh audio load. Both were accepted knowingly when the
owner chose Gemma over Ministral: Ministral fits alongside everything and
would make this fast, and it is *already measured as not good enough by ear*
(A3 run 2, ten defects). A model that fits but produces کال for کل is not a
cheaper version of this feature.

The user-facing consequence — this is slow, and it stalls generation while it
runs — belongs in the UI, not hidden here.

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
#: The 4-bit checkpoint A3 run 3 actually passed on. The unquantized weights
#: do not fit this card at all, so this is not an optimisation — it is the
#: only form of the model that has ever run here.
#: NOTE THE CAPITAL B. `google/gemma-4-31b-it` is a 307 redirect to this;
#: using the lowercase form works but resolves through a redirect on every
#: cold load, and a pinned revision against a redirecting id is a worse thing
#: to debug than a corrected id.
GEMMA_TRANSLITERATOR_HF_REPO = "google/gemma-4-31B-it"
#: ACTUALLY PINNED (golden rule 7), resolved 2026-08-17 from
#: `https://huggingface.co/api/models/google/gemma-4-31b-it` -> `sha`.
#:
#: This said `"main"` until then, under a comment claiming it was pinned —
#: which is precisely the supply-chain hole rule 7 exists to close, and worse
#: than an honest `"main"` because the comment stopped anyone looking. The
#: repo is NOT gated, so no token is needed.
GEMMA_TRANSLITERATOR_HF_REVISION = "842da3794eaa0b77d5f08bae87a17459d91ff475"

_WORKER_RUNTIME = "gemma_transliterator"
#: 78.4 s measured cold on the pod, but that assumes warm HF cache and no
#: competing I/O. Generous, because the alternative to waiting is killing a
#: load that was nearly done and paying for it again.
_LOAD_TIMEOUT_SEC = 600.0
_CONVERT_TIMEOUT_SEC = 180.0


class TransliteratorScheduler:
    """
    Owns nothing between calls, by design.

    One conversion = one worker's whole lifetime. `convert()` is serialized
    against itself by `_lock` and against all audio work by the main
    scheduler's GPU slot.
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
    ) -> None:
        self._python = python_executable
        self._scheduler = inference_scheduler
        self._env = env
        self._cwd = cwd
        self._hf_repo = hf_repo
        self._hf_revision = hf_revision
        self._load_timeout_sec = load_timeout_sec
        self._convert_timeout_sec = convert_timeout_sec
        #: Serializes convert() against itself. `exclusive_gpu` would already
        #: serialize these, but two callers would then each pay a full ~78 s
        #: load back to back — this makes the second one wait for a result
        #: rather than start a second 19 GB download into the same card.
        self._lock = asyncio.Lock()

    async def convert(
        self, *, text: str, instruction: str = "", source_script: str = "latin"
    ) -> TransliterateResult:
        """
        Convert one passage. Loads Gemma, converts, and kills the worker —
        always, including on failure.

        `source_script` is `latin` (Roman Urdu) or `devanagari` (a Hindi
        caption). It is passed through as a plain string and NOT validated
        here: the runtime owns the prompt, so it owns which sources it knows,
        and it falls back to Latin for anything else. A second copy of that
        table in this process would be one more thing to keep in step.

        Returns the model's RAW output. Validation lives in
        `domain/transliterate.py` and is applied by the caller, because
        "is this a transliteration or an answer" is a pure question that
        should not require a GPU to test.
        """
        if not self._python:
            raise TransliteratorUnavailableError(
                "no interpreter configured for the gemma_transliterator runtime "
                "(set VCS_GEMMA_TRANSLITERATOR_PYTHON)"
            )

        async with self._lock:
            async with self._scheduler.exclusive_gpu("transliterate"):
                worker = WorkerProcess(
                    _WORKER_RUNTIME, self._python, env=self._env, cwd=self._cwd
                )
                try:
                    await worker.start()
                except Exception as exc:
                    raise TransliteratorUnavailableError(
                        f"gemma transliterator worker failed to start: {exc}"
                    ) from exc

                try:
                    t0 = time.time()
                    load = await worker.call(
                        WireOp.LOAD,
                        {
                            "model_id": GEMMA_TRANSLITERATOR_MODEL_ID,
                            "hf_repo": self._hf_repo,
                            "hf_revision": self._hf_revision,
                        },
                        timeout=self._load_timeout_sec,
                    )
                    if not load.ok:
                        raise TransliteratorUnavailableError(
                            f"gemma transliterator failed to load: "
                            f"{load.error_message or 'unknown'}"
                        )
                    load_sec = float(load.result.get("load_time_sec", time.time() - t0))
                    logger.info("gemma transliterator loaded in %.1fs", load_sec)

                    response = await worker.call(
                        WireOp.TRANSLITERATE,
                        {
                            "text": text,
                            "instruction": instruction,
                            "source_script": source_script,
                        },
                        timeout=self._convert_timeout_sec,
                    )
                    if not response.ok:
                        raise TransliteratorUnavailableError(
                            f"transliteration failed: "
                            f"{response.error_message or 'unknown'}"
                        )
                    return TransliterateResult(
                        text=str(response.result.get("text") or ""),
                        gen_time_sec=float(response.result.get("gen_time_sec", 0.0)),
                        load_time_sec=load_sec,
                    )
                finally:
                    # ALWAYS. A worker left alive here holds ~19 GB on a 24 GB
                    # card and every subsequent generation OOMs — and unlike
                    # the analyzer there is no idle timer to eventually clean
                    # it up, because there is no worker between calls to time.
                    # Kill, not a wire UNLOAD: that leaves fragmentation and a
                    # CUDA context behind, so the VRAM does not really return.
                    with contextlib.suppress(Exception):
                        await worker.kill()

    async def shutdown(self) -> None:
        """
        Nothing to do. There is deliberately no worker between calls, which is
        the one operational upside of load-convert-unload: this scheduler
        cannot leak a process across a restart.
        """
        return None
