"""
Tests for emotional speech parameters and audio effect processing.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import soundfile as sf
import numpy as np

from app.api.schemas.tts import TTSGenerateRequest
from app.audio import apply_audio_effects, EMOTION_FILTERS


def test_schema_emotion_default() -> None:
    req = TTSGenerateRequest(text="Hello", profile_id=1, language="en")
    assert req.emotion == "neutral"
    assert req.speed == 1.0


def test_schema_emotion_custom() -> None:
    req = TTSGenerateRequest(text="Hello", profile_id=1, language="en", emotion="happy", speed=1.1)
    assert req.emotion == "happy"
    assert req.speed == 1.1


def test_neutral_emotion_regression_noop() -> None:
    """Ensure neutral emotion with speed=1.0 performs ZERO modification to audio file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        wav_path = Path(tmp_dir) / "test.wav"
        sr = 24000
        # Generate 1 second sine wave
        t = np.linspace(0, 1.0, sr, endpoint=False)
        samples = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        sf.write(str(wav_path), samples, sr)

        original_bytes = wav_path.read_bytes()
        
        # Apply neutral emotion with default speed
        apply_audio_effects(wav_path, speed=1.0, emotion="neutral")
        
        # Must be bit-for-bit identical
        assert wav_path.read_bytes() == original_bytes


def test_all_emotions_valid() -> None:
    """Verify that all declared emotions are present in EMOTION_FILTERS mapping."""
    expected_emotions = {"neutral", "happy", "sad", "angry", "excited", "calm", "whisper", "narration"}
    assert set(EMOTION_FILTERS.keys()) == expected_emotions
