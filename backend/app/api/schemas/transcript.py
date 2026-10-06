"""Request/response models for the Convert tab: chunk pasted text for review + conversion.

Accepts pasted scripts or public caption tracks, with explicit source language
and review-sized chunks. No language is inferred from Latin characters.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr

__all__ = [
    "PrepareTextRequest",
    "TranscriptChunk",
    "PreparedTextResponse",
    "YoutubeTranscriptRequest",
]

#: Hard ceiling on a single paste. A whole book is not the use case, and an
#: unbounded field is a memory footgun; over this, the request is refused rather
#: than silently truncated.
MAX_PREPARE_CHARS = 200_000


class PrepareTextRequest(BaseModel):
    """A script the user pasted, to be chunked for review and conversion."""

    text: str = Field(..., min_length=1, max_length=MAX_PREPARE_CHARS)
    source_language: Literal["en", "hi", "ur"] | None = None


class YoutubeTranscriptRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    source_language: Literal["en", "hi", "ur"] | None = None
    refresh: bool = False


class ApifyConnectionInput(BaseModel):
    # Validate manually so a validation response never echoes a rejected key.
    api_key: SecretStr


class CaptionTrackResponse(BaseModel):
    language: str
    language_code: str
    is_generated: bool


class TranscriptChunk(BaseModel):
    """One unit of the pasted text, sized for a single generation."""

    index: int
    text: str
    #: False means the chunk was cut at a clause or word boundary because a
    #: sentence would not fit. That is where a join artifact will be audible,
    #: so the UI badges it rather than hiding it.
    ends_on_sentence: bool


class PreparedTextResponse(BaseModel):
    """The pasted text, split and script-tagged for the conversion UI."""

    #: The text as chunked (paragraph breaks preserved), so what the UI shows
    #: and what it converts cannot drift from each other.
    text: str
    #: Detected script (`latin`, `arabic`, `devanagari`, …), from the same
    #: `profile_text()` routing uses. `devanagari` is the signal that this text
    #: is NOT routable and must be converted first.
    script: str
    #: True when nothing in the catalog can render this script. Computed
    #: server-side so the UI never has to encode routing rules.
    needs_transliteration: bool
    chunks: list[TranscriptChunk]
    source_language: Literal["en", "hi", "ur"] | None = None
    video_id: str | None = None
    caption_language_code: str | None = None
    captions_generated: bool | None = None
    available_caption_tracks: list[CaptionTrackResponse] = Field(default_factory=list)
    caption_provider: Literal["local", "apify"] | None = None


class CaptionImportStatus(BaseModel):
    id: str
    phase: Literal["starting", "fetching", "checking", "cancelling", "ready", "failed", "cancelled"]
    detail: str
    error_code: str | None = None
    created_at: float
    provider: Literal["local", "apify"]
    cached: bool = False
    maximum_import_usd: float = 0
    result: PreparedTextResponse | None = None
