"""Verify actual PCM ordering, pauses and format normalization without a GPU."""

import wave
from array import array
from pathlib import Path

from app.speech.dialogue_assembly import assemble_dialogue


def clip(path: Path, value: int, rate: int = 44100) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(array("h", [value] * rate).tobytes())


def test_dialogue_clip_order_and_pause(tmp_path: Path) -> None:
    first, second = tmp_path / "first.wav", tmp_path / "second.wav"
    clip(first, 1000)
    clip(second, -2000)
    result = assemble_dialogue([(4, first, 0.5), (9, second, 0)], tmp_path / "out.wav")
    assert result["duration_sec"] == 2.5
    assert [(s["job_id"], s["start_sec"], s["end_sec"]) for s in result["spans"]] == [
        (4, 0, 1),
        (9, 1.5, 2.5),
    ]
    with wave.open(str(tmp_path / "out.wav"), "rb") as audio:
        samples = array("h")
        samples.frombytes(audio.readframes(audio.getnframes()))
    assert samples[0] == 1000
    assert samples[44100] == 0
    assert samples[66150] == -2000


def test_dialogue_resamples_real_different_rate_clip(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    clip(source, 1000, rate=16000)
    result = assemble_dialogue([(1, source, 0)], tmp_path / "out.wav")
    assert result["duration_sec"] == 1.0
    with wave.open(str(tmp_path / "out.wav"), "rb") as audio:
        assert audio.getframerate() == 44100
        assert audio.getnframes() == 44100
