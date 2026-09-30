"""Validated immutable plans with reference hashes and authorization evidence."""

from __future__ import annotations

import hashlib
import math
import re
import wave
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def wav_info(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as audio:
        if audio.getsampwidth() != 2 or audio.getnchannels() not in (1, 2):
            raise ValueError("Assembly requires mono or stereo PCM16 WAV audio")
        if audio.getcomptype() != "NONE" or audio.getframerate() <= 0:
            raise ValueError("Assembly requires uncompressed PCM WAV audio")
        if audio.getnframes() <= 0:
            raise ValueError("Audio must contain at least one frame")
        return audio.getframerate(), audio.getnchannels(), audio.getnframes()


@dataclass(frozen=True)
class ModelPin:
    """A registry-provided pin; validation does not qualify a model for release."""

    id: str
    repo: str
    revision: str
    code_license: str
    weights_license: str

    def validate(self) -> None:
        if not self.id.strip() or not re.fullmatch(r"[^/\s]+/[^/\s]+", self.repo):
            raise ValueError("Model id and repository are required")
        if not re.fullmatch(r"[0-9a-f]{40}", self.revision):
            raise ValueError("Model revision must be a pinned 40-character commit")
        if not self.code_license.strip() or not self.weights_license.strip():
            raise ValueError("Both model code and weights licenses are required")


@dataclass(frozen=True)
class SpeakerVoice:
    speaker_id: str
    voice_id: str
    reference_path: Path
    reference_sha256: str
    authorization_id: str
    authorization_sha256: str
    reference_text: str = ""

    def validate(self) -> None:
        if not self.speaker_id.strip() or not self.voice_id.strip():
            raise ValueError("Speaker and voice identifiers are required")
        if not self.authorization_id.strip():
            raise ValueError("Voice authorization evidence is required")
        for digest in (self.reference_sha256, self.authorization_sha256):
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("Reference and authorization hashes must be SHA256")
        if file_sha256(self.reference_path) != self.reference_sha256:
            raise ValueError("Voice reference changed after enrollment")


@dataclass(frozen=True)
class ScriptLine:
    id: str
    speaker_id: str
    text: str
    pause_after_sec: float = 0.25


@dataclass(frozen=True)
class ScriptPlan:
    lines: tuple[ScriptLine, ...]
    voices: tuple[SpeakerVoice, ...]
    model: ModelPin


def _voices(voices: Iterable[SpeakerVoice], speakers: set[str]) -> tuple[SpeakerVoice, ...]:
    records = tuple(voices)
    if not 1 <= len(records) <= 3:
        raise ValueError("Assign between one and three speakers")
    identifiers = [voice.speaker_id for voice in records]
    if len(set(identifiers)) != len(identifiers) or set(identifiers) != speakers:
        raise ValueError("Assign exactly one voice to every detected or scripted speaker")
    for voice in records:
        voice.validate()
    return records


def build_script_plan(
    lines: Iterable[ScriptLine], voices: Iterable[SpeakerVoice], model: ModelPin,
) -> ScriptPlan:
    records = tuple(lines)
    if not records:
        raise ValueError("Script must contain at least one line")
    if len({line.id for line in records}) != len(records):
        raise ValueError("Script line identifiers must be unique")
    for line in records:
        if not line.id.strip() or not line.text.strip() or not line.speaker_id.strip():
            raise ValueError("Every script line needs an id, speaker and text")
        if not math.isfinite(line.pause_after_sec) or not 0 <= line.pause_after_sec <= 60:
            raise ValueError("Line pauses must be between zero and sixty seconds")
    model.validate()
    return ScriptPlan(records, _voices(voices, {line.speaker_id for line in records}), model)


@dataclass(frozen=True)
class RecordedStem:
    """A real isolated speaker segment, already reviewed; no audio is invented."""

    id: str
    speaker_id: str
    audio_path: Path
    audio_sha256: str
    start_sec: float


@dataclass(frozen=True)
class ConversionPlan:
    source_path: Path
    source_sha256: str
    sample_rate: int
    channels: int
    frames: int
    stems: tuple[RecordedStem, ...]
    voices: tuple[SpeakerVoice, ...]
    models: tuple[ModelPin, ...]
    mapping_review_id: str


def build_conversion_plan(
    source_path: Path, stems: Iterable[RecordedStem], voices: Iterable[SpeakerVoice],
    models: Iterable[ModelPin], *, mapping_review_id: str,
) -> ConversionPlan:
    if not mapping_review_id.strip():
        raise ValueError("Review the detected speaker-to-voice mapping before conversion")
    rate, channels, frames = wav_info(source_path)
    if frames > 3600 * rate:
        raise ValueError("Recordings may be at most one hour")
    records = tuple(stems)
    if not records or len({stem.id for stem in records}) != len(records):
        raise ValueError("Provide real speaker stems with unique identifiers")
    events: list[tuple[int, int]] = []
    speaker_intervals: dict[str, list[tuple[int, int]]] = {}
    for stem in records:
        if not stem.id.strip() or not stem.speaker_id.strip():
            raise ValueError("Each stem needs an id and speaker")
        if not math.isfinite(stem.start_sec) or stem.start_sec < 0:
            raise ValueError("Stem start time must be finite and nonnegative")
        stem_rate, stem_channels, stem_frames = wav_info(stem.audio_path)
        if (stem_rate, stem_channels) != (rate, channels):
            raise ValueError("Resample stems to the source format before planning")
        if file_sha256(stem.audio_path) != stem.audio_sha256:
            raise ValueError("Separated speaker stem hash does not match")
        start = round(stem.start_sec * rate)
        end = start + stem_frames
        if end > frames:
            raise ValueError("Speaker stem extends beyond the source recording")
        intervals = speaker_intervals.setdefault(stem.speaker_id, [])
        if any(start < old_end and old_start < end for old_start, old_end in intervals):
            raise ValueError("Segments belonging to one speaker must not overlap")
        intervals.append((start, end))
        events.extend(((start, 1), (end, -1)))
    active = 0
    for _, delta in sorted(events):  # End before start at a shared frame.
        active += delta
        if active > 3:
            raise ValueError("At most three speakers may overlap")
    pins = tuple(models)
    if not pins or len({pin.id for pin in pins}) != len(pins):
        raise ValueError("Provide unique pinned pipeline models")
    for pin in pins:
        pin.validate()
    assignments = _voices(voices, {stem.speaker_id for stem in records})
    return ConversionPlan(
        source_path, file_sha256(source_path), rate, channels, frames,
        records, assignments, pins, mapping_review_id,
    )
