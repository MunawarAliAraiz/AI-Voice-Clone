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
from app.domain.youtube import cues_to_text, parse_json3
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


def test_a_missing_yt_dlp_does_not_masquerade_as_rate_limiting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The pod's API venv predated `yt-dlp` being added to pyproject.toml, and the
    broad `except Exception` below the fetch reported that ImportError as
    "could not reach YouTube — on a datacenter connection this is usually rate
    limiting, try again shortly."

    Every clause of that was wrong. The box was reaching youtube.com with a 200,
    nothing was rate limited, and waiting could never have fixed it. A WRONG
    cause is worse than an unknown one: it sends someone to wait instead of to
    `uv sync`. So the ImportError gets its own clause ABOVE the broad one, its
    own code, and a message naming the actual fix.
    """
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise ModuleNotFoundError("No module named 'yt_dlp'")

    monkeypatch.setattr("app.api.routers.transcript._fetch_info", _boom)
    with _client(tmp_path) as c:
        r = c.post(
            "/api/transcript/fetch",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )

    assert r.status_code == 503, r.text
    body = r.json()
    assert body["code"] == "TRANSCRIPT_TOOL_MISSING"
    assert "yt-dlp" in body["detail"]
    assert "uv sync" in body["detail"], "the message must name the fix"
    # The specific regression: it must NOT blame the network.
    assert "rate limit" not in body["detail"].lower()
    assert "try again" not in body["detail"].lower()


# ── Chapter-aware chunking ───────────────────────────────────────────────────
# The cues are chunked once per chapter, so a part never straddles a topic
# change. `_install_fakes` already lets `chapters` be just another key in the
# fake `info` dict.


def _chaptered_json3() -> dict[str, Any]:
    """Two chapters' worth of cues. Cue N starts at N seconds (see `_json3`),
    so a boundary at 2 s puts the first two cues in one chapter and the rest in
    the next."""
    return _json3(
        "One two three four five six seven eight nine ten eleven twelve.",
    )


def test_a_video_with_no_chapters_behaves_exactly_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    THE REGRESSION THAT MATTERS MOST.

    Chapter grouping is an enhancement; a chapter-less video is the common case
    and must be untouched by it. `group_cues` returns exactly one group when
    there are no chapters, so this asserts the whole pipeline collapses back to
    a single pass — same text, same chunks, same indexes.

    Asserted for `chapters` both ABSENT and explicitly `None`, because yt-dlp
    does both and only one of them is the obvious case.
    """
    results = []
    for chapters in (None, "absent"):
        info: dict[str, Any] = {
            "title": "A talk", "duration": 630.0,
            "subtitles": {"en": [{"ext": "json3", "url": "https://example.invalid/en.json3"}]},
        }
        if chapters is None:
            info["chapters"] = None
        _install_fakes(monkeypatch, info=info)
        with _client(tmp_path) as c:
            body = c.post("/api/transcript/fetch", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}).json()
        results.append(body)
        assert body["chapters"] == []
        assert body["truncated"] is False
        assert all(ch["chapter_index"] is None for ch in body["chunks"])

    # Both spellings of "no chapters" produce identical output.
    assert results[0]["text"] == results[1]["text"]
    assert results[0]["chunks"] == results[1]["chunks"]


def test_no_chapters_adds_no_newline_of_its_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Guards the ~380 ms silence rule directly.

    NOT "the text contains no newlines" — that was this test's first draft and
    it was wrong. `cues_to_text` emits newlines at real sentence boundaries and
    always has; that is the paragraph behaviour `direction_analyze` depends on.

    What must not happen is the CHAPTER JOIN adding one. A topic change earns a
    blank line; a video with no topic changes must earn nothing. Asserted as
    byte-identity against the pure function, so it cannot pass by coincidence.
    """
    payload = _json3(
        "Hello there this is the first sentence of the talk.",
        "And here is the second one for you.",
    )
    _install_fakes(monkeypatch, payload=payload)
    with _client(tmp_path) as c:
        body = c.post(
            "/api/transcript/fetch",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        ).json()

    assert body["text"] == cues_to_text(parse_json3(payload))


def test_chunk_indexes_are_global_across_chapters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    `chunk_for_synthesis` numbers from 0 on every call, and it is now called
    once per chapter. Without global re-numbering several parts share an index,
    and the UI keys conversion results off it — part 8's Urdu would be written
    onto part 1 and look completely plausible there.
    """
    _install_fakes(monkeypatch, info={
        "title": "Chaptered", "duration": 60.0,
        "chapters": [
            {"start_time": 0, "end_time": 2, "title": "Intro"},
            {"start_time": 2, "title": "Body"},
        ],
        "subtitles": {"en": [{"ext": "json3", "url": "https://example.invalid/en.json3"}]},
    }, payload=_chaptered_json3())
    with _client(tmp_path, transcript_chunk_chars=40, transcript_chunk_min_chars=0) as c:
        body = c.post("/api/transcript/fetch", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}).json()

    indexes = [ch["index"] for ch in body["chunks"]]
    assert indexes == list(range(len(indexes))), indexes
    assert len(indexes) == len(set(indexes)), "duplicate index across chapters"


def test_a_part_never_straddles_a_chapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole point of chunking per chapter. Every part's text must be
    contained in the text of the chapter it claims."""
    _install_fakes(monkeypatch, info={
        "title": "Chaptered", "duration": 60.0,
        "chapters": [
            {"start_time": 0, "end_time": 2, "title": "Intro"},
            {"start_time": 2, "title": "Body"},
        ],
        "subtitles": {"en": [{"ext": "json3", "url": "https://example.invalid/en.json3"}]},
    }, payload=_chaptered_json3())
    with _client(tmp_path, transcript_chunk_chars=40, transcript_chunk_min_chars=0) as c:
        body = c.post("/api/transcript/fetch", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}).json()

    assert len(body["chapters"]) == 2
    assert [c["title"] for c in body["chapters"]] == ["Intro", "Body"]
    # A chapter boundary is a paragraph break in the joined text.
    assert "\n\n" in body["text"]

    # Every chunk names a chapter that exists, and no chunk's text spans the
    # boundary between the two chapter bodies.
    known = {c["index"] for c in body["chapters"]}
    for chunk in body["chunks"]:
        assert chunk["chapter_index"] in known
        assert "\n\n" not in chunk["text"]


def test_a_chapter_with_no_cues_is_not_advertised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A heading with no parts under it reads as a broken transcript rather
    than a quiet stretch of video, so it is dropped from `chapters` too — not
    just from the grouping."""
    _install_fakes(monkeypatch, info={
        "title": "Chaptered", "duration": 6000.0,
        "chapters": [
            {"start_time": 0, "title": "Talking"},
            {"start_time": 5000, "title": "Silent outro"},
        ],
        "subtitles": {"en": [{"ext": "json3", "url": "https://example.invalid/en.json3"}]},
    })
    with _client(tmp_path) as c:
        body = c.post("/api/transcript/fetch", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}).json()
    assert [c["title"] for c in body["chapters"]] == ["Talking"]


def test_truncation_is_reported_rather_than_only_logged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hitting the ceiling used to be a `logger.warning` and nothing else, so
    the user was handed a silently shortened transcript."""
    _install_fakes(monkeypatch)
    with _client(tmp_path, transcript_max_chars=40) as c:
        body = c.post("/api/transcript/fetch", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}).json()

    assert body["truncated"] is True
    assert len(body["text"]) <= 40
    # And no chunk quotes text that was cut away.
    for chunk in body["chunks"]:
        assert chunk["text"] in body["text"]
