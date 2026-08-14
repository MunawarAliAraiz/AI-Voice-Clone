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
  * `optimize` (torch.compile) is OFF by default. With it on, the first-ever
    cold load compiles for ~383s on a fresh inductor cache — past the
    scheduler's 300s load_timeout, so it would get the worker killed. Compile
    buys RTF ~0.83 vs ~1.05, so it is a deliberate opt-in that also needs a
    raised load timeout and a persistent TORCHINDUCTOR_CACHE_DIR. Off, load is
    seconds (weights cached) and the first request is normal speed, no trap.

LoRA loading (added for `voxcpm2_urdu_lora`, the Urdu bake-off's arm D) mirrors
`eval/run_urdu_bakeoff.py::_load_voxcpm` exactly, because that function is the
one that was actually measured end-to-end against real weights — this is a
port, not a reimplementation from the model card. Two things carry over
unchanged:
  * The LoRA shape (`enable_lm`/`enable_dit`/`enable_proj`/`r`/`alpha`/
    `dropout`) is read from the checkpoint's OWN `lora_config.json`, never
    hardcoded. Constructing the wrong shape does not raise — `load_lora()`
    silently drops mismatched weights and returns a real-looking number for
    the wrong checkpoint. This exact bug was found and fixed once already.
  * ANY skipped weight is a hard failure, not a warning. A partially-applied
    adapter is a model that is only partly the thing that was measured in the
    bake-off — shipping it anyway would mean the blind-listen scores no
    longer describe what is actually running.
"""

from __future__ import annotations

import contextlib
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

    def load(
        self, model_id: str, hf_repo: str, hf_revision: str,
        *, lora_local_path: str | None = None,
    ) -> float:
        t0 = time.time()
        from huggingface_hub import snapshot_download
        from voxcpm.core import VoxCPM

        kwargs: dict = {}
        if lora_local_path is not None:
            kwargs["lora_config"] = self._read_lora_config(lora_local_path)

        # Honour the pinned revision (golden rule 7): resolve the exact snapshot
        # on disk and load from that path, rather than trusting `main`.
        model_path = snapshot_download(repo_id=hf_repo, revision=hf_revision)
        try:
            self._model = VoxCPM(
                voxcpm_model_path=model_path, load_denoiser=False, optimize=False,
                **kwargs,
            )
        except TypeError:
            # Older/newer signature: fall back to from_pretrained (cache already
            # holds the pinned revision from the snapshot_download above).
            self._model = VoxCPM.from_pretrained(
                hf_repo, load_denoiser=False, optimize=False, **kwargs
            )
        if lora_local_path is not None:
            self._apply_lora(lora_local_path)
        self._sr = int(self._model.tts_model.sample_rate)
        self.loaded_model_id = model_id
        self._warm()
        return time.time() - t0

    @staticmethod
    def _read_lora_config(lora_local_path: str) -> Any:
        import json
        from pathlib import Path

        from voxcpm.model.voxcpm2 import LoRAConfig

        cfg_path = Path(lora_local_path) / "lora_config.json"
        if not cfg_path.exists():
            raise RuntimeError(
                f"{cfg_path} missing — cannot infer the LoRA shape safely. A "
                f"fresh pod needs this checkpoint restored (see "
                f"VCS_VOXCPM_URDU_LORA_DIR) before this spec can load."
            )
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))["lora_config"]
        keys = ("enable_lm", "enable_dit", "enable_proj", "r", "alpha", "dropout")
        return LoRAConfig(**{k: raw[k] for k in keys})

    def _apply_lora(self, lora_local_path: str) -> None:
        loaded, skipped = self._model.load_lora(lora_local_path)
        if skipped:
            # A skipped weight means the reconstructed adapter shape disagrees
            # with the checkpoint on disk — the loaded model is only PARTLY
            # the thing the bake-off measured. Serving it anyway would silently
            # decouple what runs from what was scored; refuse instead.
            raise RuntimeError(
                f"LoRA load for {lora_local_path!r} skipped {len(skipped)} of "
                f"{len(loaded) + len(skipped)} weights — refusing to serve a "
                f"partially-applied adapter."
            )

    def _warm(self) -> None:
        """
        Prime the cloning path once at load so the first real request doesn't
        pay CUDA kernel autotune / lazy-init latency. Cheap without compile.
        """
        import os

        ref = next((p for p in _WARMUP_REF_CANDIDATES if os.path.exists(p)), None)
        if ref is None or self._model is None:
            return
        # Best-effort: a warm-up failure must not fail the load.
        with contextlib.suppress(Exception):
            self._model.generate(
                text="warm up.", reference_wav_path=ref,
                cfg_value=2.0, inference_timesteps=10, normalize=False,
            )

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
        with contextlib.suppress(Exception):
            import gc

            import torch

            gc.collect()
            torch.cuda.empty_cache()
