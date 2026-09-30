"""Bounded-memory audio assembly from real generated/converted PCM16 files."""

from __future__ import annotations

import os
import sys
import tempfile
import wave
from array import array
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from .plans import (
    ConversionPlan,
    ScriptPlan,
    build_conversion_plan,
    build_script_plan,
    file_sha256,
    wav_info,
)

CHUNK_FRAMES = 8192


@dataclass(frozen=True)
class AudioSegment:
    id: str
    speaker_id: str
    start_frame: int
    end_frame: int
    input_sha256: str


@dataclass(frozen=True)
class AudioResult:
    path: Path
    sha256: str
    sample_rate: int
    channels: int
    frames: int
    applied_gain: float
    segments: tuple[AudioSegment, ...]

    @property
    def duration_sec(self) -> float:
        return self.frames / self.sample_rate


def _samples(data: bytes) -> array:
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _bytes(samples: array) -> bytes:
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def _inputs(paths: Mapping[str, Path], ids: set[str], output_path: Path) -> None:
    if set(paths) != ids:
        raise ValueError("Supply exactly one completed audio file for every planned item")
    if any(path.resolve() == output_path.resolve() for path in paths.values()):
        raise ValueError("Output must not overwrite an input file")


def _temporary(output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".speech-", suffix=".wav", dir=output_path.parent)
    os.close(fd)
    return Path(name)


def assemble_script_wav(
    plan: ScriptPlan, generated_lines: Mapping[str, Path], output_path: Path,
) -> AudioResult:
    """Join actual line outputs and explicit pauses; each line remains editable."""
    build_script_plan(plan.lines, plan.voices, plan.model)
    _inputs(generated_lines, {line.id for line in plan.lines}, output_path)
    if any(voice.reference_path.resolve() == output_path.resolve() for voice in plan.voices):
        raise ValueError("Output must not overwrite a voice reference")
    rate, channels, _ = wav_info(generated_lines[plan.lines[0].id])
    segments = []
    for line in plan.lines:
        info = wav_info(generated_lines[line.id])
        if info[:2] != (rate, channels):
            raise ValueError("Resample all generated lines to one format before assembly")
    temporary = _temporary(output_path)
    position = 0
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
            for line in plan.lines:
                path = generated_lines[line.id]
                with wave.open(str(path), "rb") as audio:
                    start = position
                    expected_frames = audio.getnframes()
                    for block in iter(lambda: audio.readframes(CHUNK_FRAMES), b""):
                        if len(block) % (2 * channels):
                            raise ValueError("Generated line contains an incomplete PCM frame")
                        output.writeframesraw(block)
                        position += len(block) // (2 * channels)
                    if position - start != expected_frames:
                        raise ValueError("Generated line was truncated during assembly")
                segments.append(AudioSegment(
                    line.id, line.speaker_id, start, position, file_sha256(path),
                ))
                silence_frames = round(line.pause_after_sec * rate)
                while silence_frames:
                    count = min(silence_frames, CHUNK_FRAMES)
                    output.writeframesraw(bytes(count * 2 * channels))
                    silence_frames -= count
                    position += count
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return AudioResult(
        output_path, file_sha256(output_path), rate, channels, position, 1.0, tuple(segments),
    )


def mix_converted_stems_wav(
    plan: ConversionPlan, converted_stems: Mapping[str, Path], output_path: Path,
) -> AudioResult:
    """Place real converted stems on the source timeline with a global anti-clipping gain.

    Two streaming passes measure the summed peak then write PCM16. The function
    cannot perform diarization, separation or conversion; those outputs are inputs.
    """
    if file_sha256(plan.source_path) != plan.source_sha256:
        raise ValueError("Source recording changed after mapping review")
    refreshed = build_conversion_plan(
        plan.source_path, plan.stems, plan.voices, plan.models,
        mapping_review_id=plan.mapping_review_id,
    )
    if (plan.sample_rate, plan.channels, plan.frames) != (
        refreshed.sample_rate, refreshed.channels, refreshed.frames,
    ):
        raise ValueError("Source timeline differs from the reviewed plan")
    _inputs(converted_stems, {stem.id for stem in plan.stems}, output_path)
    protected_paths = [plan.source_path, *(stem.audio_path for stem in plan.stems),
                       *(voice.reference_path for voice in plan.voices)]
    if any(path.resolve() == output_path.resolve() for path in protected_paths):
        raise ValueError("Output must not overwrite source or reference audio")
    segments = []
    for stem in plan.stems:
        path = converted_stems[stem.id]
        if wav_info(path) != wav_info(stem.audio_path):
            raise ValueError("Converted stems must preserve format and exact frame count")
        start = round(stem.start_sec * plan.sample_rate)
        segments.append(AudioSegment(
            stem.id, stem.speaker_id, start, start + wav_info(path)[2], file_sha256(path),
        ))

    with ExitStack() as stack:
        readers = [stack.enter_context(wave.open(str(converted_stems[s.id]), "rb"))
                   for s in segments]

        def blocks():
            for offset in range(0, plan.frames, CHUNK_FRAMES):
                count = min(CHUNK_FRAMES, plan.frames - offset)
                sums = [0] * (count * plan.channels)
                for segment, reader in zip(segments, readers, strict=True):
                    start = max(offset, segment.start_frame)
                    end = min(offset + count, segment.end_frame)
                    if end <= start:
                        continue
                    reader.setpos(start - segment.start_frame)
                    samples = _samples(reader.readframes(end - start))
                    if len(samples) != (end - start) * plan.channels:
                        raise ValueError("Converted stem was truncated during assembly")
                    first = (start - offset) * plan.channels
                    for index, value in enumerate(samples):
                        sums[first + index] += value
                yield sums

        peak = max((max(map(abs, block), default=0) for block in blocks()), default=0)
        gain = min(1.0, 32767 / peak) if peak else 1.0
        temporary = _temporary(output_path)
        try:
            with wave.open(str(temporary), "wb") as output:
                output.setparams((plan.channels, 2, plan.sample_rate, 0, "NONE", ""))
                for block in blocks():
                    output.writeframesraw(_bytes(array("h", (round(v * gain) for v in block))))
            os.replace(temporary, output_path)
        finally:
            temporary.unlink(missing_ok=True)
    return AudioResult(
        output_path, file_sha256(output_path), plan.sample_rate, plan.channels,
        plan.frames, gain, tuple(segments),
    )
