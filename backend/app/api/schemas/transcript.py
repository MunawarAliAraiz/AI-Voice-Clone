"""Request/response models for YouTube transcript import."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "TranscriptRequest",
    "TranscriptTrack",
    "TranscriptChunk",
    "TranscriptResponse",
]


class TranscriptRequest(BaseModel):
    """
    `url` is validated by `domain/youtube.parse_video_id`, NOT here.

    Deliberate: the check that matters is the SSRF guard, and it belongs in one
    pure, tested function rather than split between a pydantic validator and a
    router. A regex here would be a second, weaker copy of it.
    """

    url: str = Field(..., max_length=2000)
    #: Preferred caption language (`en`, `ur`, `hi`, …). The chosen track is
    #: reported back, because what you asked for and what exists often differ.
    language: str | None = Field(None, max_length=16)


class TranscriptTrack(BaseModel):
    """One caption track the video offers."""

    language: str
    name: str | None = None
    #: Auto-generated captions are markedly worse for Urdu and Hindi. Surfaced
    #: so the UI can say so rather than presenting a rough machine transcript
    #: as if it were authored.
    is_auto_generated: bool


class TranscriptChunk(BaseModel):
    """One unit of text sized for a single generation."""

    index: int
    text: str
    #: False means the chunk was cut at a clause or word boundary because a
    #: sentence would not fit. That is where a join artifact will be audible,
    #: so the UI badges it rather than hiding it.
    ends_on_sentence: bool


class TranscriptResponse(BaseModel):
    video_id: str
    title: str | None = None
    duration_sec: float | None = None
    #: Every track found, so the caller can re-request a different one.
    available_tracks: list[TranscriptTrack]
    chosen_track: TranscriptTrack
    text: str
    #: Detected script of the transcript (`latin`, `arabic`, `devanagari`, …),
    #: from the same `profile_text()` routing uses. `devanagari` is the signal
    #: that this text is NOT routable and needs transliterating first.
    script: str
    #: True when nothing in the catalog can render this script. Computed
    #: server-side so the UI never has to encode routing rules.
    needs_transliteration: bool
    chunks: list[TranscriptChunk]
