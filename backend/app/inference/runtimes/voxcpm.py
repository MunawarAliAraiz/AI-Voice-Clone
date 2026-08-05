"""
AI Voice Clone Studio — VoxCPM2 runtime backend.

Torch lives here (allowed: this is under `inference/runtimes/`). Wraps the
`voxcpm` package's validated load + generate into the `RuntimeBackend` contract.

Design facts fixed by Phase-A / E1-E2 validation (2026-08-05, RTX 4000 Ada):
  * Cloning path is `reference_wav_path` — timbre only, NO reference transcript.
    This is the product-representative, cross-lingual-correct path (identity
    survives language changes: measured cosine 0.68-0.89).
  * Output sample rate is the model's own (`tts_model.sample_rate`, 48000);
    we write at that rate and report it — never silently resample to 44100.
  * `cfg_value` default 2.0 (2.0-2.5 sound natural by ear; 1.5/3.0 drift).
  * `optimize=True` + first real clone = ~74s torch.compile warm-up trap, so we
    warm the cloning path at load with a bundled reference.
"""

from __future__ import annotations

import time
from typing import Any

__all__ = ["VoxCPMBackend"]

#: Bundled Apache-2.0 example clip shipped with the voxcpm repo, used only to
#: warm the compiled cloning path at load. Never presented as a voice.
_WARMUP_REF_CANDIDATES = (
    "/workspace/vox/VoxCPM_repo/examples/reference_speaker.wav",
)


class VoxCPMBackend:
    """One VoxCPM process. Holds at most one loaded checkpoint."""

    runtime = "voxcpm"

    def __init__(self) -> None:
        self._model: Any = None
        self._sr: int | None = None
        self.loaded_model_id: str | None = None

    def load(self, model_id: str, hf_repo: str, hf_revision: str) -> float:
        t0 = time.time()
        from huggingface_hub import snapshot_download
        from voxcpm.core import VoxCPM

        # Honour the pinned revision (golden rule 7): resolve the exact snapshot
        # on disk and load from that path, rather than trusting `main`.
        model_path = snapshot_download(repo_id=hf_repo, revision=hf_revision)
        try:
            self._model = VoxCPM(
                voxcpm_model_path=model_path, load_denoiser=False, optimize=True
            )
        except TypeError:
            # Older/newer signature: fall back to from_pretrained (cache already
            # holds the pinned revision from the snapshot_download above).
            self._model = VoxCPM.from_pretrained(
                hf_repo, load_denoiser=False, optimize=True
            )
        self._sr = int(self._model.tts_model.sample_rate)
        self.loaded_model_id = model_id
        self._warm()
        return time.time() - t0

    def _warm(self) -> None:
        """Compile the cloning path once so the first real clone isn't +74s."""
        import os

        ref = next((p for p in _WARMUP_REF_CANDIDATES if os.path.exists(p)), None)
        if ref is None or self._model is None:
            return
        try:
            self._model.generate(
                text="warm up.", reference_wav_path=ref,
                cfg_value=2.0, inference_timesteps=10, normalize=False,
            )
        except Exception:
            pass  # warm-up is best-effort; a failure here must not fail load

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

        cfg = float(params.get("cfg_value", 2.0))
        steps = int(params.get("inference_timesteps", 10))
        t0 = time.time()
        audio = self._model.generate(
            text=text, reference_wav_path=reference_audio,
            cfg_value=cfg, inference_timesteps=steps, normalize=False,
        )
        gen = time.time() - t0
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
        try:
            import gc

            import torch

            gc.collect()
            torch.cuda.empty_cache()
        except Exception:
            pass
