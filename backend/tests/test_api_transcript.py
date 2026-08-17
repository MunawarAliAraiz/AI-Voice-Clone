"""
`POST /api/transcript/fetch` — YouTube caption import.

OFFLINE. `yt_dlp` and the caption download are both monkeypatched; nothing in
this suite touches the network. That is deliberate rather than convenient: a
test that really called YouTube would fail on rate limiting rather than on a
regression, and would fail differently in CI than on a laptop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeScheduler


def _client(tmp_path: Path, **settings_kwargs: Any):
    app = create_app(
        scheduler=FakeScheduler(), settings=Settings(data_dir=tmp_path, **settings_kwargs)
    )
    return TestClient(app)


def _json3(*sentences: str) -> dict[str, Any]:
    """A caption track split the way real ones are — mid-sentence."""
    events = []
    t = 0
    for sentence in sentences:
        words = sentence.split()
        for i in range(0, len(words), 3):
            events.append({
                "tStartMs": t,
                "dDurationMs": 1000,
                "segs": [{"utf8": " ".join(words[i : i + 3])}],
            })
            t += 1000
    return {"events": events}


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    info: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replace the two network calls. Returns a dict recording what was asked for."""
    seen: dict[str, Any] = {}

    def fake_fetch_info(video_id: str, timeout_sec: float) -> dict[str, Any]:
        seen["video_id"] = video_id
        return info if info is not None else {
            "title": "A talk",
            "duration": 630.0,
            "subtitles": {
                "en": [{"ext": "json3", "url": "https://example.invalid/en.json3"}]
            },
            "automatic_captions": {
                "ur": [{"ext": "json3", "url": "https://example.invalid/ur.json3"}]
            },
        }

    monkeypatch.setattr("app.api.routers.transcript._fetch_info", fake_fetch_info)

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return payload if payload is not None else _json3(
                "Hello there this is the first sentence of the talk.",
                "And here is the second one for you.",
            )

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def get(self, url: str) -> _Response:
            seen["url"] = url
            return _Response()

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    return seen


# ── The SSRF boundary, through the real HTTP layer ───────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/api/voices",
        "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com@evil.test/watch?v=dQw4w9WgXcQ",
        "file:///etc/passwd",
    ],
)
def test_a_non_youtube_url_is_rejected_without_any_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """
    422 AND nothing was fetched. The second half is the point: a guard that
    rejects only after making the request is not a guard.
    """
    seen = _install_fakes(monkeypatch)
    with _client(tmp_path) as c:
        r = c.post("/api/transcript/fetch", json={"url": url})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "INVALID_VIDEO_URL"
    assert seen == {}, f"a rejected URL still caused a fetch: {seen}"


# ── The happy path ───────────────────────────────────────────────────────────


def test_fetch_returns_text_tracks_and_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _install_fakes(monkeypatch)
    with _client(tmp_path) as c:
        r = c.post(
            "/api/transcript/fetch",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )
    assert r.status_code == 200, r.text
    body = r.json()

    assert seen["video_id"] == "dQw4w9WgXcQ"
    assert body["title"] == "A talk"
    assert body["duration_sec"] == 630.0
    # Cues split mid-sentence must be rejoined, not left one line per cue.
    assert "Hello there this is the first sentence of the talk." in body["text"]
    assert body["chunks"]
    assert body["script"] == "latin"
    assert body["needs_transliteration"] is False


def test_an_authored_track_is_preferred_over_auto_captions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto-generated Urdu captions are markedly worse, so a human-authored
    track wins when the caller expressed no preference."""
    seen = _install_fakes(monkeypatch)
    with _client(tmp_path) as c:
        r = c.post(
            "/api/transcript/fetch",
            json={"url": "https://youtu.be/dQw4w9WgXcQ"},
        )
    assert r.json()["chosen_track"]["is_auto_generated"] is False
    assert seen["url"].endswith("en.json3")


def test_a_requested_language_wins_over_the_authored_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _install_fakes(monkeypatch)
    with _client(tmp_path) as c:
        r = c.post(
            "/api/transcript/fetch",
            json={"url": "https://youtu.be/dQw4w9WgXcQ", "language": "ur"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["chosen_track"]["language"] == "ur"
    assert seen["url"].endswith("ur.json3")


def test_a_region_tagged_track_matches_a_bare_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """YouTube tags tracks inconsistently — `ur-PK` is what the user meant by
    `ur`, and an exact match would miss it."""
    _install_fakes(monkeypatch, info={
        "title": "T",
        "duration": 10.0,
        "subtitles": {"ur-PK": [{"ext": "json3", "url": "https://example.invalid/u.json3"}]},
    })
    with _client(tmp_path) as c:
        r = c.post(
            "/api/transcript/fetch",
            json={"url": "https://youtu.be/dQw4w9WgXcQ", "language": "ur"},
        )
    assert r.json()["chosen_track"]["language"] == "ur-PK"


def test_a_translated_track_never_beats_the_videos_own_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    REGRESSION, caught by a REAL fetch and not by this file's earlier fixtures.

    A popular video carries dozens of community translations, so "the first
    authored track" is effectively alphabetical — fetching an English lecture
    returned its ARABIC translation. A translation is not what the speaker
    said, which is the entire thing being handed to a voice model.

    `ar` deliberately sorts before `en` here, exactly as it did on the real
    video, so this fails if the ordering rule regresses.
    """
    _install_fakes(monkeypatch, info={
        "title": "But what is a neural network?",
        "duration": 1120.0,
        "language": "en",
        "subtitles": {
            "ar": [{"ext": "json3", "url": "https://example.invalid/ar.json3"}],
            "de": [{"ext": "json3", "url": "https://example.invalid/de.json3"}],
            "en": [{"ext": "json3", "url": "https://example.invalid/en.json3"}],
        },
    })
    with _client(tmp_path) as c:
        r = c.post(
            "/api/transcript/fetch",
            json={"url": "https://youtu.be/aircAruvnKk"},
        )
    assert r.json()["chosen_track"]["language"] == "en"


def test_english_wins_when_the_videos_language_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One step weaker than the rule above: with no `language` on the video,
    the source track is far more often English than whichever locale sorts
    first."""
    _install_fakes(monkeypatch, info={
        "title": "T",
        "duration": 10.0,
        "subtitles": {
            "ar": [{"ext": "json3", "url": "https://example.invalid/ar.json3"}],
            "en": [{"ext": "json3", "url": "https://example.invalid/en.json3"}],
        },
    })
    with _client(tmp_path) as c:
        r = c.post(
            "/api/transcript/fetch",
            json={"url": "https://youtu.be/dQw4w9WgXcQ"},
        )
    assert r.json()["chosen_track"]["language"] == "en"


def test_an_explicit_request_still_beats_the_videos_own_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asking for Urdu on an English video must give Urdu — the ordering rule
    is a DEFAULT, not an override of what the caller said."""
    _install_fakes(monkeypatch, info={
        "title": "T",
        "duration": 10.0,
        "language": "en",
        "subtitles": {
            "en": [{"ext": "json3", "url": "https://example.invalid/en.json3"}],
            "ur": [{"ext": "json3", "url": "https://example.invalid/ur.json3"}],
        },
    })
    with _client(tmp_path) as c:
        r = c.post(
            "/api/transcript/fetch",
            json={"url": "https://youtu.be/dQw4w9WgXcQ", "language": "ur"},
        )
    assert r.json()["chosen_track"]["language"] == "ur"


# ── Failure modes ────────────────────────────────────────────────────────────


def test_a_video_with_no_captions_is_404_not_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fakes(monkeypatch, info={"title": "Silent", "duration": 5.0})
    with _client(tmp_path) as c:
        r = c.post(
            "/api/transcript/fetch",
            json={"url": "https://youtu.be/dQw4w9WgXcQ"},
        )
    assert r.status_code == 404
    assert r.json()["code"] == "TRANSCRIPT_UNAVAILABLE"


def test_youtube_refusing_is_502_not_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    On a datacenter IP — which every pod has — a refusal is the EXPECTED
    outcome often enough that it must not read as an application bug.
    """
    def boom(video_id: str, timeout_sec: float) -> dict[str, Any]:
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr("app.api.routers.transcript._fetch_info", boom)
    with _client(tmp_path) as c:
        r = c.post(
            "/api/transcript/fetch",
            json={"url": "https://youtu.be/dQw4w9WgXcQ"},
        )
    assert r.status_code == 502
    assert r.json()["code"] == "TRANSCRIPT_FETCH_FAILED"


def test_an_empty_caption_track_is_reported_not_returned_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fakes(monkeypatch, payload={"events": []})
    with _client(tmp_path) as c:
        r = c.post(
            "/api/transcript/fetch",
            json={"url": "https://youtu.be/dQw4w9WgXcQ"},
        )
    assert r.status_code == 404
    assert r.json()["code"] == "TRANSCRIPT_UNAVAILABLE"


# ── Hindi is a SOURCE format, never a target language ────────────────────────


def test_a_devanagari_transcript_is_flagged_as_needing_transliteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    No catalog spec renders Devanagari (`routing.py` raises NoRouteError for it
    on purpose), so the response has to SAY so rather than let the user click
    Generate into a 422.
    """
    _install_fakes(monkeypatch, payload=_json3(
        "कल ऑफिस में मीटिंग है और रिपोर्ट भी देनी है।",
        "फिर मैं घर जाऊंगा।",
    ))
    with _client(tmp_path) as c:
        r = c.post(
            "/api/transcript/fetch",
            json={"url": "https://youtu.be/dQw4w9WgXcQ"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["script"] == "devanagari"
    assert body["needs_transliteration"] is True


def test_chunks_respect_the_configured_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_text = " ".join(f"This is sentence number {i}." for i in range(60))
    _install_fakes(monkeypatch, payload=_json3(long_text))
    with _client(tmp_path, transcript_chunk_chars=200) as c:
        r = c.post(
            "/api/transcript/fetch",
            json={"url": "https://youtu.be/dQw4w9WgXcQ"},
        )
    chunks = r.json()["chunks"]
    assert len(chunks) > 1
    assert all(len(ch["text"]) <= 200 for ch in chunks)
    assert [ch["index"] for ch in chunks] == list(range(len(chunks)))
