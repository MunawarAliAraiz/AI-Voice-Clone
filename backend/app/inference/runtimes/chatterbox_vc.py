"""Pinned Chatterbox voice conversion for an isolated GPU interpreter.

Consumes one speech stem/chunk plus an authorized target reference. Speaker
discovery, separation, long-recording assembly and authorization belong to the
orchestrator. This adapter never downloads weights or claims listening quality.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

MODEL_ID = "chatterbox_vc"
HF_REPO = "ResembleAI/chatterbox"
HF_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
WEIGHT_SHA256 = "2b78103c654207393955e4900aac14a12de8ef25f4b09424f1ef91941f161d4e"
MAX_SOURCE_SEC = 30.0
MAX_REFERENCE_SEC = 60.0
MAX_INPUT_BYTES = 50 * 1024 * 1024


class ConversionBackend(Protocol):
    sr: int

    def generate(self, audio: str, target_voice_path: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class ConversionResult:
    model_id: str
    hf_repo: str
    hf_revision: str
    source_sha256: str
    target_sha256: str
    output_sha256: str
    source_duration_sec: float
    duration_sec: float
    sample_rate: int
    generation_time_sec: float
    load_time_sec: float
    reference_effective_duration_sec: float
    quality_status: str = "awaiting_listening_review"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _synchronize_cuda() -> None:
    import torch

    torch.cuda.synchronize()


def _load_pinned(snapshot: Path) -> ConversionBackend:
    """Mirror 0.1.7's local loader while omitting optional builtin voice pickle."""
    if importlib.metadata.version("chatterbox-tts") != "0.1.7":
        raise ValueError("Voice conversion requires chatterbox-tts 0.1.7")
    if importlib.metadata.version("s3tokenizer") != "0.3.0":
        raise ValueError("Voice conversion requires the pinned s3tokenizer 0.3.0")
    import torch
    from chatterbox.models.s3gen import S3Gen
    from chatterbox.vc import ChatterboxVC
    from safetensors.torch import load_file

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; voice conversion cannot run")
    s3gen = S3Gen()
    incompatible = s3gen.load_state_dict(load_file(snapshot / "s3gen.safetensors"), strict=False)
    # These derived buffers are named explicitly by S3Gen.ignore_state_dict_missing
    # in the inspected 0.1.7 package. Any other omission is a partial checkpoint.
    allowed = {"tokenizer._mel_filters", "tokenizer.window"}
    if set(incompatible.missing_keys) - allowed or incompatible.unexpected_keys:
        raise ValueError("Voice conversion checkpoint does not match the pinned runtime")
    s3gen.to("cuda").eval()
    return ChatterboxVC(s3gen, "cuda", ref_dict=None)


def _copy_verified_wav(source: Path, destination: Path, expected: str, limit: float) -> float:
    import soundfile as sf

    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected.lower()):
        raise ValueError("Audio authorization requires a SHA256 file hash")
    # The model reads this immutable request copy, not a path that can change
    # after its authorization hash has been checked.
    with source.open("rb") as original, destination.open("xb") as copied:
        count = 0
        for block in iter(lambda: original.read(1024 * 1024), b""):
            count += len(block)
            if count > MAX_INPUT_BYTES:
                raise ValueError("Audio input exceeds 50 MB")
            copied.write(block)
    if sha256_file(destination) != expected.lower():
        raise ValueError("Audio file does not match the authorized hash")
    info = sf.info(destination)
    if info.format not in {"WAV", "WAVEX"} or not 0 < info.duration <= limit:
        raise ValueError(f"Expected a nonempty WAV of at most {limit:g} seconds")
    if not 1 <= info.channels <= 2:
        raise ValueError("Voice conversion accepts mono or stereo WAV input")
    return info.duration


class ChatterboxVCAdapter:
    """Load once, convert serially; the caller owns GPU admission and shutdown."""

    def __init__(
        self, *, factory: Callable[[Path], ConversionBackend] | None = None,
        synchronize: Callable[[], None] | None = None,
    ) -> None:
        self._factory = factory or _load_pinned
        self._synchronize = synchronize or (_synchronize_cuda if factory is None else lambda: None)
        self._backend: ConversionBackend | None = None
        self.load_time_sec = 0.0

    def load(self, snapshot_dir: Path, *, revision: str = HF_REVISION) -> float:
        self._backend = None
        self.load_time_sec = 0.0
        if revision != HF_REVISION or snapshot_dir.name != HF_REVISION:
            raise ValueError("Provide the exact pinned Chatterbox snapshot directory")
        weights = snapshot_dir / "s3gen.safetensors"
        if not weights.is_file() or sha256_file(weights) != WEIGHT_SHA256:
            raise ValueError("Chatterbox voice conversion checkpoint hash mismatch")
        started = time.perf_counter()
        backend = self._factory(snapshot_dir)
        self._synchronize()
        if int(backend.sr) != 24_000:
            raise ValueError("Pinned Chatterbox voice conversion must output 24000 Hz")
        self._backend = backend
        self.load_time_sec = time.perf_counter() - started
        return self.load_time_sec

    def convert(
        self, *, source_audio: Path, source_sha256: str, target_reference: Path,
        target_sha256: str, output_path: Path,
    ) -> ConversionResult:
        import numpy as np
        import soundfile as sf

        if self._backend is None:
            raise RuntimeError("Load the pinned voice conversion model first")
        if output_path.exists() or output_path.is_symlink():
            raise FileExistsError("Choose a new output path")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="vcs-vc-audio-") as workdir:
                work = Path(workdir)
                source = work / "source.wav"
                target = work / "target.wav"
                source_duration = _copy_verified_wav(
                    source_audio, source, source_sha256, MAX_SOURCE_SEC,
                )
                target_duration = _copy_verified_wav(
                    target_reference, target, target_sha256, MAX_REFERENCE_SEC,
                )
                self._synchronize()
                started = time.perf_counter()
                waveform = self._backend.generate(audio=str(source), target_voice_path=str(target))
                self._synchronize()
                elapsed = time.perf_counter() - started
                if hasattr(waveform, "detach"):
                    waveform = waveform.detach().cpu().numpy()
                data = np.asarray(waveform, dtype=np.float32)
                if data.ndim == 2 and data.shape[0] == 1:
                    data = data[0]
                if data.ndim != 1 or not data.size or not np.isfinite(data).all():
                    raise ValueError("Voice conversion returned invalid audio")
                if not np.any(data) or np.max(np.abs(data)) > 1.0:
                    raise ValueError("Voice conversion returned silent or clipping audio")
                with tempfile.NamedTemporaryFile(
                    prefix=".vcs-vc-", suffix=".wav", dir=output_path.parent, delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                sf.write(temporary, data, int(self._backend.sr), format="WAV", subtype="PCM_24")
                output_hash = sha256_file(temporary)
                # Atomic publication with no overwrite, including a concurrent result.
                os.link(temporary, output_path)
                return ConversionResult(
                    model_id=MODEL_ID, hf_repo=HF_REPO, hf_revision=HF_REVISION,
                    source_sha256=source_sha256.lower(), target_sha256=target_sha256.lower(),
                    output_sha256=output_hash, source_duration_sec=source_duration,
                    duration_sec=data.size / self._backend.sr, sample_rate=int(self._backend.sr),
                    generation_time_sec=elapsed, load_time_sec=self.load_time_sec,
                    reference_effective_duration_sec=min(target_duration, 10.0),
                )
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def unload(self) -> None:
        self._backend = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args()
    protocol_out = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        adapter = ChatterboxVCAdapter()
        try:
            if args.request.stat().st_size > 16 * 1024:
                raise ValueError("Conversion request is too large")
            request = json.loads(args.request.read_text(encoding="utf-8"))
            adapter.load(Path(request["snapshot_dir"]))
            result = adapter.convert(
                source_audio=Path(request["source_audio"]), source_sha256=request["source_sha256"],
                target_reference=Path(request["target_reference"]),
                target_sha256=request["target_sha256"], output_path=Path(request["output_path"]),
            )
            protocol_out.write(json.dumps({"ok": True, "result": asdict(result)}) + "\n")
            return 0
        except Exception as error:
            # Dependency exceptions may contain private paths or access details.
            protocol_out.write(json.dumps({
                "ok": False, "error": type(error).__name__,
                "detail": "Voice conversion failed; no successful result",
            }) + "\n")
            return 1
        finally:
            adapter.unload()


if __name__ == "__main__":
    raise SystemExit(main())
