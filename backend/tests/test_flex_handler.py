"""Offline bridge contract checks. No provider, CUDA, downloads, or cloned voices."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import struct
import wave
from uuid import uuid4

import pytest

from app.config import Settings
from app.inference.progress import emit_progress
from app.inference.protocol import AnalyzeResult, SynthResult, TransliterateResult
from app.remote_worker.flex_handler import FlexHandler
from app.remote_worker.model_install import REQUIRED_MODEL_IDS
from app.runpod.flex_protocol import MAX_AUDIO_BYTES, FlexOutput
from tests.fakes import FakeScheduler


def pcm() -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(struct.pack("<h", 1000) * 1600)
    return stream.getvalue()


def job(operation="synthesize", *, identity=None, payload=None):
    default = {"model_id": "voxcpm2", "text": "Private test script"}
    return {
        "id": "provider-job",
        "input": {
            "operation_id": str(identity or uuid4()), "operation": operation,
            "payload": default if payload is None and operation == "synthesize" else payload or {},
            **({"reference_audio_b64": base64.b64encode(pcm()).decode()}
               if operation == "synthesize" else {}),
        },
    }


class AudioScheduler(FakeScheduler):
    def __init__(self):
        super().__init__()
        self.in_flight = self.peak = 0
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.hold = False

    async def synthesize(self, request):
        self.requests.append(request)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if self.hold:
                self.entered.set()
                await self.release.wait()
            request.output_path.write_bytes(pcm())
            return SynthResult(request.output_path, 0.1, 0.01, 16000, request.model_id, 0.02)
        finally:
            self.in_flight -= 1


class Analyzer:
    calls = 0

    async def classify(self, **_kwargs):
        self.calls += 1
        await emit_progress("analyzing", "qwen2.5-3b-instruct-analyzer")
        return AnalyzeResult(rows=(), title="Demo", gen_time_sec=0.1)


class Converter:
    calls = 0

    async def convert_many(self, **_kwargs):
        self.calls += 1
        await emit_progress("converting", "gemma-4-31b-it-transliterator")
        return [TransliterateResult("Ø³Ù„Ø§Ù…", 0.1)]


def handler(tmp_path, scheduler=None, **kwargs):
    return FlexHandler(
        volume_namespace=tmp_path / "durable", scheduler=scheduler or AudioScheduler(),
        settings=Settings(data_dir=tmp_path / "ephemeral", allow_fake_runtime=True),
        analyzer=Analyzer(), transliterator=Converter(), poll_interval=0.005, **kwargs,
    )


async def test_wav_response_ephemeral_cleanup_and_marker_privacy(tmp_path):
    scheduler = AudioScheduler()
    request = job()
    async with handler(tmp_path, scheduler) as bridge:
        response = await bridge.handle(request)
        FlexOutput.model_validate(response)
        assert response["status_code"] == 200
        assert base64.b64decode(response["audio_b64"]) == pcm()
        assert response["headers"]["x-request-id"] == request["input"]["operation_id"]
        assert not scheduler.requests[0].reference_audio.exists()
        assert not scheduler.requests[0].output_path.exists()
    markers = list((tmp_path / "durable" / "flex-operations-v1").glob("*.json"))
    record = json.loads(markers[0].read_text())
    assert set(record) == {"operation", "fingerprint", "status"}
    assert record["status"] == "completed"
    assert "Private" not in markers[0].read_text()
    assert request["input"]["reference_audio_b64"] not in markers[0].read_text()


@pytest.mark.parametrize("operation,payload", [
    ("analyze", {"language": "en", "sentences": ["Hello"]}),
    ("transliterate", {"texts": ["salam"], "source_script": "latin",
                       "target_script": "perso_arabic"}),
    ("warm", {"model_id": "voxcpm2"}),
    ("models", {}),
    ("setup_status", {}),
])
async def test_allowlisted_helpers_and_status(tmp_path, operation, payload):
    async with handler(tmp_path) as bridge:
        response = await bridge.handle(job(operation, payload=payload))
        FlexOutput.model_validate(response)
        assert response["status_code"] == 200
        assert "audio_b64" not in response
        if operation == "warm":
            assert bridge.app.state.scheduler.warmed == ["voxcpm2"]
        if operation == "models":
            assert all("detail" not in row["install"] for row in response["body"]["models"])


@pytest.mark.parametrize("mutation", [
    lambda value: value["input"].update(operation="../../private"),
    lambda value: value["input"]["payload"].update(output_path="/private/path"),
    lambda value: value["input"]["payload"].update(params={"url": "https://private"}),
    lambda value: value["input"].update(reference_audio_b64="bad encoded token"),
    lambda value: value["input"].update(token="private-token"),
    lambda value: value["input"].update(operation_id="/private/path"),
    lambda value: value["input"]["payload"].update(sample_rate=float("nan")),
    lambda value: value.update(input=None),
])
async def test_malformed_input_never_dispatches_or_echoes(tmp_path, mutation):
    request = job()
    mutation(request)
    scheduler = AudioScheduler()
    async with handler(tmp_path, scheduler) as bridge:
        response = await bridge.handle(request)
        assert response["body"]["code"] == "INVALID_INPUT"
        serialized = json.dumps(response)
        assert "private" not in serialized and "token" not in serialized
        assert not scheduler.requests
    assert not (tmp_path / "durable").exists()


async def test_oversized_reference_never_dispatches(tmp_path):
    request = job()
    request["input"]["reference_audio_b64"] = base64.b64encode(
        b"a" * (MAX_AUDIO_BYTES + 1),
    ).decode()
    scheduler = AudioScheduler()
    async with handler(tmp_path, scheduler) as bridge:
        result = await bridge.handle(request)
        assert result["status_code"] == 422
        assert not scheduler.requests


async def test_concurrent_duplicate_and_serial_different_jobs(tmp_path):
    scheduler = AudioScheduler()
    scheduler.hold = True
    request = job()
    async with handler(tmp_path, scheduler) as bridge:
        first = asyncio.create_task(bridge.handle(request))
        await asyncio.wait_for(scheduler.entered.wait(), 2)
        duplicate = await bridge.handle(request)
        assert duplicate["body"]["code"] == "PRIOR_RESULT_UNAVAILABLE"
        second = asyncio.create_task(bridge.handle(job()))
        await asyncio.sleep(0.03)
        assert len(scheduler.requests) == 1
        scheduler.release.set()
        assert (await first)["status_code"] == 200
        assert (await second)["status_code"] == 200
        assert scheduler.peak == 1


async def test_restart_never_repeats_completed_or_interrupted_operation(tmp_path):
    request = job()
    async with handler(tmp_path) as bridge:
        assert (await bridge.handle(request))["status_code"] == 200
    scheduler = AudioScheduler()
    async with handler(tmp_path, scheduler) as bridge:
        assert (await bridge.handle(request))["body"]["code"] == "PRIOR_RESULT_UNAVAILABLE"
        changed = job(identity=request["input"]["operation_id"], payload={
            "model_id": "voxcpm2", "text": "Changed private text",
        })
        assert (await bridge.handle(changed))["body"]["code"] == "OPERATION_CONFLICT"
        assert not scheduler.requests

    interrupted = job()
    scheduler = AudioScheduler()
    scheduler.hold = True
    async with handler(tmp_path, scheduler) as bridge:
        task = asyncio.create_task(bridge.handle(interrupted))
        await asyncio.wait_for(scheduler.entered.wait(), 2)
        paths = scheduler.requests[0]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not paths.reference_audio.exists() and not paths.output_path.exists()
    async with handler(tmp_path) as bridge:
        assert (await bridge.handle(interrupted))["body"]["code"] == "PRIOR_RESULT_UNAVAILABLE"


async def test_error_text_and_invalid_audio_are_sanitized(tmp_path):
    class Broken(AudioScheduler):
        async def synthesize(self, request):
            raise RuntimeError("private-token /private/file user-script")

    async with handler(tmp_path, Broken()) as bridge:
        result = await bridge.handle(job())
        assert result["status_code"] == 500
        assert "private" not in json.dumps(result)

    async with handler(tmp_path, FakeScheduler()) as bridge:
        result = await bridge.handle(job())
        assert result["body"]["code"] == "INVALID_RESULT"
        assert "FAKE" not in json.dumps(result)


async def test_exact_activity_stages_and_callback_failure_cleanup(tmp_path):
    class Stages(AudioScheduler):
        async def synthesize(self, request):
            await emit_progress("loading_model", request.model_id)
            await asyncio.sleep(0.025)
            await emit_progress("generating", request.model_id)
            await asyncio.sleep(0.025)
            return await super().synthesize(request)

    updates = []

    async def callback(update):
        updates.append(update)

    async with handler(tmp_path, Stages(), progress_callback=callback) as bridge:
        request = job()
        assert (await bridge.handle(request))["status_code"] == 200
        assert [row["stage"] for row in updates] == ["loading_model", "generating"]
        assert all(row["operation_id"] == request["input"]["operation_id"] for row in updates)
        assert all(row["model_id"] == "voxcpm2" for row in updates)
        assert all(set(row) == {
            "protocol_version", "operation_id", "stage", "model_id",
        } for row in updates)
        assert bridge.app.state.activity.snapshot() == []
        snapshot = list(updates)
        await asyncio.sleep(0.03)
        assert snapshot == updates

    failures = []

    async def bad_callback(update):
        failures.append(update)
        raise RuntimeError("private-token")

    scheduler = Stages()
    async with handler(tmp_path, scheduler, progress_callback=bad_callback) as bridge:
        assert (await bridge.handle(job()))["status_code"] == 200
        assert len(failures) == 1
        assert len(scheduler.requests) == 1
        assert bridge.app.state.activity.snapshot() == []


async def test_setup_waits_for_terminal_model_progress_and_scrubs_paths(tmp_path):
    class Installer:
        def __init__(self):
            self.state = "not_started"
            self.task = None

        def start(self, _model_id):
            if self.task is None:
                self.state = "discovering"
                self.task = asyncio.create_task(self.install())

        async def install(self):
            await asyncio.sleep(0.03)
            self.state = "downloading"
            await asyncio.sleep(0.03)
            self.state = "verifying"
            await asyncio.sleep(0.03)
            self.state = "installed"

        def setup_status(self):
            return {
                "ready": self.state == "installed",
                "models": {model_id: {
                    "state": self.state, "bytes_total": 100, "bytes_completed": (
                        100 if self.state == "installed" else 0
                    ), "detail": "/private/token", "current_file": "/private/file",
                } for model_id in REQUIRED_MODEL_IDS},
            }

        async def shutdown(self):
            if self.task:
                await self.task

    updates = []

    async def callback(row):
        updates.append(row)

    async with handler(tmp_path, progress_callback=callback) as bridge:
        await bridge.app.state.installer.shutdown()
        bridge.app.state.installer = Installer()
        result = await bridge.handle(job("setup"))
        assert result["body"]["ready"]
        assert "private" not in json.dumps(result)
        assert {row["stage"] for row in updates} == {
            "checking_files", "downloading", "verifying",
        }
        assert all(row["model_id"] in REQUIRED_MODEL_IDS for row in updates)


async def test_marker_failure_is_definite_without_inference(tmp_path):
    volume = tmp_path / "durable"
    volume.write_text("cannot be a directory")
    scheduler = AudioScheduler()
    async with handler(tmp_path, scheduler) as bridge:
        result = await bridge.handle(job())
        assert result["status_code"] == 503
        assert result["body"]["code"] == "MARKER_UNAVAILABLE"
        assert not scheduler.requests



async def test_two_worker_instances_share_durable_duplicate_fence(tmp_path):
    left, right = AudioScheduler(), AudioScheduler()
    request = job()
    async with handler(tmp_path, left) as first, handler(tmp_path, right) as second:
        outputs = await asyncio.gather(first.handle(request), second.handle(request))
        assert sorted(row["status_code"] for row in outputs) == [200, 409]
        assert len(left.requests) + len(right.requests) == 1


async def test_timeout_cleans_audio_and_never_allows_replay(tmp_path, monkeypatch):
    import app.remote_worker.flex_handler as bridge_module

    original_timeout = asyncio.timeout
    controlled = []

    def deferred_timeout(delay):
        if delay == 0.02:
            deadline = original_timeout(None)
            controlled.append(deadline)
            return deadline
        return original_timeout(delay)

    monkeypatch.setattr(bridge_module.asyncio, "timeout", deferred_timeout)
    scheduler = AudioScheduler()
    scheduler.hold = True
    request = job()
    async with handler(tmp_path, scheduler, operation_timeout_sec=0.02) as bridge:
        task = asyncio.create_task(bridge.handle(request))
        await asyncio.wait_for(scheduler.entered.wait(), timeout=2)
        assert len(controlled) == 1
        paths = scheduler.requests[0]
        assert paths.reference_audio.exists()
        # Exercise a real asyncio deadline after inference admission. Multipart
        # parsing/CPU contention must not decide whether this cleanup case runs.
        controlled[0].reschedule(asyncio.get_running_loop().time() + 0.01)
        result = await asyncio.wait_for(task, timeout=2)
        assert result["status_code"] == 504
        assert result["body"]["code"] == "GENERATION_TIMEOUT"
        assert not paths.reference_audio.exists() and not paths.output_path.exists()
        assert bridge.app.state.activity.snapshot() == []
        assert (await bridge.handle(request))["body"]["code"] == "PRIOR_RESULT_UNAVAILABLE"


async def test_setup_cancellation_stops_background_work(tmp_path):
    class Installer:
        def __init__(self):
            self.running = False
            self.closed = False

        def start(self, _model_id):
            self.running = True

        def setup_status(self):
            return {
                "ready": False,
                "models": {model_id: {"state": "downloading"} for model_id in REQUIRED_MODEL_IDS},
            }

        async def shutdown(self):
            self.running = False
            self.closed = True

    async with handler(tmp_path, operation_timeout_sec=0.03) as bridge:
        await bridge.app.state.installer.shutdown()
        installer = Installer()
        bridge.app.state.installer = installer
        result = await bridge.handle(job("setup"))
        assert result["status_code"] == 504
        assert installer.closed and not installer.running


async def test_durable_marker_cannot_be_replayed_after_malformed_crash_record(tmp_path):
    request = job()
    namespace = tmp_path / "durable" / "flex-operations-v1"
    namespace.mkdir(parents=True)
    path = namespace / (request["input"]["operation_id"] + ".json")
    for value in ("", "null", "[]"):
        path.write_text(value)
        scheduler = AudioScheduler()
        async with handler(tmp_path, scheduler) as bridge:
            result = await bridge.handle(request)
            assert result["body"]["code"] == "PRIOR_RESULT_UNAVAILABLE"
            assert not scheduler.requests


@pytest.mark.parametrize("parameter", [
    {"inference_timesteps": "https://private/path"},
    {"inference_timesteps": 1.25},
    {"inference_timesteps": True},
    {"inference_timesteps": 100000},
    {"cfg_value": float("inf")},
])
async def test_model_knobs_cannot_accept_arbitrary_file_or_unbounded_values(tmp_path, parameter):
    scheduler = AudioScheduler()
    async with handler(tmp_path, scheduler) as bridge:
        result = await bridge.handle(job(payload={
            "model_id": "voxcpm2", "text": "Hello", "params": parameter,
        }))
        assert result["status_code"] == 422
        assert not scheduler.requests


async def test_worker_app_lifespan_is_persistent_and_closed_once(tmp_path):
    from contextlib import asynccontextmanager

    bridge = handler(tmp_path)
    original = bridge.app.router.lifespan_context
    events = []

    @asynccontextmanager
    async def counted(app):
        events.append("start")
        async with original(app):
            yield
        events.append("closed")

    bridge.app.router.lifespan_context = counted
    assert (await bridge.handle(job()))["status_code"] == 200
    assert (await bridge.handle(job()))["status_code"] == 200
    assert events == ["start"]
    await bridge.close()
    await bridge.close()
    assert events == ["start", "closed"]



async def test_cancelling_queued_setup_does_not_stop_current_worker(tmp_path):
    scheduler = AudioScheduler()
    scheduler.hold = True
    async with handler(tmp_path, scheduler) as bridge:
        first = asyncio.create_task(bridge.handle(job()))
        await asyncio.wait_for(scheduler.entered.wait(), 2)
        shutdowns = []
        original = bridge.app.state.installer.shutdown

        async def record_shutdown():
            shutdowns.append(True)
            await original()

        bridge.app.state.installer.shutdown = record_shutdown
        queued = asyncio.create_task(bridge.handle(job("setup")))
        await asyncio.sleep(0.02)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert not shutdowns
        scheduler.release.set()
        assert (await first)["status_code"] == 200

