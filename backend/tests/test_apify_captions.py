"""No network/paid runs. Exercise provider admission, recovery and actual-track contracts."""

from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.apify_captions import BUILD_ID, RUN_CAP_USD, ApifyCaptions, ApifyKeyStore, parse_output
from app.config import Settings
from app.main import create_app
from app.youtube_captions import CaptionImportError, fetch_captions
from tests.fakes import FakeScheduler

VIDEO = "hoMYed4N4S4"


def output(code="hi", *, tracks=None):
    return {
        "videoId": VIDEO,
        "text": "A short caption.",
        "languageCode": code,
        "isGenerated": True,
        "segments": [{"text": "A short caption.", "start": 0, "duration": 2}],
        "availableLanguages": tracks
        or [{"language": code, "languageCode": code, "isGenerated": True}],
    }


def provider(output_items=None, *, fail_post=False, missing_cap=False, status="SUCCEEDED"):
    calls = []
    count = 0

    def handle(request):
        nonlocal count
        calls.append((request.method, request.url.path, dict(request.url.params)))
        path = request.url.path
        if path.endswith("/users/me"):
            return httpx.Response(
                200, json={"data": {"plan": {"tier": "FREE", "monthlyUsageCreditsUsd": 5}}}
            )
        if path.endswith("/users/me/limits"):
            return httpx.Response(200, json={"data": {"current": {"monthlyUsageUsd": 0}}})
        if path.endswith("/runs") and request.method == "POST":
            if fail_post:
                raise httpx.ReadTimeout("contains-private-provider-detail")
            count += 1
            return httpx.Response(
                201,
                json={
                    "data": {
                        "id": f"run{count}",
                        "buildId": BUILD_ID,
                        "options": {
                            "timeoutSecs": 120,
                            "maxTotalChargeUsd": None if missing_cap else RUN_CAP_USD,
                        },
                    }
                },
            )
        if path.endswith("/abort"):
            return httpx.Response(200, json={"data": {"status": "ABORTED"}})
        if "/actor-runs/" in path:
            index = path.rsplit("run", 1)[1]
            return httpx.Response(
                200, json={"data": {"status": status, "defaultDatasetId": f"dataset{index}"}}
            )
        if "/datasets/" in path:
            index = int(path.split("/")[-2].removeprefix("dataset")) - 1
            return httpx.Response(200, json=[(output_items or [output()])[index]])
        raise AssertionError("Unexpected API request")

    return httpx.MockTransport(handle), calls


def service(tmp_path, monkeypatch, **kwargs):
    transport, calls = provider(**kwargs)
    monkeypatch.setattr(ApifyKeyStore, "get", lambda self: "private-token-not-returned")
    return ApifyCaptions(tmp_path, transport=transport), calls


async def finish(service, value):
    await service.tasks[value["id"]]
    return await service.status(value["id"])


@pytest.mark.asyncio
async def test_native_hindi_real_tracks_no_secret_and_bounded_request(tmp_path, monkeypatch):
    subject, calls = service(tmp_path, monkeypatch)
    result = await finish(subject, await subject.start(VIDEO, None, max_chars=1000))
    assert result["phase"] == "ready"
    assert result["captions"]["language"] == "hi"
    assert [t["language_code"] for t in result["captions"]["available_tracks"]] == ["hi"]
    creations = [r for r in calls if r[0] == "POST" and r[1].endswith("/runs")]
    assert len(creations) == 1
    assert creations[0][2]["build"] == "1.0.98"
    assert float(creations[0][2]["maxTotalChargeUsd"]) == 0.014
    assert creations[0][2]["timeout"] == "120"
    assert creations[0][2]["restartOnError"] == "false"
    assert "private-token" not in json.dumps(result)
    assert "private-token" not in "".join(p.read_text() for p in subject.root.glob("*.json"))
    assert "run_id" not in json.dumps(result)


@pytest.mark.asyncio
async def test_auto_fallback_only_two_runs_with_total_actor_caps_under_quote(tmp_path, monkeypatch):
    tracks = [
        {"language": "Spanish", "languageCode": "es", "isGenerated": True},
        {"language": "Hindi", "languageCode": "hi", "isGenerated": False},
        {"language": "English", "languageCode": "en-US", "isGenerated": True},
    ]
    hindi = output("hi", tracks=tracks)
    hindi["isGenerated"] = False
    subject, calls = service(
        tmp_path, monkeypatch, output_items=[output("es", tracks=tracks), hindi]
    )
    result = await finish(subject, await subject.start(VIDEO, None, max_chars=1000))
    assert result["phase"] == "ready" and result["captions"]["language"] == "hi"
    creations = [r for r in calls if r[0] == "POST" and r[1].endswith("/runs")]
    assert len(creations) == 2
    assert sum(float(r[2]["maxTotalChargeUsd"]) for r in creations) < 0.03
    assert all(t["language_code"] != "es" for t in result["captions"]["available_tracks"])


@pytest.mark.asyncio
async def test_unsupported_only_does_not_create_second_run(tmp_path, monkeypatch):
    subject, calls = service(tmp_path, monkeypatch, output_items=[output("es")])
    result = await finish(subject, await subject.start(VIDEO, None, max_chars=1000))
    assert result["phase"] == "failed" and result["error_code"] == "captions_unsupported"
    assert len([r for r in calls if r[0] == "POST" and r[1].endswith("/runs")]) == 1


@pytest.mark.asyncio
async def test_valid_cache_reused_without_any_http_but_corrupt_and_expired_cache_rejected(
    tmp_path, monkeypatch
):
    subject, calls = service(tmp_path, monkeypatch)
    result = await finish(subject, await subject.start(VIDEO, None, max_chars=1000))
    assert result["phase"] == "ready"
    previous_calls = len(calls)
    cached = await subject.start(VIDEO, None, max_chars=1000)
    assert cached["phase"] == "ready" and cached["cached"] and len(calls) == previous_calls
    cache_name = subject._cache_name(VIDEO, None, "apify")
    record = subject._read(cache_name)
    record["captions"]["language"] = "es"
    subject._write(cache_name, record)
    assert subject._cached(VIDEO, None, "apify", 1000) is None
    record["captions"]["language"] = "hi"
    record["saved_at"] = time.time() - 25 * 3600
    subject._write(cache_name, record)
    assert subject._cached(VIDEO, None, "apify", 1000) is None
    record["saved_at"] = "broken"
    subject._write(cache_name, record)
    assert subject._cached(VIDEO, None, "apify", 1000) is None


@pytest.mark.asyncio
async def test_uncertain_post_never_replayed_even_after_service_restart(tmp_path, monkeypatch):
    subject, calls = service(tmp_path, monkeypatch, fail_post=True)
    result = await finish(subject, await subject.start(VIDEO, None, max_chars=1000))
    assert result["error_code"] == "caption_start_uncertain"
    assert "private-provider" not in result["detail"]
    restarted = ApifyCaptions(tmp_path, transport=subject.transport)
    with pytest.raises(CaptionImportError, match="not confirmed"):
        await restarted.start(VIDEO, None, max_chars=1000)
    assert len([r for r in calls if r[0] == "POST"]) == 1


@pytest.mark.asyncio
async def test_provider_did_not_accept_cost_cap_aborted_and_no_result(tmp_path, monkeypatch):
    subject, calls = service(tmp_path, monkeypatch, missing_cap=True)
    result = await finish(subject, await subject.start(VIDEO, None, max_chars=1000))
    assert result["error_code"] == "caption_limits_failed"
    assert any(path.endswith("/abort") for _, path, _ in calls)
    assert not result["captions"]


@pytest.mark.asyncio
async def test_saved_known_run_resumes_without_creating_actor(tmp_path, monkeypatch):
    subject, calls = service(tmp_path, monkeypatch)
    identity = "a" * 32
    job = {
        "id": identity,
        "video_id": VIDEO,
        "language": None,
        "max_chars": 1000,
        "created_at": time.time(),
        "provider": "apify",
        "phase": "fetching",
        "detail": "Fetching captions…",
        "captions": None,
        "cached": False,
        "maximum_import_usd": 0.03,
        "runs": [{"attempted_at": time.time(), "run_id": "run1"}],
    }
    subject._write(f"import-{identity}.json", job)
    await subject.status(identity)
    result = await finish(subject, job)
    assert result["phase"] == "ready"
    assert not any(method == "POST" for method, _, _ in calls)


@pytest.mark.asyncio
async def test_cancel_known_run_sends_abort_no_success(tmp_path, monkeypatch):
    subject, calls = service(tmp_path, monkeypatch, status="RUNNING")
    value = await subject.start(VIDEO, None, max_chars=1000)
    for _ in range(30):
        state = subject._read(f"import-{value['id']}.json")
        if state["runs"] and state["runs"][0]["run_id"]:
            break
        await asyncio.sleep(0.01)
    await subject.cancel(value["id"])
    result = await finish(subject, value)
    assert result["phase"] == "cancelled" and result["captions"] is None
    assert any(path.endswith("/abort") for _, path, _ in calls)


@pytest.mark.asyncio
async def test_expired_known_run_aborted_without_creation(tmp_path, monkeypatch):
    subject, calls = service(tmp_path, monkeypatch, status="RUNNING")
    identity = "b" * 32
    job = {
        "id": identity,
        "video_id": VIDEO,
        "language": None,
        "max_chars": 1000,
        "created_at": time.time() - 200,
        "provider": "apify",
        "phase": "fetching",
        "detail": "Fetching",
        "captions": None,
        "cached": False,
        "maximum_import_usd": 0.03,
        "runs": [{"attempted_at": time.time() - 200, "run_id": "run1"}],
    }
    subject._write(f"import-{identity}.json", job)
    await subject.status(identity)
    result = await finish(subject, job)
    assert result["error_code"] == "caption_timeout"
    assert len([r for r in calls if r[0] == "POST"]) == 1
    assert calls[-1][1].endswith("/abort")


@pytest.mark.parametrize(
    "change",
    [
        {"videoId": "wrong"},
        {"availableLanguages": []},
        {"text": ""},
        {"segments": [{"text": "x", "start": float("nan"), "duration": 1}]},
        {"segments": [{"text": "x", "start": True, "duration": 1}]},
        {
            "segments": [
                {"text": "x", "start": 2, "duration": 1},
                {"text": "y", "start": 1, "duration": 1},
            ]
        },
        {"text": "x" * 101},
    ],
)
def test_remote_metadata_and_timings_are_validated(change):
    with pytest.raises(CaptionImportError):
        parse_output({**output(), **change}, VIDEO, 100)


def test_local_auto_supported_tracks_and_unsupported_only(monkeypatch):
    from youtube_transcript_api import YouTubeTranscriptApi

    def track(code, generated):
        return SimpleNamespace(
            language=code,
            language_code=code,
            is_generated=generated,
            fetch=lambda: [SimpleNamespace(text="Caption text")],
        )

    monkeypatch.setattr(
        YouTubeTranscriptApi, "list", lambda self, video_id: [track("hi", True), track("es", False)]
    )
    result = fetch_captions(VIDEO)
    assert result.language == "hi" and [t.language_code for t in result.available_tracks] == ["hi"]
    monkeypatch.setattr(YouTubeTranscriptApi, "list", lambda self, video_id: [track("es", False)])
    with pytest.raises(CaptionImportError) as error:
        fetch_captions(VIDEO)
    assert error.value.code == "captions_unsupported"


@pytest.mark.asyncio
async def test_oversized_provider_response_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr("app.apify_captions.MAX_BYTES", 50)
    subject = ApifyCaptions(
        tmp_path,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 51)),
    )
    with pytest.raises(CaptionImportError) as error:
        await subject.request("private-token", "GET", "/users/me")
    assert error.value.code == "captions_too_large"


def test_dpapi_storage_roundtrip_encrypted_only(tmp_path, monkeypatch):
    calls = []

    def crypt(value, *, protect):
        calls.append(protect)
        return b"ENCRYPTED:" + value if protect else value.removeprefix(b"ENCRYPTED:")

    monkeypatch.setattr("app.apify_captions._crypt", crypt)
    store = ApifyKeyStore(tmp_path)
    store.set("example-key")
    assert store.path.read_bytes().startswith(b"ENCRYPTED:")
    assert store.get() == "example-key" and calls == [True, False]
    store.clear()
    assert store.get() is None


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("VCS_TEST_NATIVE_DPAPI") != "1",
    reason="Opt-in Windows user-profile DPAPI qualification",
)
def test_native_dpapi_test_token_not_plaintext_on_disk(tmp_path):
    store = ApifyKeyStore(tmp_path)
    value = "disposable-test-token-not-a-real-credential"
    store.set(value)
    assert value.encode() not in store.path.read_bytes()
    assert store.get() == value
    store.clear()


@pytest.mark.asyncio
async def test_free_credit_exhaustion_never_starts_actor(tmp_path, monkeypatch):
    subject, calls = service(tmp_path, monkeypatch)

    async def no_credit(key):
        return {"free_plan": True, "included_credit_remaining_usd": 0}

    monkeypatch.setattr(subject, "account", no_credit)
    result = await finish(subject, await subject.start(VIDEO, None, max_chars=1000))
    assert result["error_code"] == "apify_credit_required"
    assert not calls


@pytest.mark.asyncio
async def test_active_same_import_coalesces_without_duplicate_creation(tmp_path, monkeypatch):
    subject, calls = service(tmp_path, monkeypatch, status="RUNNING")
    first = await subject.start(VIDEO, None, max_chars=1000)
    await asyncio.sleep(0)
    second = await subject.start(VIDEO, None, max_chars=1000)
    assert first["id"] == second["id"]
    await subject.cancel(first["id"])
    await finish(subject, first)
    assert len([r for r in calls if r[0] == "POST" and r[1].endswith("/runs")]) <= 1


def test_async_api_result_has_actual_tracks_no_key_or_runid(tmp_path, monkeypatch):
    from app.apify_captions import caption_service

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html></html>")
    subject = caption_service(tmp_path)
    subject.transport, _ = provider()
    monkeypatch.setattr(ApifyKeyStore, "get", lambda self: "private-token-not-returned")
    app = create_app(
        scheduler=FakeScheduler(), settings=Settings(data_dir=tmp_path, desktop_static_dir=static)
    )
    with TestClient(app) as client:
        response = client.post("/api/transcript/youtube/imports", json={"url": VIDEO})
        assert response.status_code == 202
        identity = response.json()["id"]
        for _ in range(40):
            result = client.get(f"/api/transcript/youtube/imports/{identity}").json()
            if result["phase"] in {"ready", "failed"}:
                break
            time.sleep(0.01)
        assert result["phase"] == "ready", result
        assert result["result"]["source_language"] == "hi"
        assert len(result["result"]["available_caption_tracks"]) == 1
        assert "private-token" not in json.dumps(result) and "run_id" not in json.dumps(result)
        assert client.get("/api/transcript/youtube/imports/not-an-id").status_code == 404
        bad_key = client.put("/api/transcript/apify", json={"api_key": "short"})
        assert bad_key.status_code == 400 and "short" not in bad_key.text
