"""Request/response models for the Convert tab: chunk pasted text for review + conversion.

Formerly YouTube transcript import; the video fetch was removed once the input
became text the user pastes. The chunking and script detection are the parts
worth keeping — a pasted Hindi (Devanagari) or Roman Urdu script still needs to
be split into review-sized units and have its script detected so the client can
offer the right conversion.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "PrepareTextRequest",
    "TranscriptChunk",
    "PreparedTextResponse",
]

#: Hard ceiling on a single paste. A whole book is not the use case, and an
#: unbounded field is a memory footgun; over this, the request is refused rather
#: than silently truncated.
MAX_PREPARE_CHARS = 200_000


class PrepareTextRequest(BaseModel):
    """A script the user pasted, to be chunked for review and conversion."""

    text: str = Field(..., min_length=1, max_length=MAX_PREPARE_CHARS)


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
