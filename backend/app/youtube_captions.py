"""Import public caption tracks, with bounded requests and no media downloads."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from requests import Session

from .exceptions import AppError

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_REQUESTS = 4
TOTAL_SECONDS = 40
_VIDEO_ID = re.compile(r"^[a-zA-Z0-9_-]{11}$")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


class CaptionImportError(AppError):
    title = "Caption import failed"
    http_status = 409

    def __init__(self, code: str, detail: str):
        self.code = code
        self.http_status = 422 if code.startswith("invalid_") else 409
        super().__init__(detail)


def video_id_from_url(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise CaptionImportError(
            "invalid_youtube_url", "The URL contains unsupported control characters."
        )
    value = value.strip()
    if _VIDEO_ID.fullmatch(value):
        return value
    if not value.startswith(("https://", "http://")):
        value = "https://" + value
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and parsed.hostname in _HOSTS
        valid = (
            valid and not parsed.username and not parsed.password and parsed.port in {None, 80, 443}
        )
    except ValueError:
        valid = False
    if not valid:
        raise CaptionImportError(
            "invalid_youtube_url", "Enter a YouTube video URL or its 11-character video ID."
        )
    pieces = parsed.path.strip("/").split("/")
    if parsed.hostname == "youtu.be" and len(pieces) == 1:
        video_id = pieces[0]
    elif parsed.path == "/watch":
        ids = parse_qs(parsed.query).get("v", [])
        video_id = ids[0] if len(ids) == 1 else ""
    elif len(pieces) == 2 and pieces[0] in {"shorts", "embed", "live"}:
        video_id = pieces[1]
    else:
        video_id = ""
    if not _VIDEO_ID.fullmatch(video_id):
        raise CaptionImportError(
            "invalid_youtube_url", "The URL does not identify one YouTube video."
        )
    return video_id


class BoundedCaptionSession(Session):
    def __init__(self):
        super().__init__()
        self.trust_env = False
        self.deadline = time.monotonic() + TOTAL_SECONDS
        self.request_count = 0
        self.received_bytes = 0

    def request(self, method, url, **kwargs):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.youtube.com"
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.path not in {"/watch", "/api/timedtext", "/youtubei/v1/player"}
        ):
            raise CaptionImportError(
                "caption_url_blocked",
                "YouTube returned an unsupported caption location. Paste the transcript instead.",
            )
        remaining = self.deadline - time.monotonic()
        self.request_count += 1
        if remaining <= 0 or self.request_count > MAX_REQUESTS:
            raise CaptionImportError(
                "caption_timeout", "Caption import timed out. Try again or paste the transcript."
            )
        self.cookies.clear()
        kwargs.update(
            timeout=(min(5, remaining), min(10, remaining)), stream=True, allow_redirects=False
        )
        response = super().request(method, url, **kwargs)
        try:
            if 300 <= response.status_code < 400:
                raise CaptionImportError(
                    "caption_access_blocked",
                    "YouTube requires a redirect or consent. "
                    "Open YouTube and paste the transcript instead.",
                )
            content = bytearray()
            for chunk in response.iter_content(64 * 1024):
                content.extend(chunk)
                self.received_bytes += len(chunk)
                if len(content) > MAX_RESPONSE_BYTES or self.received_bytes > MAX_TOTAL_BYTES:
                    raise CaptionImportError(
                        "captions_too_large",
                        "YouTube's caption response is too large. Paste a shorter transcript.",
                    )
                if time.monotonic() >= self.deadline:
                    raise CaptionImportError(
                        "caption_timeout",
                        "Caption import timed out. Try again or paste the transcript.",
                    )
            if b'action="https://consent.youtube.com/' in content:
                raise CaptionImportError(
                    "caption_access_blocked",
                    "YouTube requires consent. Open YouTube and paste the transcript instead.",
                )
            response._content = bytes(content)
            response._content_consumed = True
            return response
        finally:
            response.close()
            self.cookies.clear()


@dataclass(frozen=True)
class CaptionTrack:
    language: str
    language_code: str
    is_generated: bool


@dataclass(frozen=True)
class ImportedCaptions:
    video_id: str
    text: str
    language: str
    language_code: str
    is_generated: bool
    available_tracks: tuple[CaptionTrack, ...] = ()
    provider: str = "local"


def fetch_captions(
    video_id: str, language: str | None = None, *, max_chars: int = 200_000
) -> ImportedCaptions:
    if not _VIDEO_ID.fullmatch(video_id) or language not in {None, "en", "hi", "ur"}:
        raise CaptionImportError(
            "invalid_caption_request",
            "Select English, Hindi or Urdu and enter a valid YouTube video.",
        )
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:
        raise CaptionImportError(
            "caption_import_unavailable",
            "Caption import is not installed in this build. Paste your script instead.",
        ) from exc
    try:
        with BoundedCaptionSession() as session:
            tracks = list(YouTubeTranscriptApi(http_client=session).list(video_id))
            supported = [
                t for t in tracks if t.language_code.split("-", 1)[0] in {"en", "hi", "ur"}
            ]
            if tracks and not supported:
                raise CaptionImportError(
                    "captions_unsupported",
                    "English, Hindi or Urdu captions are not available for this video.",
                )
            matching = [
                track
                for track in supported
                if language is None or track.language_code.split("-", 1)[0] == language
            ]
            if not matching:
                raise CaptionImportError(
                    "captions_not_available",
                    "No captions are available in the selected language. "
                    "Choose another language or paste its transcript.",
                )
            track = min(matching, key=lambda item: item.is_generated)
            lines, count = [], 0
            for snippet in track.fetch():
                text = " ".join(snippet.text.split())
                if not text:
                    continue
                count += len(text) + 1
                if count > max_chars:
                    raise CaptionImportError(
                        "captions_too_long",
                        f"This transcript exceeds {max_chars:,} characters. "
                        "Paste a shorter section.",
                    )
                lines.append(text)
            if not lines:
                raise CaptionImportError(
                    "captions_empty",
                    "The selected caption track is empty. Paste your script instead.",
                )
            return ImportedCaptions(
                video_id,
                "\n".join(lines),
                track.language_code.split("-", 1)[0],
                track.language_code,
                track.is_generated,
                tuple(CaptionTrack(t.language, t.language_code, t.is_generated) for t in supported),
            )
    except CaptionImportError:
        raise
    except Exception as exc:
        kind = type(exc).__name__
        if kind in {"TranscriptsDisabled", "NoTranscriptFound"}:
            code, detail = (
                "captions_not_available",
                "No public captions are available in the selected language. "
                "Paste the transcript instead.",
            )
        elif kind == "AgeRestricted":
            code, detail = (
                "caption_video_restricted",
                "This video requires age verification. Paste its transcript instead.",
            )
        elif kind in {"VideoUnavailable", "VideoUnplayable"}:
            code, detail = (
                "caption_video_unavailable",
                "This video is unavailable or private. Check the link or paste its transcript.",
            )
        elif kind in {"RequestBlocked", "IpBlocked", "PoTokenRequired"}:
            code, detail = (
                "caption_access_blocked",
                "YouTube blocked caption access from this PC. "
                "Connect Apify in YouTube settings or paste the transcript.",
            )
        elif kind == "FailedToCreateConsentCookie":
            code, detail = (
                "caption_consent_required",
                "YouTube requires consent. Open YouTube and paste the transcript instead.",
            )
        else:
            code, detail = (
                "caption_fetch_failed",
                "Could not retrieve YouTube captions. Try again or paste the transcript.",
            )
        raise CaptionImportError(code, detail) from exc
