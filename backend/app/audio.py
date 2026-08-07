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

__all__ = ["AudioMeta", "apply_audio_effects", "store_upload", "validate_audio"]

_CHUNK = 1 << 20  # 1 MiB
_MIN_DURATION_SEC = 0.5
_READABLE_EXT = {
    ".wav", ".flac", ".ogg", ".opus", ".mp3", ".m4a", ".aiff", ".aac", ".wma",
    ".amr", ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".3gp"
}

EMOTION_FILTERS: dict[str, list[str]] = {
    "neutral": [],
    "happy": ["atempo=1.05", "asetrate=24000*1.04", "aresample=24000", "volume=1.1"],
    "sad": ["atempo=0.86", "asetrate=24000*0.95", "aresample=24000", "volume=0.9"],
    "angry": ["atempo=1.10", "asetrate=24000*1.03", "aresample=24000", "volume=1.25"],
    "excited": ["atempo=1.15", "asetrate=24000*1.08", "aresample=24000", "volume=1.2"],
    "calm": ["atempo=0.88", "asetrate=24000*0.97", "aresample=24000", "volume=0.92"],
    "whisper": ["atempo=0.92", "highpass=f=250", "lowpass=f=3800", "volume=0.75"],
    "narration": ["atempo=0.95", "equalizer=f=120:width_type=h:width=200:g=2", "volume=1.05"],
}


def apply_audio_effects(path: Path, speed: float = 1.0, emotion: str = "neutral") -> None:
    """
    Apply speed and emotion DSP adjustments via ffmpeg.
    If emotion is 'neutral' (or unsupported) and speed == 1.0, returns immediately
    without modifying the file (guaranteeing exact regression behavior).
    """
    if not path.exists():
        return
    clean_emotion = emotion.lower().strip() if emotion else "neutral"
    filters = list(EMOTION_FILTERS.get(clean_emotion, []))

    if speed != 1.0 and 0.5 <= speed <= 2.0:
        filters.insert(0, f"atempo={speed}")

    if not filters:
        return

    filter_str = ",".join(filters)
    tmp_path = path.with_suffix(f".fx_{clean_emotion}_{speed}.wav")
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-filter:a", filter_str, str(tmp_path)
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0:
        tmp_path.replace(path)
    else:
        tmp_path.unlink(missing_ok=True)



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

