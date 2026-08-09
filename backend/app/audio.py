"""
AI Voice Clone Studio — Audio utilities (CPU, no torch).

Reference quality dominates clone quality more than any model parameter, so an
upload is validated and measured once here — duration, sample rate, peak dBFS,
clipping — and the numbers are surfaced in the UI rather than rediscovered by
the user after a bad generation. A clipped reference cannot be rescued
downstream, so it is flagged at the door.

The upload is streamed to disk in bounded chunks; a large file never lands in
memory whole.
"""

from __future__ import annotations

import math
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import aiofiles
import numpy as np
import soundfile as sf
from fastapi import UploadFile

from .exceptions import AudioValidationError, UploadTooLargeError

__all__ = [
    "AudioMeta",
    "apply_audio_effects",
    "concat_wavs_with_pauses",
    "store_upload",
    "validate_audio",
]

_CHUNK = 1 << 20  # 1 MiB
_MIN_DURATION_SEC = 0.5
_READABLE_EXT = {
    ".wav", ".flac", ".ogg", ".opus", ".mp3", ".m4a", ".aiff", ".aac", ".wma",
    ".amr", ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".3gp"
}

def apply_audio_effects(path: Path, speed: float = 1.0) -> None:
    """
    Apply a speed (tempo) adjustment via ffmpeg's `atempo`, which is pitch-preserving
    and sample-rate independent — unlike the deleted "emotion" presets, which hardcoded
    a 24kHz `asetrate` regardless of the runtime's real output rate (VoxCPM emits 48kHz)
    and so audibly slowed and pitch-dropped four of seven presets. That DSP was never
    model conditioning — "emotion" text never reached the model — and "style exaggeration"
    was injected into synth params no runtime read. Both were removed outright rather than
    patched; see CLAUDE.md golden rule 1 (never dress up non-model output as an AI feature)
    and rule 5 (no dead knobs). Real per-model conditioning belongs in `params`, gated by the
    catalog's declared params — see `_validate_params` in `api/routers/tts.py`.

    If speed == 1.0, returns immediately without modifying the file (exact regression
    behavior for the common case).
    """
    if not path.exists():
        return
    if speed == 1.0 or not (0.5 <= speed <= 2.0):
        return

    tmp_path = path.with_suffix(f".fx_speed{speed}.wav")
    cmd = ["ffmpeg", "-y", "-i", str(path), "-filter:a", f"atempo={speed}", str(tmp_path)]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        # ffmpeg missing from PATH: leave the file at its original tempo rather
        # than fail the whole job — real model audio, just not re-timed. Same
        # choice as the format-conversion path in routers/media.py.
        tmp_path.unlink(missing_ok=True)
        return
    if res.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0:
        tmp_path.replace(path)
    else:
        tmp_path.unlink(missing_ok=True)



def concat_wavs_with_pauses(parts: list[Path], pauses_ms: list[int], out_path: Path) -> float:
    """
    Join WAV `parts` in order into `out_path`, inserting `pauses_ms[i]` of
    silence AFTER part i (the pause after the LAST part is dropped — no trailing
    silence). Returns the final duration in seconds.

    This is how Speech Direction becomes audible: each part is a separately
    synthesized segment carrying its own per-segment prosody, and the gaps are
    real inter-segment pauses. Every written sample is either a real segment or
    explicit silence between segments — nothing is fabricated (golden rule 1).

    All parts must share a sample rate and channel count; since they come from
    one model in one job that holds, and a mismatch raises rather than being
    papered over. Output is 16-bit PCM WAV (the pipeline's mono reference form).
    """
    if not parts:
        raise AudioValidationError("No audio segments to join.")

    blocks: list[np.ndarray] = []
    sr0: int | None = None
    last = len(parts) - 1
    for i, part in enumerate(parts):
        try:
            data, sr = sf.read(str(part), dtype="float32", always_2d=True)
        except Exception as exc:
            raise AudioValidationError(f"Segment {i} is not readable audio.") from exc
        if sr0 is None:
            sr0 = int(sr)
        elif int(sr) != sr0:
            raise AudioValidationError(f"Segment {i} sample rate {sr} != {sr0}; cannot join.")
        blocks.append(data)

        pause_ms = pauses_ms[i] if i < len(pauses_ms) else 0
        if pause_ms > 0 and i != last:
            frames = int(sr0 * pause_ms / 1000)
            if frames > 0:
                blocks.append(np.zeros((frames, data.shape[1]), dtype=np.float32))

    joined = np.concatenate(blocks, axis=0)
    sf.write(str(out_path), joined, sr0, subtype="PCM_16")
    return round(joined.shape[0] / sr0, 3)


@dataclass(frozen=True)
class AudioMeta:
    path: Path
    duration_sec: float
    sample_rate: int
    channels: int
    peak_dbfs: float | None  # None for pure silence
    is_clipped: bool


async def store_upload(upload: UploadFile, dest_dir: Path, *, max_bytes: int) -> Path:
    """Stream an upload to a uuid-named file under `dest_dir`, capping size."""
    dest_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 (one-shot metadata op)
    orig_ext = Path(upload.filename or "").suffix.lower()
    ext = orig_ext if orig_ext in _READABLE_EXT else ".wav"
    path = dest_dir / f"{uuid.uuid4().hex}{ext}"

    written = 0
    async with aiofiles.open(path, "wb") as f:
        while chunk := await upload.read(_CHUNK):
            written += len(chunk)
            if written > max_bytes:
                await f.close()
                path.unlink(missing_ok=True)
                raise UploadTooLargeError(
                    limit_mb=max_bytes // 1024 // 1024, actual_mb=written / 1024 / 1024
                )
            await f.write(chunk)
    if written == 0:
        path.unlink(missing_ok=True)
        raise AudioValidationError("The uploaded file is empty.")
    return path


def _transcode_to_wav(path: Path) -> Path:
    """Extract audio from video or transcode non-standard audio formats to WAV using ffmpeg."""
    wav_path = path.with_suffix(".extracted.wav")
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-vn", "-ac", "1", "-ar", "24000", str(wav_path)
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size == 0:
        wav_path.unlink(missing_ok=True)
        raise AudioValidationError("Could not extract or decode audio from the file. Ensure it contains a valid audio track.")
    # Replace original path with converted WAV
    path.unlink(missing_ok=True)
    target_path = path.with_suffix(".wav")
    wav_path.replace(target_path)
    return target_path


def validate_audio(path: Path) -> AudioMeta:
    """Read `path` with libsndfile (or ffmpeg fallback); raise `AudioValidationError` if unusable."""
    target_path = path
    try:
        info = sf.info(str(target_path))
    except Exception:
        # Fallback to ffmpeg audio extraction/transcoding for video/unsupported audio containers
        try:
            target_path = _transcode_to_wav(path)
            info = sf.info(str(target_path))
        except Exception as exc:
            raise AudioValidationError(
                "The file is not readable audio or video. Upload an audio or video file (WAV, MP3, MP4, MKV, etc.)."
            ) from exc

    if info.frames == 0 or info.samplerate <= 0:
        raise AudioValidationError("The audio file contains no samples.")

    duration = info.frames / info.samplerate
    if duration < _MIN_DURATION_SEC:
        raise AudioValidationError(
            f"The reference is only {duration:.2f}s; at least "
            f"{_MIN_DURATION_SEC:.1f}s is needed to clone a voice."
        )

    data, sr = sf.read(str(target_path), always_2d=True)
    mono = data.mean(axis=1)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    peak_dbfs = round(20.0 * math.log10(peak), 2) if peak > 0 else None
    return AudioMeta(
        path=target_path, duration_sec=round(duration, 3), sample_rate=int(sr),
        channels=info.channels, peak_dbfs=peak_dbfs, is_clipped=peak >= 0.999,
    )

