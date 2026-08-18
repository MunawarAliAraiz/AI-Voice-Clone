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


class TranscriptChapterInfo(BaseModel):
    """
    One chapter of the video.

    Carries the only timestamps in this response, and deliberately so: a chunk
    does NOT get a `start_sec`. The cheap version — stamping a chapter's start
    on all twelve of its parts — is a number that looks precise and is wrong,
    and the honest version is not cheap (`normalize_whitespace` destroys the
    newlines `cues_to_text` emitted, so a chunk is not a substring of its group
    text and its offset cannot be recovered by searching). A timestamp appears
    here, where it is true, and nowhere else.
    """

    index: int
    title: str
    start_sec: float
    #: yt-dlp omits this on description-derived chapters. Display only — chapter
    #: boundaries are decided by the NEXT chapter's start, never by this.
    end_sec: float | None = None


class TranscriptChunk(BaseModel):
    """One unit of text sized for a single generation."""

    #: GLOBAL across the transcript, not per chapter.
    #:
    #: `chunk_for_synthesis` numbers from 0 on every call, so chapter-aware
    #: chunking would otherwise produce several parts all called `0`. The UI
    #: keys conversion results off this, and duplicates would land part 8's
    #: text on part 1 — plausible Urdu in the wrong place, which is invisible.
    index: int
    text: str
    #: False means the chunk was cut at a clause or word boundary because a
    #: sentence would not fit. That is where a join artifact will be audible,
    #: so the UI badges it rather than hiding it.
    ends_on_sentence: bool
    #: Which chapter this part came from. `None` when the video has no chapters
    #: at all, or for the stretch before the first one begins.
    chapter_index: int | None = None


class TranscriptResponse(BaseModel):
    video_id: str
    title: str | None = None
    duration_sec: float | None = None
    #: Tracks worth re-requesting — NOT every track found.
    #:
    #: A real video measured **4867** of them, 4837 being YouTube's machine
    #: auto-translations into every language it supports, at **367 KB of
    #: JSON** for a list no UI can meaningfully present. What is useful is the
    #: authored tracks (someone wrote them) plus whichever one was chosen, so
    #: that is what this carries. See `_selectable_tracks`.
    available_tracks: list[TranscriptTrack]
    #: How many tracks existed before that trim, so the number is not simply
    #: lost. A caller can still request any language by code — the list is a
    #: convenience, not the set of what is permitted.
    total_tracks: int
    chosen_track: TranscriptTrack
    text: str
    #: Detected script of the transcript (`latin`, `arabic`, `devanagari`, …),
    #: from the same `profile_text()` routing uses. `devanagari` is the signal
    #: that this text is NOT routable and needs transliterating first.
    script: str
    #: True when nothing in the catalog can render this script. Computed
    #: server-side so the UI never has to encode routing rules.
    needs_transliteration: bool
    #: Empty when the video has no chapters, which is the common case and must
    #: stay indistinguishable from the pre-chapters behaviour.
    chapters: list[TranscriptChapterInfo] = []
    #: True when the transcript hit `transcript_max_chars` and was cut.
    #:
    #: Until now this was only a `logger.warning` and the user was never told —
    #: a silently shortened transcript is golden rule 5's family. Deliberately
    #: no chapter/part count here: both are derivable from the lists below, and
    #: a duplicated count is a count that can disagree with what it describes.
    truncated: bool = False
    chunks: list[TranscriptChunk]
