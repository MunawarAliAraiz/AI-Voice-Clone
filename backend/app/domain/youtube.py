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
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

__all__ = [
    "TranscriptCue",
    "InvalidVideoUrl",
    "parse_video_id",
    "parse_json3",
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
