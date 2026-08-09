"""
`apply_audio_effects` — the post-synth ffmpeg tempo step every generation
(single-shot and directed) runs through.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app import audio as audio_module
from app.audio import apply_audio_effects


def _wav(path: Path, dur_sec: float = 0.3, sr: int = 16000) -> Path:
    n = int(dur_sec * sr)
    sf.write(str(path), np.full((n,), 0.1, dtype=np.float32), sr, subtype="PCM_16")
    return path


def test_speed_one_is_a_true_noop(tmp_path: Path) -> None:
    """No subprocess is even attempted at 1.0 — the common case must not pay
    an ffmpeg round trip for nothing."""
    path = _wav(tmp_path / "a.wav")
    before = path.read_bytes()

    def _boom(*a, **kw):
        raise AssertionError("subprocess.run should not be called at speed=1.0")

    original = subprocess.run
    audio_module.subprocess.run = _boom
    try:
        apply_audio_effects(path, speed=1.0)
    finally:
        audio_module.subprocess.run = original
    assert path.read_bytes() == before


def test_out_of_range_speed_is_a_noop(tmp_path: Path) -> None:
    path = _wav(tmp_path / "a.wav")
    before = path.read_bytes()
    apply_audio_effects(path, speed=5.0)  # outside the declared 0.5-2.0 range
    assert path.read_bytes() == before


def test_missing_file_is_a_noop(tmp_path: Path) -> None:
    apply_audio_effects(tmp_path / "does-not-exist.wav", speed=1.5)  # must not raise


def test_missing_ffmpeg_degrades_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact gap this test guards: on a machine with no ffmpeg on PATH,
    a non-1.0 speed must leave the original (untimed) audio in place — real
    model output, just not re-timed — never crash the whole synthesis job.
    Same choice already made for the format-conversion path in routers/media.py."""
    path = _wav(tmp_path / "a.wav")
    before = path.read_bytes()

    def _raise_not_found(*a, **kw):
        raise FileNotFoundError("ffmpeg not on PATH")

    monkeypatch.setattr(audio_module.subprocess, "run", _raise_not_found)

    apply_audio_effects(path, speed=1.5)  # must not raise

    assert path.exists()
    assert path.read_bytes() == before
    # No leftover temp file from the aborted attempt.
    assert not path.with_suffix(".fx_speed1.5.wav").exists()


def test_ffmpeg_failure_leaves_original_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-zero ffmpeg exit (bad filter, corrupt input) must also fall back
    to the original file, not leave a half-written temp file in its place."""
    path = _wav(tmp_path / "a.wav")
    before = path.read_bytes()

    class _FailedRun:
        returncode = 1

    monkeypatch.setattr(audio_module.subprocess, "run", lambda *a, **kw: _FailedRun())

    apply_audio_effects(path, speed=1.5)

    assert path.read_bytes() == before
