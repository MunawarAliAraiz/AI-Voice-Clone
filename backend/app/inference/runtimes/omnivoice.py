"""
AI Voice Clone Studio — OmniVoice runtime backend.

Torch lives here (allowed: this is under `inference/runtimes/`). Wraps the
`omnivoice` package's load + generate into the `RuntimeBackend` contract,
mirroring `chatterbox.py`'s shape.

LICENSE, READ THIS FIRST: OmniVoice ships Apache-2.0 CODE but **CC-BY-NC
WEIGHTS** — confirmed on the HF card, separately from the GitHub `LICENSE`
file (see docs/URDU_MODEL_LICENSING.md's "trap this report exists to catch").
Non-commercial. Permitted here for the owner's personal use behind the
`VCS_API_KEY` gate only, per golden rule 6 as amended 2026-08-15 — never for
a shipped product. `catalog.py`'s spec carries `License.CC_BY_NC` and the
scheduler/UI must never claim otherwise.

Design facts, ported from the proven `eval/run_urdu_bakeoff.py::_load_omnivoice`
(bake-off arm E, real pod run, 2026-08-14 — 5.0/5 pronunciation, the single
best result of the whole bake-off):

  * `OmniVoice.from_pretrained(repo, device_map=..., dtype=torch.float16)` is
    the verified call shape against the installed package. Unlike Chatterbox,
    the eval driver never exercised a pinned `revision=` kwarg (it only
    *recorded* whatever `main` resolved to, via a separate `HfApi` call, for
    reproducibility bookkeeping — it did not pin the load itself). This
    backend passes `revision=hf_revision` directly, following the standard HF
    `from_pretrained` convention golden rule 7 requires — but that specific
    call has NOT been pod-verified the way Chatterbox's `from_local` bypass
    was. If the pod smoke test shows `revision` is silently ignored (the
    exact Chatterbox trap), switch to the same `snapshot_download` +
    load-from-local-path bypass `chatterbox.py` and `voxcpm.py` both use.
  * `generate(text, language=None, ref_text=None, ref_audio=None, ...)`
    returns `list[np.ndarray]` (verified against the installed package, not
    assumed from docs) — this backend takes `audio[0]`.
  * `language="ur"` is always passed explicitly, never left to auto-detect.
    OmniVoice genuinely lists Urdu (211.27 h training data) — naming it
    removes language-detection as a variable, which is the exact failure
    mode suspected for VoxCPM2 (Urdu is absent from its language list, so
    Perso-Arabic input may be read as Arabic).
  * `ref_text` is optional (the model Whisper-transcribes the reference when
    omitted) but is passed when the caller supplies a non-empty
    `reference_text`, to remove a source of run-to-run variance.
  * Sample rate: read from `model.sample_rate` or `model.sr`, falling back to
    24000 (the bake-off's measured rate) only if neither attribute exists —
    never silently assumed to save an attribute lookup.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

__all__ = ["OmniVoiceBackend"]


class OmniVoiceBackend:
    """One OmniVoice process. Holds at most one loaded checkpoint."""

    runtime = "omnivoice"

    def __init__(self) -> None:
        self._model: Any = None
        self._sr: int | None = None
        self.loaded_model_id: str | None = None

    def load(self, model_id: str, hf_repo: str, hf_revision: str) -> float:
        t0 = time.time()
        import torch
        from omnivoice import OmniVoice

        self._model = OmniVoice.from_pretrained(
            hf_repo,
            revision=hf_revision,
            device_map="cuda:0" if torch.cuda.is_available() else "cpu",
            dtype=torch.float16,
        )
        self._sr = int(
            getattr(self._model, "sample_rate", None)
            or getattr(self._model, "sr", None)
            or 24000
        )
        self.loaded_model_id = model_id
        self._warm()
        return time.time() - t0

    def _warm(self) -> None:
        """
        Prime the generation path once at load, same rationale as
        `chatterbox.py`'s `_warm()`: best-effort, must never fail the load.
        No bundled reference clip ships with this package either, so this
        only exercises the non-cloning path.
        """
        if self._model is None:
            return
        with contextlib.suppress(Exception):
            self._model.generate(text="Warm up.", language="en")

    def synth(
        self,
        *,
        text: str,
        reference_audio: str,
        output_path: str,
        params: dict[str, Any],
        sample_rate: int,
        reference_text: str | None = None,
    ) -> dict[str, Any]:
        import soundfile as sf

        if self._model is None or self._sr is None:
            raise RuntimeError("synth called before load")

        # OmniVoice's own kwarg is called `language`, distinct from
        # Chatterbox's `language_id` — both are injected by
        # app/jobs/direction.py's render() under whichever name the
        # runtime's params schema declares.
        language = str(params.get("language", "ur"))
        kwargs: dict[str, Any] = {}
        if reference_text:
            kwargs["ref_text"] = reference_text

        t0 = time.time()
        result = self._model.generate(
            text=text, language=language, ref_audio=reference_audio, **kwargs
        )
        gen = time.time() - t0
        audio = result[0] if isinstance(result, list) else result
        audio = audio.squeeze() if hasattr(audio, "squeeze") else audio
        sf.write(output_path, audio, self._sr)
        return {
            "duration_sec": len(audio) / self._sr,
            "gen_time_sec": gen,
            "sample_rate": self._sr,
        }

    def unload(self) -> None:
        self._model = None
        self._sr = None
        self.loaded_model_id = None
        with contextlib.suppress(Exception):
            import gc

            import torch

            gc.collect()
            torch.cuda.empty_cache()
