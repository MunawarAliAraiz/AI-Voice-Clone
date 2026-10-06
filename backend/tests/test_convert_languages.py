"""Conversion contracts: explicit language, bounded captions, durable draft jobs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from requests import Response, Session
from youtube_transcript_api import YouTubeTranscriptApi

from app.config import Settings
from app.exceptions import TransliteratorUnavailableError
from app.inference.protocol import TransliterateResult
from app.inference.remote_features import RemoteTransliterator
from app.inference.runtimes.gemma_transliterator import build_translation_prompt
from app.remote_worker.main import create_worker_app
from app.youtube_captions import (
    BoundedCaptionSession,
    CaptionImportError,
    ImportedCaptions,
    fetch_captions,
    video_id_from_url,
)
from tests.fakes import FakeScheduler
from tests.test_api_transcript import _client as transcript_client
from tests.test_api_transliterate import _client, _FakeTransliterator, _poll


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com/watch?v=abcdefgh_12",
        "https://youtu.be/abcdefgh_12?t=2",
        "https://m.youtube.com/shorts/abcdefgh_12",
        "youtube.com/embed/abcdefgh_12",
        "abcdefgh_12",
    ],
)
def test_youtube_video_urls(url):
    assert video_id_from_url(url) == "abcdefgh_12"


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com.evil.example/watch?v=abcdefgh_12",
        "https://youtube.com@127.0.0.1/watch?v=abcdefgh_12",
        "http://127.0.0.1/watch?v=abcdefgh_12",
        "https://youtube.com:8443/watch?v=abcdefgh_12",
        "https://youtube.com/watch?v=abcdefgh_12&v=abcdefgh_34",
        "https://youtu.be/invalid",
        "https://youtube.com/playlist?list=abcdefgh_12",
        "https://you\ntube.com/watch?v=abcdefgh_12",
    ],
)
def test_invalid_urls_are_refused(url):
    with pytest.raises(CaptionImportError):
        video_id_from_url(url)


def test_caption_transport_bounds_and_no_redirect_or_cookie(monkeypatch):
    captured = []

    def transport(self, method, url, **kwargs):
        captured.append(kwargs)
        assert not self.cookies
        response = Response()
        response.status_code = 302
        response._content = b""
        response._content_consumed = True
        return response

    monkeypatch.setattr(Session, "request", transport)
    with BoundedCaptionSession() as session:
        session.cookies.set("auth", "test")
        with pytest.raises(CaptionImportError, match="redirect"):
            session.get("https://www.youtube.com/watch?v=abcdefgh_12")
        with pytest.raises(CaptionImportError, match="unsupported caption location"):
            session.get("https://127.0.0.1/api/timedtext")
    assert captured[0]["allow_redirects"] is False
    assert captured[0]["stream"] is True
    assert captured[0]["timeout"] == (5, 10)


def test_caption_bytes_and_deadline_are_bounded(monkeypatch):
    import app.youtube_captions as captions

    monkeypatch.setattr(captions, "MAX_RESPONSE_BYTES", 5)

    def transport(*args, **kwargs):
        response = Response()
        response.status_code = 200
        response._content = b"123456"
        response._content_consumed = True
        return response

    monkeypatch.setattr(Session, "request", transport)
    with BoundedCaptionSession() as session:
        with pytest.raises(CaptionImportError, match="too large"):
            session.get("https://www.youtube.com/api/timedtext?v=abcdefgh_12")
        session.deadline = 0
        with pytest.raises(CaptionImportError, match="timed out"):
            session.get("https://www.youtube.com/watch?v=abcdefgh_12")


def test_caption_language_is_explicit_and_manual_track_preferred(monkeypatch):
    calls = []

    def track(code, generated, text):
        return SimpleNamespace(
            language=code,
            language_code=code,
            is_generated=generated,
            fetch=lambda: calls.append(code) or [SimpleNamespace(text=text)],
        )

    monkeypatch.setattr(
        YouTubeTranscriptApi,
        "list",
        lambda self, video_id: [
            track("en", True, "generated"),
            track("hi", False, "हिंदी"),
            track("en-US", False, "English captions"),
        ],
    )
    result = fetch_captions("abcdefgh_12", "en")
    assert result.text == "English captions" and result.language_code == "en-US"
    assert not result.is_generated and calls == ["en-US"]
    with pytest.raises(CaptionImportError, match="No captions"):
        fetch_captions("abcdefgh_12", "ur")
    with pytest.raises(CaptionImportError, match="exceeds"):
        fetch_captions("abcdefgh_12", "en", max_chars=3)


def test_youtube_route_keeps_source_metadata_and_chunk_indexes(monkeypatch, tmp_path):
    import app.youtube_captions as captions

    monkeypatch.setattr(
        captions,
        "fetch_captions",
        lambda video_id, language, **kwargs: ImportedCaptions(
            video_id, "First caption.\nSecond caption.", language, "en-US", True
        ),
    )
    with transcript_client(tmp_path) as client:
        result = client.post(
            "/api/transcript/youtube",
            json={"url": "https://youtu.be/abcdefgh_12", "source_language": "en"},
        )
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["video_id"] == "abcdefgh_12" and body["source_language"] == "en"
        assert body["captions_generated"] is True
        assert [part["index"] for part in body["chunks"]] == list(range(len(body["chunks"])))
        assert (
            client.post(
                "/api/transcript/youtube",
                json={"url": "https://127.0.0.1/", "source_language": "en"},
            ).status_code
            == 422
        )


def test_youtube_blocked_route_reports_clear_error(monkeypatch, tmp_path):
    import app.youtube_captions as captions

    def fail(*args, **kwargs):
        raise CaptionImportError(
            "caption_access_blocked", "YouTube blocked captions. Paste the script instead."
        )

    monkeypatch.setattr(captions, "fetch_captions", fail)
    with transcript_client(tmp_path) as client:
        result = client.post(
            "/api/transcript/youtube", json={"url": "abcdefgh_12", "source_language": "ur"}
        )
    assert result.status_code == 409
    assert result.json()["code"] == "caption_access_blocked"
    assert "Paste" in result.json()["detail"]


class TranslationFake(_FakeTransliterator):
    async def convert_many(self, *, source_language=None, **kwargs):
        self.source_language = source_language
        return await super().convert_many(**kwargs)


@pytest.mark.parametrize(
    "language,target,source,output",
    [
        ("en", "roman", "The weather is pleasant today.", "Aaj mausam khushgawar hai."),
        ("en", "perso_arabic", "The weather is pleasant today.", "آج موسم خوشگوار ہے۔"),
        ("hi", "perso_arabic", "आज मौसम अच्छा है।", "آج موسم اچھا ہے۔"),
    ],
)
def test_explicit_language_selects_translation_and_persists_job(
    tmp_path, language, target, source, output
):
    fake = TranslationFake(text=output)
    client, _ = _client(tmp_path, fake)
    with client:
        response = client.post(
            "/api/text/transliterate",
            json={"text": source, "target": target, "source_language": language},
        )
        assert response.status_code == 202, response.text
        result = _poll(client, response.json()["id"])
    assert result["status"] == "succeeded", result
    assert fake.source_language == language
    assert result["result"]["operation"] == "translation"
    assert result["result"]["source_language"] == language
    assert result["result"]["items"][0]["text"] == output


def test_translation_echo_is_not_returned_as_a_draft(tmp_path):
    fake = TranslationFake(text="The weather is pleasant.")
    client, _ = _client(tmp_path, fake)
    with client:
        response = client.post(
            "/api/text/transliterate",
            json={"text": "The weather is pleasant.", "target": "roman", "source_language": "en"},
        )
        result = _poll(client, response.json()["id"])
    assert result["status"] == "failed" and result["result"] is None


def test_translation_prompt_and_legacy_latin_are_separate():
    prompt = build_translation_prompt("en", "roman")
    assert "Translate its meaning" in prompt and "not an English translation" in prompt
    assert "Hindi" in build_translation_prompt("hi", "perso_arabic")
    with pytest.raises(ValueError):
        build_translation_prompt("fr", "roman")


@pytest.mark.asyncio
async def test_old_worker_cannot_silently_transliterate_english():
    class Features:
        async def call(self, *args):
            return {"results": [{"text": "bad draft", "gen_time_sec": 1}]}

    with pytest.raises(TransliteratorUnavailableError):
        await RemoteTransliterator(Features()).convert_many(
            texts=["English source"], source_language="en", target_script="roman"
        )


def test_worker_translation_capability_and_language_reach_runtime(tmp_path):
    class Translator:
        async def convert_many(self, *, texts, source_language, target_script, **kwargs):
            assert source_language == "en" and target_script == "roman"
            return [TransliterateResult(text="Aaj mausam acha hai.", gen_time_sec=1)]

    worker = create_worker_app(
        settings=Settings(data_dir=tmp_path),
        scheduler=FakeScheduler(),
        token="test-token",
        transliterator=Translator(),
    )
    with TestClient(worker) as client:
        body = {
            "texts": ["The weather is good today."],
            "source_script": "latin",
            "target_script": "roman",
            "source_language": "en",
        }
        result = client.post(
            "/v1/transliterate", headers={"Authorization": "Bearer test-token"}, json=body
        )
        assert result.status_code == 200, result.text
        assert result.json()["source_language"] == "en"
        body["source_language"] = "fr"
        assert (
            client.post(
                "/v1/transliterate", headers={"Authorization": "Bearer test-token"}, json=body
            ).status_code
            == 422
        )
