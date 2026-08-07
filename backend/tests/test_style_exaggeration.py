"""
Tests for style_exaggeration parameter and neutral mode protection.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import soundfile as sf
import numpy as np
from pydantic import ValidationError

from app.api.schemas.tts import TTSGenerateRequest
from app.audio import apply_audio_effects


def test_style_exaggeration_default_is_zero() -> None:
    """Verify default style_exaggeration is 0."""
    req = TTSGenerateRequest(text="Hello world", profile_id=1, language="en")
    assert req.style_exaggeration == 0


def test_style_exaggeration_valid_range() -> None:
    """Verify style_exaggeration accepts values 0 to 100 inclusive."""
    req0 = TTSGenerateRequest(text="Test", profile_id=1, language="en", style_exaggeration=0)
    assert req0.style_exaggeration == 0

    req50 = TTSGenerateRequest(text="Test", profile_id=1, language="en", style_exaggeration=50)
    assert req50.style_exaggeration == 50

    req100 = TTSGenerateRequest(text="Test", profile_id=1, language="en", style_exaggeration=100)
    assert req100.style_exaggeration == 100


def test_style_exaggeration_out_of_bounds() -> None:
    """Verify ValidationError is raised for out of bounds values."""
    with pytest.raises(ValidationError):
        TTSGenerateRequest(text="Test", profile_id=1, language="en", style_exaggeration=-1)

    with pytest.raises(ValidationError):
        TTSGenerateRequest(text="Test", profile_id=1, language="en", style_exaggeration=101)


def test_neutral_mode_with_zero_exaggeration_is_noop() -> None:
    """Verify neutral emotion with style_exaggeration=0 is a zero-op (bit-for-bit identical)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        wav_path = Path(tmp_dir) / "test.wav"
        sr = 24000
        t = np.linspace(0, 1.0, sr, endpoint=False)
        samples = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        sf.write(str(wav_path), samples, sr)

        original_bytes = wav_path.read_bytes()

        apply_audio_effects(wav_path, speed=1.0, emotion="neutral", style_exaggeration=0)

        # Must remain 100% bit-for-bit identical
        assert wav_path.read_bytes() == original_bytes
