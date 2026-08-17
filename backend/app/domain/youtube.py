"""
YouTube URL and caption-track parsing.

CONTRACT: PURE. No network, no I/O, no clock. Everything here is a string
transformation, which is what makes the two things most likely to break —
the SSRF guard and YouTube's caption format — testable without touching the
internet.

THE SSRF GUARD IS THE REASON `parse_video_id` IS ITS OWN FUNCTION
------------------------------------------------------------------
The endpoint above this takes a URL from the user and fetches server-side.
That is a server-side request forgery hole by default: a request for
`http://169.254.169.254/latest/meta-data/` or `http://localhost:8000/api/...`
is, to a naive fetcher, just a URL.

So no user-supplied URL is ever fetched. This extracts an ELEVEN-CHARACTER
VIDEO ID from a known host, and the caller builds its own request from that id
alone. The user controls which video; they never control the host, scheme,
port, or path. A URL that does not yield an id is rejected outright rather
than passed along.

`urlsplit` does the parsing rather than a regex over the whole URL. Hostname
matching in a hand-rolled regex is where this class of guard usually fails —
`https://youtube.com.evil.test/watch?v=…` and `https://evil.test/?x=youtube.com`
both contain "youtube.com", and `urlsplit(...).hostname` is what tells them
apart from the real thing.

WHY CUE JOINING IS NOT TRIVIAL
-------------------------------
Caption tracks are timed for READING, not for sentence structure. A single
sentence is routinely split across three cues at arbitrary points, so naively
joining cues with newlines produces text whose line breaks fall mid-clause —
and `domain/direction_analyze.py` now treats a newline as the longest pause
there is. Joining them wrong would put a deliberate 380 ms silence in the
middle of a sentence.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

__all__ = [
    "TranscriptCue",
    "TranscriptChapter",
    "InvalidVideoUrl",
    "parse_video_id",
    "parse_json3",
    "parse_chapters",
    "group_cues",
    "cues_to_text",
]

#: Exactly what YouTube ids are: 11 chars of URL-safe base64. Anchored, so a
#: longer string containing a valid id does not pass.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

#: Hosts a video id may be taken from. Compared against `urlsplit().hostname`
#: — never against the raw URL — and `www.`/`m.` are separate entries rather
#: than a suffix match, because a suffix match accepts `notyoutube.com`.
_ALLOWED_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be",
})

#: Path prefixes that carry the id in the path rather than in `?v=`.
_PATH_PREFIXES = ("/shorts/", "/embed/", "/live/", "/v/")


class InvalidVideoUrl(ValueError):
    """Not a YouTube video URL this will fetch. Deliberately a plain
    ValueError subclass: `domain/` raises no `AppError`, the API layer maps."""


@dataclass(frozen=True, slots=True)
class TranscriptCue:
    """One caption cue. Times in seconds from the start of the video."""

    text: str
    start_sec: float
    duration_sec: float


@dataclass(frozen=True, slots=True)
class TranscriptChapter:
    """
    One chapter of a video. Times in seconds from the start.

    `end_sec` is FOR DISPLAY ONLY and is never used to decide which chapter a
    cue belongs to — see `group_cues`.
    """

    index: int
    title: str
    start_sec: float
    end_sec: float | None


def parse_video_id(url: str) -> str:
    """
    Extract an 11-character video id from a YouTube URL, or raise.

    Accepts `watch?v=`, `youtu.be/<id>`, `/shorts/<id>`, `/embed/<id>`,
    `/live/<id>` and `/v/<id>`. Rejects everything else — including a bare id,
    which is deliberate: accepting one would mean this function sometimes
    validates a URL and sometimes does not, and a caller could then pass
    anything through by stripping the scheme.
    """
    raw = (url or "").strip()
    if not raw:
        raise InvalidVideoUrl("No URL given.")

    # A scheme-less "youtu.be/xyz" parses with an empty hostname and the whole
    # thing in `path`, which would sail past the host check. Give it a scheme
    # so `urlsplit` populates `hostname`.
    if "//" not in raw:
        raw = f"https://{raw}"

    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise InvalidVideoUrl("Only http and https URLs are accepted.")

    host = (parts.hostname or "").lower()
    if host not in _ALLOWED_HOSTS:
        raise InvalidVideoUrl(
            f"{host or 'that address'} is not YouTube. Only YouTube links are fetched."
        )

    # youtu.be puts the id in the path; youtube.com uses ?v= or a prefix path.
    candidate: str | None = None
    if host in ("youtu.be", "www.youtu.be"):
        candidate = parts.path.lstrip("/").split("/", 1)[0]
    else:
        query_v = parse_qs(parts.query).get("v")
        if query_v:
            candidate = query_v[0]
        else:
            for prefix in _PATH_PREFIXES:
                if parts.path.startswith(prefix):
                    candidate = parts.path[len(prefix) :].split("/", 1)[0]
                    break

    if not candidate or not _VIDEO_ID_RE.match(candidate):
        raise InvalidVideoUrl("That YouTube link has no video id in it.")
    return candidate


def parse_json3(payload: dict[str, Any]) -> list[TranscriptCue]:
    """
    Parse YouTube's `json3` caption format into cues.

    Shape: `{"events": [{"tStartMs": int, "dDurationMs": int,
    "segs": [{"utf8": str}, ...]}, ...]}`. Events with no `segs` are timing
    padding and carry no text; events whose joined text is only a newline are
    the format's own line breaks between cues, not content.

    Tolerant on purpose about missing keys, strict about nothing else: this is
    the one part of the feature that breaks when YouTube changes its format,
    and it should degrade to "no cues" rather than a 500.
    """
    cues: list[TranscriptCue] = []
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        segs = event.get("segs")
        if not isinstance(segs, list):
            continue
        text = "".join(
            str(seg.get("utf8", "")) for seg in segs if isinstance(seg, dict)
        )
        text = text.replace("\n", " ").strip()
        if not text:
            continue
        cues.append(
            TranscriptCue(
                text=text,
                start_sec=float(event.get("tStartMs", 0)) / 1000.0,
                duration_sec=float(event.get("dDurationMs", 0)) / 1000.0,
            )
        )
    return cues


def parse_chapters(raw: object) -> list[TranscriptChapter]:
    """
    yt-dlp's `chapters` list, or `[]`.

    Same tolerance contract as `parse_json3`, for the same reason: this is
    third-party data whose shape is not ours, and it should degrade to "no
    chapters" rather than a 500. `None`, a non-list, and entries missing or
    mistyping `start_time` are all skipped rather than raising.

    Sorted by start, because yt-dlp does not promise an order and the boundary
    search below assumes one. A missing `title` gets a positional fallback —
    a chapter with no name is still a real division of the video, and dropping
    it would silently merge it into its neighbour.
    """
    if not isinstance(raw, list):
        return []

    parsed: list[tuple[float, float | None, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            start = float(entry["start_time"])
        except (KeyError, TypeError, ValueError):
            continue
        try:
            end: float | None = float(entry["end_time"])
        except (KeyError, TypeError, ValueError):
            end = None
        title = str(entry.get("title") or "").strip()
        parsed.append((start, end, title))

    parsed.sort(key=lambda item: item[0])
    return [
        TranscriptChapter(
            index=i,
            title=title or f"Chapter {i + 1}",
            start_sec=start,
            end_sec=end,
        )
        for i, (start, end, title) in enumerate(parsed)
    ]


def group_cues(
    cues: list[TranscriptCue],
    chapters: list[TranscriptChapter],
) -> list[tuple[TranscriptChapter | None, list[TranscriptCue]]]:
    """
    Split cues into one group per chapter, in video order.

    **With no chapters this returns exactly `[(None, cues)]`.** That is not a
    convenience — it is what makes the caller a single code path and keeps a
    chapter-less video producing byte-identical output to before chapters
    existed. Every downstream difference follows from the number of groups.

    BOUNDARIES COME FROM `start_sec` ALONE. A chapter spans `[its start, the
    next chapter's start)`, and `end_sec` is ignored. yt-dlp emits chapters
    with gaps, with overlaps, and with no `end_time` at all (description-derived
    ones routinely do), and one rule handles all three where three rules would
    disagree with each other.

    A cue belongs to the chapter containing its OWN start — not its midpoint,
    and not wherever most of it lies. Deterministic, and it matches what
    YouTube's chapter bar does with the same cue.

    Two shapes worth knowing:

    - Cues starting before the first chapter get a leading `(None, ...)` group.
      YouTube requires a chapter at 00:00, but description-derived ones do not,
      so this is reachable.
    - **A chapter with no cues in its window is DROPPED**, not returned empty.
      An empty group would render as a heading with nothing under it, which
      reads as a bug in the transcript rather than a quiet stretch of video.
    """
    if not chapters:
        return [(None, cues)]

    starts = [chapter.start_sec for chapter in chapters]
    buckets: dict[int, list[TranscriptCue]] = {}
    for cue in cues:
        # -1 is the pre-first-chapter bucket. `bisect_right` puts a cue exactly
        # on a boundary into the LATER chapter, which is the half-open reading
        # of the span above.
        slot = bisect_right(starts, cue.start_sec) - 1
        buckets.setdefault(slot, []).append(cue)

    groups: list[tuple[TranscriptChapter | None, list[TranscriptCue]]] = []
    if buckets.get(-1):
        groups.append((None, buckets[-1]))
    for i, chapter in enumerate(chapters):
        group = buckets.get(i)
        if group:
            groups.append((chapter, group))
    return groups


#: Terminators after which a cue boundary is a real sentence boundary. Covers
#: Latin, Perso-Arabic (`۔`/`؟`) and Devanagari (`।`/`॥`) — a Hindi caption
#: track ends its sentences with `।`, and treating only `.` as terminal would
#: run an entire Hindi transcript into one paragraph.
_TERMINALS = ".!?۔؟।॥…"


def cues_to_text(cues: list[TranscriptCue]) -> str:
    """
    Join cues into flowing text.

    Cue boundaries are NOT sentence boundaries — caption tracks split a
    sentence across cues wherever the line got long enough. So cues are joined
    with a SPACE, and a newline is emitted only where the previous cue actually
    ended on sentence punctuation.

    That distinction is load-bearing now: `direction_analyze._split_units`
    treats a newline as a paragraph break and gives it the longest pause there
    is. Joining every cue with a newline would put a deliberate ~380 ms silence
    in the middle of most sentences.
    """
    lines: list[str] = []
    current: list[str] = []
    for cue in cues:
        current.append(cue.text)
        if cue.text.rstrip().endswith(tuple(_TERMINALS)):
            lines.append(" ".join(current))
            current = []
    if current:
        lines.append(" ".join(current))
    # Collapse the double spaces cue text routinely carries, without touching
    # the newlines this just decided on.
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in lines if line.strip())
