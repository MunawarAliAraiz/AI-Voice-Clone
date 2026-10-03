"""Real HTTP failure categories stay useful without disclosing runtime details."""

from __future__ import annotations

import json
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.exceptions import (
    AnalyzerUnavailableError,
    GenerationError,
    ModelLoadError,
    TransliteratorUnavailableError,
)
from app.inference.analyzer_scheduler import AnalyzerScheduler
from app.inference.catalog import CATALOG
from app.inference.remote_features import RemoteTransliterator
from app.inference.remote_scheduler import RemoteScheduler, RemoteWorkerError
from app.inference.runtimes.voxcpm import VoxCPMBackend
from app.inference.scheduler import InferenceScheduler, SchedulerConfig
from app.inference.transliterator_scheduler import TransliteratorScheduler
from app.remote_worker.errors import WORKER_MESSAGES, safe_worker_problem
from app.remote_worker.main import create_worker_app
from tests.fakes import FakeScheduler


def test_pinned_voxcpm_constructor_contract(monkeypatch) -> None:
    calls = []

    class VoxCPM:
        def __init__(self, voxcpm_model_path, *, enable_denoiser, optimize):
            calls.append((voxcpm_model_path, enable_denoiser, optimize))
            self.tts_model = SimpleNamespace(sample_rate=48000)

        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            raise AssertionError("Must not resolve a moving repo default")

    def snapshot_download(*, repo_id, revision):
        assert (repo_id, revision) == ("org/model", "a" * 40)
        return "/cached/pinned-snapshot"

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot_download)
    )
    monkeypatch.setitem(sys.modules, "voxcpm", types.ModuleType("voxcpm"))
    monkeypatch.setitem(sys.modules, "voxcpm.core", SimpleNamespace(VoxCPM=VoxCPM))
    runtime = VoxCPMBackend()
    monkeypatch.setattr(runtime, "_warm", lambda: None)
    assert runtime.load("voxcpm2", "org/model", "a" * 40) >= 0
    assert calls == [("/cached/pinned-snapshot", False, False)]
    assert runtime.loaded_model_id == "voxcpm2"


def test_voxcpm_internal_type_error_is_not_retried_against_repo_main(monkeypatch) -> None:
    class VoxCPM:
        def __init__(self, **kwargs):
            raise TypeError("internal model construction failed")

        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            raise AssertionError("Must not mask the original failure")

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=lambda **kw: "/pin")
    )
    monkeypatch.setitem(sys.modules, "voxcpm", types.ModuleType("voxcpm"))
    monkeypatch.setitem(sys.modules, "voxcpm.core", SimpleNamespace(VoxCPM=VoxCPM))
    with pytest.raises(TypeError, match="internal model construction failed"):
        VoxCPMBackend().load("voxcpm2", "org/model", "a" * 40)


@pytest.mark.parametrize(
    "runtime_code,expected",
    [
        ("TypeError", "MODEL_LOAD_FAILED"),
        ("OutOfMemoryError", "GPU_MEMORY_EXHAUSTED"),
        ("LocalEntryNotFoundError", "MODEL_CACHE_MISSING"),
    ],
)
def test_scheduler_preserves_known_failure_category(runtime_code, expected) -> None:
    scheduler = InferenceScheduler(CATALOG, lambda _: None, SchedulerConfig())
    response = SimpleNamespace(error_code=runtime_code, error_message="private path/token/text")
    error = scheduler._error_from(CATALOG.get("voxcpm2"), response, during_load=True)
    assert isinstance(error, ModelLoadError)
    problem = safe_worker_problem(error)
    assert problem["code"] == expected
    assert problem["detail"] == WORKER_MESSAGES[expected]
    assert "private" not in json.dumps(problem)


def test_worker_synthesis_error_is_structured_and_safe(tmp_path: Path) -> None:
    class FailingScheduler(FakeScheduler):
        async def synthesize(self, request):
            raise ModelLoadError(
                request.model_id, "secret-token /workspace/private-file user script"
            )

    worker = create_worker_app(
        scheduler=FailingScheduler(), settings=Settings(data_dir=tmp_path), token="test-session"
    )
    with TestClient(worker) as client:
        response = client.post(
            "/v1/synthesize",
            headers={"Authorization": "Bearer test-session"},
            data={"request": json.dumps({"model_id": "voxcpm2", "text": "Hello"})},
            files={"reference_audio": ("reference.wav", b"sample", "audio/wav")},
        )
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "MODEL_LOAD_FAILED"
    assert "secret-token" not in response.text
    assert "private-file" not in response.text
    assert "user script" not in response.text


@pytest.mark.parametrize(
    "path,body,code",
    [
        ("/v1/analyze", {"language": "en", "sentences": ["Hello"]}, "ANALYZER_UNAVAILABLE"),
        (
            "/v1/transliterate",
            {
                "texts": ["aap kaise hain"],
                "source_script": "latin",
                "target_script": "perso_arabic",
            },
            "TRANSLITERATOR_UNAVAILABLE",
        ),
    ],
)
def test_worker_helper_error_is_structured_and_safe(tmp_path: Path, path, body, code) -> None:
    class FailingAnalyzer:
        async def classify(self, **kwargs):
            raise AnalyzerUnavailableError("secret-token /workspace/private-file")

    class FailingTransliterator:
        async def convert_many(self, **kwargs):
            raise TransliteratorUnavailableError("secret-token /workspace/private-file")

    worker = create_worker_app(
        scheduler=FakeScheduler(),
        settings=Settings(data_dir=tmp_path),
        token="test-session",
        analyzer=FailingAnalyzer(),
        transliterator=FailingTransliterator(),
    )
    with TestClient(worker) as client:
        response = client.post(path, headers={"Authorization": "Bearer test-session"}, json=body)
    assert response.status_code == 503
    assert response.json()["code"] == code
    assert response.json()["detail"] == WORKER_MESSAGES[code]
    assert "secret-token" not in response.text
    assert "private-file" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem",
    [
        {"code": "GPU_MEMORY_EXHAUSTED", "detail": "secret-token /private/path"},
        {"code": "MODEL_LOAD_FAILED", "detail": "script text"},
    ],
)
async def test_remote_adapter_uses_local_curated_message(problem) -> None:
    remote = RemoteScheduler(
        "https://pod.example",
        "test-session",
        CATALOG,
        transport=httpx.MockTransport(lambda _: httpx.Response(503, json=problem)),
    )
    try:
        with pytest.raises(RemoteWorkerError) as caught:
            await remote._response("POST", "/v1/synthesize")
        assert caught.value.detail == WORKER_MESSAGES[problem["code"]]
        assert caught.value.code == problem["code"]
    finally:
        await remote.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [{"code": ["invalid"]}, {"code": "UNKNOWN", "detail": "secret"}, ["secret"]]
)
async def test_remote_adapter_rejects_unrecognized_error_bodies(body) -> None:
    remote = RemoteScheduler(
        "https://pod.example",
        "test-session",
        CATALOG,
        transport=httpx.MockTransport(lambda _: httpx.Response(500, json=body)),
    )
    try:
        with pytest.raises(GenerationError) as caught:
            await remote._response("POST", "/v1/synthesize")
        assert str(caught.value) == "Runpod worker returned HTTP 500"
    finally:
        await remote.shutdown()


@pytest.mark.asyncio
async def test_script_helper_preserves_safe_worker_failure_reason() -> None:
    class Features:
        async def call(self, *args):
            raise RemoteWorkerError("MODEL_CACHE_MISSING", 409)

    with pytest.raises(TransliteratorUnavailableError) as caught:
        await RemoteTransliterator(Features()).convert_many(texts=["aap kaise hain"])
    assert str(caught.value) == WORKER_MESSAGES["MODEL_CACHE_MISSING"]


@pytest.mark.asyncio
@pytest.mark.parametrize("helper", ["analyzer", "transliterator"])
@pytest.mark.parametrize(
    "runtime_code,expected",
    [
        ("OutOfMemoryError", "GPU_MEMORY_EXHAUSTED"),
        ("LocalEntryNotFoundError", "MODEL_CACHE_MISSING"),
    ],
)
async def test_helper_load_keeps_memory_and_cache_category(helper, runtime_code, expected) -> None:
    class FailingWorker:
        async def call(self, *args, **kwargs):
            return SimpleNamespace(
                ok=False, error_code=runtime_code, error_message="secret-token /private-path"
            )

        async def kill(self):
            pass

    if helper == "analyzer":
        scheduler = AnalyzerScheduler(python_executable="unused-in-test")
    else:
        class Slot:
            @asynccontextmanager
            async def reserve_slot(self, reason):
                assert reason == "transliterate-load"
                yield

        scheduler = TransliteratorScheduler(
            python_executable="unused-in-test", inference_scheduler=Slot()
        )
    scheduler._worker = FailingWorker()
    try:
        with pytest.raises((AnalyzerUnavailableError, TransliteratorUnavailableError)) as caught:
            await scheduler._load()
        problem = safe_worker_problem(caught.value)
        assert problem["code"] == expected
        assert "secret-token" not in json.dumps(problem)
        assert "private-path" not in json.dumps(problem)
    finally:
        await scheduler.shutdown()


@pytest.mark.parametrize(
    "runtime_code,expected",
    [
        ("OutOfMemoryError", "GPU_MEMORY_EXHAUSTED"),
        ("OfflineModeIsEnabled", "MODEL_CACHE_MISSING"),
    ],
)
def test_wrapped_helper_failure_keeps_known_category(runtime_code, expected) -> None:
    inner = GenerationError("gemma", "secret-token").with_worker_error(runtime_code)
    outer = TransliteratorUnavailableError("Private runtime detail")
    outer.__cause__ = inner
    assert safe_worker_problem(outer)["code"] == expected


def test_unknown_or_cyclic_exception_chain_never_echoes_private_text() -> None:
    outer = TransliteratorUnavailableError("Secret-token says OutOfMemoryError in text")
    inner = ValueError("private-path")
    outer.__cause__ = inner
    inner.__cause__ = outer
    outer.extensions["worker_error_code"] = ["malformed"]
    problem = safe_worker_problem(outer)
    assert problem["code"] == "TRANSLITERATOR_UNAVAILABLE"
    assert "Secret-token" not in json.dumps(problem)
    assert "private-path" not in json.dumps(problem)
