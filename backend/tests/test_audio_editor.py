"""
Tests for built-in Audio Editor processing and non-destructive file handling.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import soundfile as sf
import numpy as np

from app.audio_editor import EditOptions, process_audio_edits


def test_original_file_remains_unchanged() -> None:
    """Verify that process_audio_edits NEVER modifies or deletes the original input file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        input_wav = Path(tmp_dir) / "original.wav"
        output_wav = Path(tmp_dir) / "edited.wav"

        sr = 24000
        t = np.linspace(0, 3.0, sr * 3, endpoint=False)
        samples = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        sf.write(str(input_wav), samples, sr)

        original_bytes = input_wav.read_bytes()

        # Apply edit options (trim start 0.5s, trim end 2.0s, pitch +2, speed 1.25x, gain +3dB, LUFS normalization)
        options = EditOptions(
            trim_start=0.5,
            trim_end=2.0,
            speed=1.25,
            pitch_semitones=2.0,
            gain_db=3.0,
            fade_in_sec=0.2,
            fade_out_sec=0.2,
            normalize_lufs=True,
            remove_silence=True,
        )

        meta = process_audio_edits(input_wav, output_wav, options)

        # 1. Original file MUST be completely unchanged bit-for-bit
        assert input_wav.exists()
        assert input_wav.read_bytes() == original_bytes

        # 2. Output file MUST exist and be valid audio
        assert output_wav.exists()
        assert meta.duration_sec > 0
        assert meta.sample_rate == 24000


def test_edit_options_default() -> None:
    """Verify default EditOptions attributes."""
    opts = EditOptions()
    assert opts.trim_start == 0.0
    assert opts.trim_end is None
    assert opts.speed == 1.0
    assert opts.pitch_semitones == 0.0
    assert opts.gain_db == 0.0
    assert opts.normalize_lufs is False
    assert opts.remove_silence is False
