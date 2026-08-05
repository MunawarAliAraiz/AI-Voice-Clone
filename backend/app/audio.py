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
import uuid
from dataclasses import dataclass
from pathlib import Path

import aiofiles
import numpy as np
import soundfile as sf
from fastapi import UploadFile

from .exceptions import AudioValidationError, UploadTooLargeError

__all__ = ["AudioMeta", "store_upload", "validate_audio"]

_CHUNK = 1 << 20  # 1 MiB
_MIN_DURATION_SEC = 0.5
_READABLE_EXT = {".wav", ".flac", ".ogg", ".opus", ".mp3", ".m4a", ".aiff"}


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
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in _READABLE_EXT:
        ext = ".wav"  # unknown container: let soundfile try, validation decides
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


def validate_audio(path: Path) -> AudioMeta:
    """Read `path` with libsndfile; raise `AudioValidationError` if unusable."""
    try:
        info = sf.info(str(path))
    except Exception as exc:
        raise AudioValidationError(
            "The file is not readable audio. Upload a WAV, FLAC, OGG, or MP3."
        ) from exc
    if info.frames == 0 or info.samplerate <= 0:
        raise AudioValidationError("The audio file contains no samples.")

    duration = info.frames / info.samplerate
    if duration < _MIN_DURATION_SEC:
        raise AudioValidationError(
            f"The reference is only {duration:.2f}s; at least "
            f"{_MIN_DURATION_SEC:.1f}s is needed to clone a voice."
        )

    data, sr = sf.read(str(path), always_2d=True)
    mono = data.mean(axis=1)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    peak_dbfs = round(20.0 * math.log10(peak), 2) if peak > 0 else None
    return AudioMeta(
        path=path, duration_sec=round(duration, 3), sample_rate=int(sr),
        channels=info.channels, peak_dbfs=peak_dbfs, is_clipped=peak >= 0.999,
    )
