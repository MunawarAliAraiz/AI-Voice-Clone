"""
`concat_wavs_with_pauses` — the primitive that makes Speech Direction audible:
join per-segment WAVs with real inter-segment silence, nothing fabricated.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from app.audio import concat_wavs_with_pauses
from app.exceptions import AudioValidationError


def _wav(path: Path, dur_sec: float, sr: int = 16000, value: float = 0.2) -> Path:
    n = int(dur_sec * sr)
    data = np.full((n,), value, dtype=np.float32)
    sf.write(str(path), data, sr, subtype="PCM_16")
    return path


def test_joins_two_segments_with_pause(tmp_path: Path) -> None:
    a = _wav(tmp_path / "a.wav", 1.0)
    b = _wav(tmp_path / "b.wav", 0.5)
    out = tmp_path / "out.wav"

    duration = concat_wavs_with_pauses([a, b], [200, 0], out)

    data, sr = sf.read(str(out))
    assert sr == 16000
    # 1.0s + 0.2s pause + 0.5s, within a couple ms of rounding.
    assert abs(duration - 1.7) < 0.01
    assert abs(len(data) / sr - 1.7) < 0.01


def test_no_trailing_silence_after_last_segment(tmp_path: Path) -> None:
    """The pause AFTER the last segment must be dropped — no dead air at the
    end of a clip just because the analyzer set a pause_after_ms on it."""
    a = _wav(tmp_path / "a.wav", 1.0)
    out = tmp_path / "out.wav"

    duration = concat_wavs_with_pauses([a], [500], out)

    assert abs(duration - 1.0) < 0.01


def test_zero_pause_is_a_hard_join(tmp_path: Path) -> None:
    a = _wav(tmp_path / "a.wav", 0.3)
    b = _wav(tmp_path / "b.wav", 0.3)
    out = tmp_path / "out.wav"

    duration = concat_wavs_with_pauses([a, b], [0, 0], out)

    assert abs(duration - 0.6) < 0.01


def test_single_segment_passthrough(tmp_path: Path) -> None:
    a = _wav(tmp_path / "a.wav", 0.75)
    out = tmp_path / "out.wav"

    duration = concat_wavs_with_pauses([a], [0], out)

    assert abs(duration - 0.75) < 0.01


def test_the_silence_is_actually_silent(tmp_path: Path) -> None:
    """The inserted pause is real zero-amplitude silence, not a copy of either
    neighboring segment — a directed clip's gaps must be audibly quiet."""
    a = _wav(tmp_path / "a.wav", 0.2, value=0.5)
    b = _wav(tmp_path / "b.wav", 0.2, value=0.5)
    out = tmp_path / "out.wav"

    concat_wavs_with_pauses([a, b], [100, 0], out)

    data, sr = sf.read(str(out))
    gap_start = int(0.2 * sr)
    gap_end = gap_start + int(0.1 * sr)
    gap = data[gap_start:gap_end]
    assert np.max(np.abs(gap)) < 1e-4


def test_mismatched_sample_rates_raise(tmp_path: Path) -> None:
    a = _wav(tmp_path / "a.wav", 0.2, sr=16000)
    b = _wav(tmp_path / "b.wav", 0.2, sr=22050)
    out = tmp_path / "out.wav"

    try:
        concat_wavs_with_pauses([a, b], [0, 0], out)
        raise AssertionError("expected AudioValidationError")
    except AudioValidationError:
        pass


def test_empty_parts_list_raises(tmp_path: Path) -> None:
    try:
        concat_wavs_with_pauses([], [], tmp_path / "out.wav")
        raise AssertionError("expected AudioValidationError")
    except AudioValidationError:
        pass


def test_missing_pauses_entry_defaults_to_zero(tmp_path: Path) -> None:
    """pauses shorter than parts (e.g. a caller bug) must not IndexError —
    treat a missing entry as no pause rather than crashing mid-join."""
    a = _wav(tmp_path / "a.wav", 0.2)
    b = _wav(tmp_path / "b.wav", 0.2)
    out = tmp_path / "out.wav"

    duration = concat_wavs_with_pauses([a, b], [], out)

    assert abs(duration - 0.4) < 0.01
