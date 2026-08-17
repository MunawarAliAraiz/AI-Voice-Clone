"""
YouTube transcript import.

WHY THIS IS SYNCHRONOUS AND NOT A JOB
--------------------------------------
Same argument as `routers/text.py`'s title endpoint, from the other side: the
`jobs` table is the GPU QUEUE (golden rule 8), and this never touches the GPU.
Putting a network fetch in it would make a caption download queue behind a
60-second synthesis for no reason, and would report an ETA computed from
VRAM residency for work that has nothing to do with VRAM.

WHAT THIS DOES NOT DO
---------------------
It does not synthesize, and it does not convert scripts. A fetched transcript
lands in an EDITABLE FIELD, because auto-generated captions — especially Urdu
and Hindi ones — are a rough draft, not a script. Handing them straight to a
model would be presenting a machine's guess as the user's words.

THE SSRF BOUNDARY
-----------------
`domain/youtube.parse_video_id` is the only thing that decides what may be
fetched, and it returns an 11-character id, not a URL. Everything below builds
its own request from that id. A user-supplied URL is never passed to a fetcher
— see that module's docstring for why the hostname check lives in `urlsplit`
rather than a regex.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from ...config import Settings
from ...domain.language import Script, profile_text
from ...domain.text import chunk_for_synthesis
from ...domain.youtube import InvalidVideoUrl, cues_to_text, parse_json3, parse_video_id
from ...exceptions import (
    InvalidVideoUrlError,
    TranscriptFetchFailedError,
    TranscriptUnavailableError,
)
from ...inference.catalog import ModelCatalog
from ..deps import get_catalog, get_settings
from ..schemas.transcript import (
    TranscriptChunk,
    TranscriptRequest,
    TranscriptResponse,
    TranscriptTrack,
)

router = APIRouter(prefix="/transcript", tags=["transcript"])
logger = logging.getLogger(__name__)

#: `json3` over `vtt`/`srv3`: it is plain JSON with explicit millisecond
#: timings, so `parse_json3` needs no subtitle-format parser and no regex over
#: timestamp lines.
_SUB_FORMAT = "json3"


def _fetch_info(video_id: str, timeout_sec: float) -> dict[str, Any]:
    """
    Ask yt-dlp what this video offers. BLOCKING — call via a threadpool.

    Imported inside the function, not at module scope: yt-dlp is a large
    dependency used by exactly one endpoint, and importing it at startup would
    put it in the critical path of every request the API serves.
    """
    from yt_dlp import YoutubeDL

    options = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": timeout_sec,
        # Ask for both. `subtitles` is human-authored; `automatic_captions` is
        # YouTube's ASR. Which one we got is reported back rather than blurred,
        # because the quality difference is large in Urdu and Hindi.
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitlesformat": _SUB_FORMAT,
    }
    with YoutubeDL(options) as ydl:
        # `download=False` is what keeps this a metadata call. The video's
        # media is never fetched — only the caption track is, below.
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)


def _tracks(info: dict[str, Any]) -> list[tuple[TranscriptTrack, str]]:
    """
    Flatten yt-dlp's two caption dicts into `(track, url)` pairs.

    Authored tracks come first, so a video with both is read from the human
    one by default.
    """
    out: list[tuple[TranscriptTrack, str]] = []
    for key, is_auto in (("subtitles", False), ("automatic_captions", True)):
        for lang, formats in (info.get(key) or {}).items():
            entry = next(
                (f for f in formats if f.get("ext") == _SUB_FORMAT),
                None,
            )
            if entry and entry.get("url"):
                out.append((
                    TranscriptTrack(
                        language=lang,
                        name=entry.get("name") or None,
                        is_auto_generated=is_auto,
                    ),
                    entry["url"],
                ))
    return out


def _choose(
    tracks: list[tuple[TranscriptTrack, str]], preferred: str | None
) -> tuple[TranscriptTrack, str]:
    """
    Pick a track: the requested language if present, else the first authored
    one, else the first of anything.

    Prefix-matched (`ur` matches `ur-PK`), because YouTube's language tags
    carry regions inconsistently and an exact match would miss the track the
    user plainly meant.
    """
    if preferred:
        want = preferred.lower()
        for track, url in tracks:
            if track.language.lower().split("-")[0] == want.split("-")[0]:
                return track, url
    for track, url in tracks:
        if not track.is_auto_generated:
            return track, url
    return tracks[0]


@router.post("/fetch", response_model=TranscriptResponse)
async def fetch_transcript(
    body: TranscriptRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
) -> TranscriptResponse:
    try:
        video_id = parse_video_id(body.url)
    except InvalidVideoUrl as exc:
        # The SSRF rejection surfaces here, as a 422 naming what was wrong —
        # not a generic 400, and never by attempting the fetch to find out.
        raise InvalidVideoUrlError(str(exc)) from exc

    try:
        info = await run_in_threadpool(_fetch_info, video_id, settings.transcript_timeout_sec)
    except Exception as exc:
        # Deliberately broad: yt-dlp raises a family of its own exception types
        # plus whatever the network layer produces, and every one of them means
        # the same thing to a caller. Logged in full so a real bug is still
        # diagnosable from the server side.
        logger.exception("yt-dlp failed for video %s", video_id)
        raise TranscriptFetchFailedError(
            "Could not reach YouTube for that video. On a datacenter connection "
            "this is usually rate limiting — try again shortly."
        ) from exc

    tracks = _tracks(info or {})
    if not tracks:
        raise TranscriptUnavailableError(
            "That video has no captions this can read. Paste the text in by hand, "
            "or pick a video that publishes a transcript."
        )

    chosen, url = _choose(tracks, body.language)

    try:
        import httpx

        async with httpx.AsyncClient(timeout=settings.transcript_timeout_sec) as client:
            # `url` comes from yt-dlp's own response for a video id THIS
            # validated — not from the caller. That is what keeps the SSRF
            # boundary intact through the second request.
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.exception("caption track fetch failed for %s", video_id)
        raise TranscriptFetchFailedError(
            "The caption track could not be downloaded."
        ) from exc

    cues = parse_json3(payload)
    if not cues:
        raise TranscriptUnavailableError(
            "That caption track came back empty. It may be a placeholder track "
            "with no text in it."
        )

    text = cues_to_text(cues)
    if len(text) > settings.transcript_max_chars:
        text = text[: settings.transcript_max_chars]
        logger.warning("transcript for %s truncated to the configured ceiling", video_id)

    # Same detector routing uses, so what this reports and what `resolve()`
    # would decide cannot drift apart.
    profile = profile_text(text, "ur")
    script = profile.script
    needs_transliteration = script is Script.DEVANAGARI and not catalog.candidates(
        "hi", Script.DEVANAGARI
    )

    chunks = chunk_for_synthesis(
        text,
        script,
        max_chars=settings.transcript_chunk_chars,
        min_chars=settings.transcript_chunk_min_chars,
    )

    return TranscriptResponse(
        video_id=video_id,
        title=(info or {}).get("title"),
        duration_sec=(info or {}).get("duration"),
        available_tracks=[t for t, _ in tracks],
        chosen_track=chosen,
        text=text,
        script=script.value,
        needs_transliteration=needs_transliteration,
        chunks=[
            TranscriptChunk(
                index=c.index, text=c.text, ends_on_sentence=c.ends_on_sentence
            )
            for c in chunks
        ],
    )
