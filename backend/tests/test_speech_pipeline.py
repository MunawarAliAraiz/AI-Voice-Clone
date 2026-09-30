"""Real PCM samples exercise ordering, overlap, timing and provenance controls."""

import wave
from array import array
from dataclasses import replace
from pathlib import Path

import pytest

from app.speech import (
    ModelPin,
    RecordedStem,
    ScriptLine,
    SpeakerVoice,
    assemble_script_wav,
    build_conversion_plan,
    build_script_plan,
    mix_converted_stems_wav,
)
from app.speech.plans import file_sha256

PIN = ModelPin("fixture", "test/model", "a" * 40, "MIT", "MIT")


def wav(path: Path, samples: list[int], rate=8, channels=1) -> Path:
    with wave.open(str(path), "wb") as audio:
        audio.setparams((channels, 2, rate, 0, "NONE", ""))
        audio.writeframes(array("h", samples).tobytes())
    return path


def read(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as audio:
        return list(array("h", audio.readframes(audio.getnframes())))


def voice(tmp_path: Path, speaker: str) -> SpeakerVoice:
    ref = wav(tmp_path / f"ref-{speaker}.wav", [100, 200])
    return SpeakerVoice(speaker, f"voice-{speaker}", ref, file_sha256(ref),
                        "consent-record", "b" * 64)


def stem(tmp_path: Path, identifier: str, speaker: str, samples: list[int], start: float):
    path = wav(tmp_path / f"stem-{identifier}.wav", samples)
    return RecordedStem(identifier, speaker, path, file_sha256(path), start)


def test_script_keeps_line_order_pauses_and_editable_segments(tmp_path):
    voices = [voice(tmp_path, name) for name in ("a", "b", "c")]
    plan = build_script_plan([
        ScriptLine("1", "a", "First", 0.25),
        ScriptLine("2", "b", "Second", 0),
        ScriptLine("3", "c", "Third", 0.125),
    ], voices, PIN)
    paths = {"3": wav(tmp_path / "three.wav", [30]),
             "1": wav(tmp_path / "one.wav", [10, 11]),
             "2": wav(tmp_path / "two.wav", [20, 21])}
    result = assemble_script_wav(plan, paths, tmp_path / "dialogue.wav")
    assert read(result.path) == [10, 11, 0, 0, 20, 21, 30, 0]
    assert [(s.start_frame, s.end_frame) for s in result.segments] == [(0, 2), (4, 6), (6, 7)]
    assert result.duration_sec == 1
    assert result.sha256 == file_sha256(result.path)
    assert result.segments[0].input_sha256 == file_sha256(paths["1"])


def test_script_rejects_missing_outputs_format_mismatch_and_overwrite(tmp_path):
    assigned = voice(tmp_path, "a")
    plan = build_script_plan([ScriptLine("1", "a", "Hi", 0)], [assigned], PIN)
    line = wav(tmp_path / "line.wav", [1])
    with pytest.raises(ValueError, match="exactly one"):
        assemble_script_wav(plan, {}, tmp_path / "out.wav")
    with pytest.raises(ValueError, match="overwrite"):
        assemble_script_wav(plan, {"1": line}, line)
    two_lines = build_script_plan([ScriptLine("1", "a", "Hi"), ScriptLine("2", "a", "Bye")],
                                 [assigned], PIN)
    different = wav(tmp_path / "different.wav", [1], rate=16)
    with pytest.raises(ValueError, match="one format"):
        assemble_script_wav(two_lines, {"1": line, "2": different}, tmp_path / "out.wav")


def test_enrollment_hash_authorization_mapping_and_model_pins_are_required(tmp_path):
    assigned = voice(tmp_path, "a")
    line = [ScriptLine("1", "a", "Hi")]
    with pytest.raises(ValueError, match="authorization"):
        build_script_plan(line, [replace(assigned, authorization_id="")], PIN)
    with pytest.raises(ValueError, match="every"):
        build_script_plan(line, [replace(assigned, speaker_id="b")], PIN)
    with pytest.raises(ValueError, match="40-character"):
        build_script_plan(line, [assigned], replace(PIN, revision="main"))
    wav(assigned.reference_path, [900])
    with pytest.raises(ValueError, match="changed"):
        build_script_plan(line, [assigned], PIN)


def test_mix_preserves_source_timeline_three_speaker_overlap_and_global_gain(tmp_path):
    source = wav(tmp_path / "source.wav", [0] * 8)
    stems = [stem(tmp_path, name, name, [20000, 20000], 0.25) for name in ("a", "b", "c")]
    plan = build_conversion_plan(source, stems, [voice(tmp_path, n) for n in ("a", "b", "c")],
                                 [PIN], mapping_review_id="review-1")
    outputs = {s.id: wav(tmp_path / f"converted-{s.id}.wav", [20000, 20000]) for s in stems}
    result = mix_converted_stems_wav(plan, outputs, tmp_path / "converted.wav")
    assert read(result.path) == [0, 0, 32767, 32767, 0, 0, 0, 0]
    assert result.applied_gain == pytest.approx(32767 / 60000)
    assert result.frames == 8
    assert result.duration_sec == 1


def test_mix_across_chunk_boundary_and_preserves_nonoverlap_level(tmp_path):
    source = wav(tmp_path / "source.wav", [0] * 9000)
    a = stem(tmp_path, "a1", "a", [1000] * 9000, 0)
    b = stem(tmp_path, "b1", "b", [2000] * 3, 8191 / 8)
    plan = build_conversion_plan(source, [a, b], [voice(tmp_path, n) for n in ("a", "b")],
                                 [PIN], mapping_review_id="review")
    result = mix_converted_stems_wav(plan, {a.id: a.audio_path, b.id: b.audio_path},
                                     tmp_path / "mixed.wav")
    samples = read(result.path)
    assert samples[8190:8195] == [1000, 3000, 3000, 3000, 1000]
    assert result.applied_gain == 1
    assert len(samples) == 9000


def test_conversion_rejects_unreviewed_mapping_long_source_and_overlap(tmp_path):
    assigned = voice(tmp_path, "a")
    source = wav(tmp_path / "source.wav", [0] * 8)
    a = stem(tmp_path, "a", "a", [1] * 4, 0)
    with pytest.raises(ValueError, match="Review"):
        build_conversion_plan(source, [a], [assigned], [PIN], mapping_review_id="")
    too_long = wav(tmp_path / "long.wav", [0] * (3600 * 8 + 1))
    with pytest.raises(ValueError, match="one hour"):
        build_conversion_plan(too_long, [a], [assigned], [PIN], mapping_review_id="review")
    b = stem(tmp_path, "b", "a", [1] * 4, 0.25)
    with pytest.raises(ValueError, match="one speaker"):
        build_conversion_plan(source, [a, b], [assigned], [PIN], mapping_review_id="review")
    four = [stem(tmp_path, name, name, [1] * 4, 0) for name in ("a", "b", "c", "d")]
    with pytest.raises(ValueError, match="three speakers"):
        build_conversion_plan(source, four, [assigned], [PIN], mapping_review_id="review")


def test_conversion_rejects_changed_source_and_duration_drift(tmp_path):
    source = wav(tmp_path / "source.wav", [0] * 8)
    a = stem(tmp_path, "a", "a", [100] * 4, 0)
    plan = build_conversion_plan(source, [a], [voice(tmp_path, "a")], [PIN],
                                 mapping_review_id="review")
    drifted = wav(tmp_path / "drifted.wav", [100] * 5)
    with pytest.raises(ValueError, match="exact frame count"):
        mix_converted_stems_wav(plan, {"a": drifted}, tmp_path / "out.wav")
    wav(source, [1] * 8)
    with pytest.raises(ValueError, match="Source recording changed"):
        mix_converted_stems_wav(plan, {"a": a.audio_path}, tmp_path / "out.wav")


def test_conversion_stems_cannot_exceed_source_or_change_after_separation(tmp_path):
    source = wav(tmp_path / "source.wav", [0] * 8)
    assigned = voice(tmp_path, "a")
    a = stem(tmp_path, "a", "a", [1] * 4, 0.75)
    with pytest.raises(ValueError, match="beyond"):
        build_conversion_plan(source, [a], [assigned], [PIN], mapping_review_id="review")
    changed = replace(a, start_sec=0)
    wav(changed.audio_path, [2] * 4)
    with pytest.raises(ValueError, match="stem hash"):
        build_conversion_plan(source, [changed], [assigned], [PIN], mapping_review_id="review")


def test_stereo_conversion_preserves_channel_positions(tmp_path):
    source = wav(tmp_path / "source.wav", [0] * 8, channels=2)
    path = wav(tmp_path / "stereo.wav", [100, -100, 200, -200], channels=2)
    isolated = RecordedStem("a", "a", path, file_sha256(path), 0.125)
    plan = build_conversion_plan(source, [isolated], [voice(tmp_path, "a")], [PIN],
                                 mapping_review_id="review")
    result = mix_converted_stems_wav(plan, {"a": path}, tmp_path / "out.wav")
    assert result.channels == 2
    assert read(result.path) == [0, 0, 100, -100, 200, -200, 0, 0]


def test_truncated_line_does_not_replace_existing_output(tmp_path):
    assigned = voice(tmp_path, "a")
    plan = build_script_plan([ScriptLine("1", "a", "Hi", 0)], [assigned], PIN)
    path = wav(tmp_path / "line.wav", [1] * 8)
    path.write_bytes(path.read_bytes()[:-4])
    output = wav(tmp_path / "existing.wav", [9])
    before = output.read_bytes()
    with pytest.raises(ValueError, match="truncated"):
        assemble_script_wav(plan, {"1": path}, output)
    assert output.read_bytes() == before
    assert not list(tmp_path.glob(".speech-*"))
