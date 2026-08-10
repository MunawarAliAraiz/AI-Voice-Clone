"""
AI Voice Clone Studio — Chatterbox Multilingual runtime backend.

Torch lives here (allowed: this is under `inference/runtimes/`). Wraps the
`chatterbox-tts` package's load + generate into the `RuntimeBackend` contract,
mirroring `voxcpm.py`'s shape.

Design facts fixed by Phase 4b pod introspection (2026-08-10/11,
`chatterbox-tts==0.1.7`, verified via `inspect`/`HfApi` on the pod, not assumed
from docs — see docs/PHASE4_CHATTERBOX_DESIGN.md §3):

  * `ChatterboxMultilingualTTS.from_pretrained()` hardcodes `revision="main"`
    internally — calling it would violate golden rule 7 (pin every HF
    revision). This backend instead does its own pinned `snapshot_download`
    and loads via `from_local(ckpt_dir, device)`, which has no revision
    opinion of its own, exactly the pattern `voxcpm.py` already uses for the
    same reason.
  * The installed package's `from_local()` only ever loads the v2 T3
    checkpoint (`t3_mtl23ls_v2.safetensors`) — the HF repo also has a v3 file,
    but this package version has no code path to it. `catalog.py`'s
    "Chatterbox Multilingual v3" display name is therefore not accurate for
    what actually loads; flagged for the owner in the design doc §10, not
    silently fixed here.
  * Output sample rate is fixed at 24000 Hz (`model.sr`, from the verified
    `S3GEN_SR` constant) — reported as-is, never silently resampled, same
    discipline as VoxCPM's 48000 Hz.
  * `generate()`'s own `language_id` validation lowercases and checks against
    `SUPPORTED_LANGUAGES`, which contains `'en'`/`'hi'` matching this
    project's `LanguageCode` values exactly — the `params["language_id"]`
    Phase 4a's `render()` already injects needs no translation here.
  * `audio_prompt_path` is optional in `generate()` (unlike VoxCPM, which
    requires a reference wav) — still always passed here, since this backend
    only ever serves cloning requests, never a builtin-voice fallback.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

__all__ = ["ChatterboxBackend"]

#: Only the files `from_local()` actually reads (verified from its source) —
#: a smaller set than `from_pretrained()`'s own `allow_patterns`, which also
#: fetches an unused `Cangjie5_TC.json`. `conds.pt` is optional (a builtin
#: voice) but harmless to request; missing it from a repo snapshot is not an
#: error.
_CKPT_ALLOW_PATTERNS = (
    "ve.pt",
    "t3_mtl23ls_v2.safetensors",
    "s3gen.pt",
    "grapheme_mtl_merged_expanded_v1.json",
    "conds.pt",
)


class ChatterboxBackend:
    """One Chatterbox process. Holds at most one loaded checkpoint."""

    runtime = "chatterbox"

    def __init__(self) -> None:
        self._model: Any = None
        self._sr: int | None = None
        self.loaded_model_id: str | None = None

    def load(self, model_id: str, hf_repo: str, hf_revision: str) -> float:
        t0 = time.time()
        import torch
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        from huggingface_hub import snapshot_download

        # Honour the pinned revision (golden rule 7): from_pretrained() cannot
        # do this (see module docstring), so resolve the exact snapshot on
        # disk ourselves and load from that path via the lower-level
        # from_local(), same as voxcpm.py's own snapshot_download + load.
        ckpt_dir = snapshot_download(
            repo_id=hf_repo, revision=hf_revision, allow_patterns=list(_CKPT_ALLOW_PATTERNS)
        )
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model = ChatterboxMultilingualTTS.from_local(ckpt_dir, device)
        self._sr = int(self._model.sr)
        self.loaded_model_id = model_id
        self._warm()
        return time.time() - t0

    def _warm(self) -> None:
        """
        Prime the generation path once at load so the first real request
        doesn't pay CUDA kernel autotune / lazy-init latency. Best-effort: a
        warm-up failure must not fail the load.

        Unlike VoxCPM there is no bundled example reference clip shipped with
        this package to warm the cloning path specifically — this only
        exercises the non-cloning T3/S3Gen path (no `audio_prompt_path`).
        Revisit at Phase 4c pod validation if the first real cloned request
        still pays a cold-kernel cost worth avoiding.
        """
        if self._model is None:
            return
        with contextlib.suppress(Exception):
            self._model.generate(text="Warm up.", language_id="en")

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

        exaggeration = float(params.get("exaggeration", 0.5))
        cfg_weight = float(params.get("cfg_weight", 0.5))
        # Injected by app/jobs/direction.py's render() (Phase 4a) — SynthRequest
        # itself carries no language field. "en" matches this project's own
        # LanguageCode default and Chatterbox's SUPPORTED_LANGUAGES.
        language_id = str(params.get("language_id", "en"))

        t0 = time.time()
        wav = self._model.generate(
            text=text,
            language_id=language_id,
            audio_prompt_path=reference_audio,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
        )
        gen = time.time() - t0
        audio = wav.squeeze().cpu().numpy()
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
