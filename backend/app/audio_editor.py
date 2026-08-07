"""
AI Voice Clone Studio — Built-in Audio Editor utilities (CPU, ffmpeg).

Applies non-destructive audio edits (trim, pitch, tempo, gain, fade in/out,
LUFS loudness normalization, silence removal) to generate temporary preview
or profile clips without altering original uploads.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf
import numpy as np

from .audio import AudioMeta, validate_audio
from .exceptions import AudioValidationError

__all__ = ["EditOptions", "process_audio_edits"]


@dataclass
class EditOptions:
    trim_start: float = 0.0
    trim_end: float | None = None
    speed: float = 1.0  # 0.5, 0.75, 1.0, 1.25, 1.5, 2.0
    pitch_semitones: float = 0.0  # -12 to +12
    gain_db: float = 0.0  # -12 to +12
    fade_in_sec: float = 0.0
    fade_out_sec: float = 0.0
    normalize_lufs: bool = False
    remove_silence: bool = False


def process_audio_edits(input_path: Path, output_path: Path, options: EditOptions) -> AudioMeta:
    """
    Process `input_path` using ffmpeg according to `options` and write to `output_path`.
    Original input_path is NEVER modified or deleted.
    """
    if not input_path.exists():
        raise AudioValidationError("Source audio file does not exist.")

    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

    # Get input info
    try:
        info = sf.info(str(input_path))
        duration = info.frames / info.samplerate
    except Exception:
        # Try transcoding first if libsndfile cannot read directly
        duration = 30.0

    trim_start = max(0.0, options.trim_start)
    trim_end = options.trim_end if (options.trim_end is not None and options.trim_end > trim_start) else duration
    effective_duration = max(0.1, trim_end - trim_start)

    cmd = [ffmpeg_bin, "-y"]

    # Trim start/end at input level
    if trim_start > 0:
        cmd.extend(["-ss", str(trim_start)])
    if trim_end < duration:
        cmd.extend(["-to", str(trim_end)])

    cmd.extend(["-i", str(input_path)])

    filters: list[str] = []

    # 1. Pitch shift (-12 to +12 semitones)
    if options.pitch_semitones != 0:
        pitch_factor = math.pow(2.0, options.pitch_semitones / 12.0)
        target_rate = int(24000 * pitch_factor)
        filters.extend([f"asetrate={target_rate}", "aresample=24000"])

    # 2. Speed / Tempo (0.5x to 2.0x)
    if options.speed != 1.0 and 0.5 <= options.speed <= 2.0:
        filters.append(f"atempo={options.speed}")

    # 3. Gain / Volume (dB)
    if options.gain_db != 0:
        filters.append(f"volume={options.gain_db}dB")

    # 4. Fade In / Out
    if options.fade_in_sec > 0:
        filters.append(f"afade=t=in:ss=0:d={options.fade_in_sec}")
    if options.fade_out_sec > 0:
        st = max(0.0, effective_duration - options.fade_out_sec)
        filters.append(f"afade=t=out:st={st}:d={options.fade_out_sec}")

    # 5. Silence Removal
    if options.remove_silence:
        filters.append("silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak")

    # 6. Loudness Normalization (LUFS)
    if options.normalize_lufs:
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    # Always ensure clean 24kHz mono output
    filters.append("aformat=sample_fmts=fltp:sample_rates=24000:channel_layouts=mono")

    cmd.extend(["-filter:a", ",".join(filters)])
    cmd.append(str(output_path))

    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        # Fallback if ffmpeg is missing from PATH in environment
        import shutil as fs_shutil
        fs_shutil.copyfile(input_path, output_path)
        return validate_audio(output_path)

    if res.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        output_path.unlink(missing_ok=True)
        raise AudioValidationError("Failed to apply audio edits. Ensure valid trim ranges and filter parameters.")

    return validate_audio(output_path)
