"""Model-test API contracts using the real queue and offline inference doubles.

These prove routing, persistence and safe defaults, not GPU/model quality.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app import model_test_voice
from app.api.routers import model_tests
from app.config import Settings
from app.exceptions import GenerationError
from app.main import create_app
from app.remote_worker.model_install import REQUIRED_MODEL_IDS
from app.runpod.worker_pair import WorkerPairStore
from tests.fakes import FakeAnalyzerScheduler, FakeScheduler
from tests.test_api_transliterate import _FakeTransliterator


class _Converter(_FakeTransliterator):
    async def warm(self):
        return 0.0


def _wav() -> bytes:
    signal = (0.1 * np.sin(np.arange(32000) * 2 * np.pi * 220 / 16000)).astype(np.float32)
    output = io.BytesIO()
    sf.write(output, signal, 16000, format="WAV")
    return output.getvalue()


def _client(tmp_path: Path):
    scheduler = FakeScheduler()
    analyzer = FakeAnalyzerScheduler()
    converter = _Converter(text="السلام علیکم۔ یہ ایک چھوٹا تجربہ ہے۔")
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings=settings, scheduler=scheduler, analyzer=analyzer)
    app.state.transliterator = converter
    return TestClient(app), settings, scheduler, analyzer, converter


def _enroll(client: TestClient) -> int:
    result = client.post("/api/voices", files={"file": ("reference.wav", _wav(), "audio/wav")},
                         data={"name": "Selected reference", "language": "en", "consent": "true"})
    assert result.status_code == 201, result.text
    return result.json()["id"]


def _poll(client: TestClient, job_id: int) -> dict:
    for _ in range(200):
        result = client.get(f"/api/jobs/{job_id}")
        assert result.status_code == 200, result.text
        body = result.json()
        if body["status"] in {"succeeded", "failed", "cancelled"}:
            return body
        time.sleep(0.01)
    raise AssertionError(f"Model-test job {job_id} did not settle")


@pytest.mark.parametrize("model_id", REQUIRED_MODEL_IDS)
def test_each_model_uses_normal_queue_and_records_its_result(tmp_path: Path, model_id: str):
    client, settings, scheduler, analyzer, converter = _client(tmp_path)
    with client as c:
        profile_id = _enroll(c)
        response = c.post(f"/api/model-tests/{model_id}", json={"profile_id": profile_id})
        assert response.status_code == 202, response.text
        queued = response.json()
        done = _poll(c, queued["id"])
        assert done["status"] == "succeeded", done
        status = c.get("/api/model-tests/status")
        assert status.status_code == 200, status.text
        row = next(item for item in status.json()["models"] if item["id"] == model_id)
        assert row["last_test"]["job_id"] == queued["id"]
        assert row["last_test"]["status"] == "succeeded"
        saved = json.loads((settings.data_dir / "model-tests.json").read_text())
        assert saved[model_id] == queued["id"]

    if model_id == "qwen2.5-3b-instruct-analyzer":
        assert queued["kind"] == "analyze_llm" and queued["route"] is None
        assert analyzer.calls == [("en", ("Hello, this is a short test.",))]
        assert done["result"]["rows"]
        assert scheduler.requests == [] and converter.calls == []
    elif model_id == "gemma-4-31b-it-transliterator":
        assert queued["kind"] == "transliterate" and queued["route"] is None
        assert converter.calls[0]["source_script"] == "latin"
        assert converter.calls[0]["target_script"] == "perso_arabic"
        assert done["result"]["items"][0]["text"].startswith("السلام")
        assert scheduler.requests == [] and analyzer.calls == []
    else:
        assert queued["kind"] == "synthesize"
        assert queued["route"]["model_id"] == model_id
        assert len(scheduler.requests) == 1
        request = scheduler.requests[0]
        assert request.model_id == model_id
        prefix = "السلام" if model_id == "omnivoice_urdu" else "Hello"
        assert request.text.startswith(prefix)
        assert analyzer.calls == [] and converter.calls == []


@pytest.mark.parametrize("model_id", ["voxcpm2", "chatterbox_ml_v3", "omnivoice_urdu"])
def test_no_voice_creates_labelled_sample_once_and_reuses_it(
    tmp_path: Path, monkeypatch, model_id: str,
):
    created: list[Path] = []

    def create_sample(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_wav())
        created.append(path)
        return 2.0, 16000, -20.0

    monkeypatch.setattr(model_test_voice, "_create", create_sample)
    client, _, scheduler, _, _ = _client(tmp_path)
    with client as c:
        for _ in range(2):
            result = c.post(f"/api/model-tests/{model_id}", json={})
            assert result.status_code == 202, result.text
            assert _poll(c, result.json()["id"])["status"] == "succeeded"
        voices = c.get("/api/voices").json()["profiles"]
        assert len(voices) == 1
        assert voices[0]["name"] == model_test_voice.DEMO_NAME
        assert voices[0]["transcript"] == model_test_voice.DEMO_TEXT
    assert len(created) == 1
    audio, rate = sf.read(created[0])
    assert rate == 16000 and np.isfinite(audio).all() and np.max(np.abs(audio)) > 0
    assert len(scheduler.requests) == 2
    assert scheduler.requests[0].reference_audio == scheduler.requests[1].reference_audio


def test_default_reference_uses_existing_voice_without_creating_demo(tmp_path: Path, monkeypatch):
    def unexpected_create(_path):
        raise AssertionError("An existing reference must be reused")

    monkeypatch.setattr(model_test_voice, "_create", unexpected_create)
    client, _, scheduler, _, _ = _client(tmp_path)
    with client as c:
        profile_id = _enroll(c)
        result = c.post("/api/model-tests/voxcpm2", json={})
        assert result.status_code == 202, result.text
        done = _poll(c, result.json()["id"])
        assert done["status"] == "succeeded"
        assert done["profile_id"] == profile_id
    assert len(scheduler.requests) == 1


def test_unknown_model_is_rejected_before_creating_sample_or_job(tmp_path: Path, monkeypatch):
    def unexpected_create(_path):
        raise AssertionError("Unknown models must not create a sample")

    monkeypatch.setattr(model_test_voice, "_create", unexpected_create)
    client, _, scheduler, analyzer, converter = _client(tmp_path)
    with client as c:
        result = c.post("/api/model-tests/not-a-model", json={})
        assert result.status_code == 404, result.text
        assert result.json()["code"] == "MODEL_NOT_FOUND"
        assert c.get("/api/jobs").json()["total"] == 0
        assert c.get("/api/voices").json()["profiles"] == []
    assert scheduler.requests == [] and analyzer.calls == [] and converter.calls == []


def test_explicit_missing_profile_is_rejected_without_sample(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        model_test_voice, "_create", lambda _: pytest.fail("No demo for explicit missing profile"),
    )
    client, _, scheduler, _, _ = _client(tmp_path)
    with client as c:
        result = c.post("/api/model-tests/voxcpm2", json={"profile_id": 9999})
        assert result.status_code == 404, result.text
        assert result.json()["code"] == "PROFILE_NOT_FOUND"
        assert c.get("/api/jobs").json()["total"] == 0
    assert scheduler.requests == []


def test_failed_test_is_reported_as_failure_instead_of_ready(tmp_path: Path):
    client, _, scheduler, _, _ = _client(tmp_path)
    scheduler.raise_on_synthesize = GenerationError("voxcpm2", "Test runtime failed")
    with client as c:
        profile_id = _enroll(c)
        response = c.post("/api/model-tests/voxcpm2", json={"profile_id": profile_id})
        assert response.status_code == 202, response.text
        done = _poll(c, response.json()["id"])
        assert done["status"] == "failed" and done["result"] is None
        status = c.get("/api/model-tests/status").json()
        row = next(item for item in status["models"] if item["id"] == "voxcpm2")
        assert row["last_test"]["status"] == "failed"
        assert row["last_test"]["error_code"] == "GENERATION_FAILED"
        assert "Test runtime failed" in row["last_test"]["error_detail"]
        assert row["loaded"] is False


@pytest.mark.parametrize("worker_error", [False, True])
def test_status_only_reads_existing_session_and_distinguishes_unknown_loading(
    tmp_path: Path, monkeypatch, worker_error: bool,
):
    calls = []

    class ReadOnlyCloud:
        def read(self):
            return {"compute": {"kind": "generation", "pod_id": "existingpod"}}

        def is_ready(self, state):
            return True

        async def _worker_call(self, pair, method, path):
            calls.append((pair.pod_id, method, path))
            if worker_error:
                raise TimeoutError("Worker status unavailable")
            return {"loaded_model_ids": ["voxcpm2"]}

    monkeypatch.setattr(model_tests, "controller", lambda _: ReadOnlyCloud())
    monkeypatch.setattr(WorkerPairStore, "get", lambda _: SimpleNamespace(pod_id="existingpod"))
    client, settings, scheduler, analyzer, converter = _client(tmp_path)
    with client as c:
        # Set after startup: no real desktop startup or provider client is used.
        settings.desktop_static_dir = tmp_path
        result = c.get("/api/model-tests/status")
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["session_active"] is True
        assert body["worker_status_available"] is not worker_error
        assert len(body["models"]) == len(REQUIRED_MODEL_IDS)
        assert all(item["files_checked"] and item["last_test"] is None for item in body["models"])
        expected_loaded = set() if worker_error else {"voxcpm2"}
        assert {item["id"] for item in body["models"] if item["loaded"]} == expected_loaded
        if worker_error:
            assert all(item["loaded"] is None for item in body["models"])
    assert calls == [("existingpod", "GET", "/v1/activity")]
    assert scheduler.requests == [] and analyzer.calls == [] and converter.calls == []
    assert scheduler.warmed == []



@pytest.mark.parametrize("model_id", REQUIRED_MODEL_IDS)
def test_desktop_spending_block_rejects_before_sample_or_enqueue(tmp_path, monkeypatch, model_id):
    from app.runpod.client import POD_START_BLOCKED_REASON

    def unexpected_sample(*args):
        raise AssertionError("A blocked request must not create a reference voice")

    monkeypatch.setattr(model_test_voice, "_create", unexpected_sample)
    client, settings, scheduler, analyzer, converter = _client(tmp_path)
    with client as c:
        settings.desktop_static_dir = tmp_path
        response = c.post(f"/api/model-tests/{model_id}", json={})
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == POD_START_BLOCKED_REASON
        activity = c.get("/api/jobs/activity")
        assert activity.status_code == 200, activity.text
        assert not activity.json()["items"]
        assert not (tmp_path / "model-tests.json").exists()
    assert scheduler.requests == [] and analyzer.calls == [] and converter.calls == []
