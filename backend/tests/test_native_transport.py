"""Offline transport/controller checks. All provider responses are synthetic."""

from __future__ import annotations

import base64
import io
import json
import wave
from uuid import uuid4

import httpx
import pytest

from app.config import Settings
from app.exceptions import GenerationError
from app.inference.native_scheduler import NativeScheduler
from app.inference.progress import progress_scope
from app.inference.protocol import SynthRequest
from app.runpod.controller import CloudController
from app.runpod.flex_jobs import FlexJobError
from app.runpod.flex_protocol import FlexInput
from app.runpod.native_admission import NativeAdmissionError, prepare_envelope
from app.runpod.native_backend import NativeBackend, NativeDeployment, deployment_for
from app.runpod.native_ledger import NativeLedger
from app.runpod.native_qualification import NativeQualificationContext
from app.runpod.native_transport import NativeTransport
from tests.test_native_admission import HASH, IMAGE, NOW, qualification, raw_catalog, workers_raw


def wav():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x01\x00" * 2400)
    return stream.getvalue()


class Provider:
    def __init__(self):
        self.calls = []
        self.raw = None
        self.inputs = {}
        self.lost_create = False
        self.lost_submit = False
        self.workers_missing = False
        self.deleted = False
        self.status_code = 200
        self.bad_model = False
        self.progress = False
        self.cache_missing = False

    def respond(self, request):
        self.calls.append(request)
        path = request.url.path
        assert request.headers["authorization"] == "Bearer test-only"
        if path.endswith("catalog/gpus"):
            assert request.url.params["product"] == "SERVERLESS"
            return httpx.Response(200, json=raw_catalog())
        if path == "/v2/serverless" and request.method == "POST":
            self.raw = {**json.loads(request.content), "id": "endpoint1"}
            if self.lost_create:
                raise httpx.ReadTimeout("synthetic lost response", request=request)
            return httpx.Response(201, json=self.raw)
        if path == "/v2/serverless" and request.method == "GET":
            # An unavailable listing must leave uncertain creation fenced.
            return httpx.Response(503)
        if path.endswith("endpoint1/workers"):
            if self.workers_missing:
                return httpx.Response(404)
            value = workers_raw()
            value["workers"] = []
            value["summary"] = dict.fromkeys(value["summary"], 0)
            return httpx.Response(200, json=value)
        if path.endswith("/run"):
            value = json.loads(request.content)["input"]
            self.inputs["providerjob1"] = value
            if self.lost_submit:
                raise httpx.ReadTimeout("synthetic lost response", request=request)
            return httpx.Response(200, json={"id": "providerjob1"})
        if "/status/" in path:
            if self.status_code != 200:
                return httpx.Response(self.status_code)
            value = self.inputs["providerjob1"]
            if self.progress:
                self.progress = False
                return httpx.Response(
                    200,
                    json={
                        "id": "providerjob1",
                        "status": "IN_PROGRESS",
                        "progress": {
                            "protocol_version": 1,
                            "operation_id": value["operation_id"],
                            "stage": "generating",
                            "model_id": value["payload"]["model_id"],
                        },
                    },
                )
            if self.cache_missing:
                output = {
                    "protocol_version": 1,
                    "operation_id": value["operation_id"],
                    "status_code": 503,
                    "body": {"code": "MODEL_CACHE_MISSING"},
                    "headers": {},
                }
            else:
                output = {
                    "protocol_version": 1,
                    "operation_id": value["operation_id"],
                    "status_code": 200,
                    "audio_b64": base64.b64encode(wav()).decode(),
                    "headers": {
                        "x-model-id": "wrong" if self.bad_model else value["payload"]["model_id"],
                        "x-request-id": value["operation_id"],
                        "x-audio-duration-sec": "0.1",
                        "x-generation-time-sec": "0.2",
                        "x-load-time-sec": "0.3",
                    },
                }
            return httpx.Response(
                200, json={"id": "providerjob1", "status": "COMPLETED", "output": output}
            )
        if path.endswith("endpoint1"):
            if request.method == "DELETE":
                self.deleted = True
                return httpx.Response(204)
            return httpx.Response(404) if self.deleted else httpx.Response(200, json=self.raw)
        if path.endswith("billing/serverless"):
            return httpx.Response(
                200, json={"records": [], "metadata": {"query": dict(request.url.params)}}
            )
        pytest.fail(f"Unexpected synthetic request: {request.method} {path}")


@pytest.fixture
def setup(tmp_path):
    provider = Provider()
    transport = NativeTransport("test-only", transport=httpx.MockTransport(provider.respond))
    ledger = NativeLedger(tmp_path / "account.sqlite", "account1")
    ledger.approve_once("0.50", approval_receipt=HASH)
    context = NativeQualificationContext(IMAGE, "volume1", "US-NE-1", "ADA_24", HASH)
    deployment = NativeDeployment(context, 24, 400, "0.01")
    backend = NativeBackend(
        transport,
        ledger,
        qualification(),
        deployment,
        root=tmp_path / "jobs",
        cache_prefix="voice-clone/hf-cache/hub/",
        clock=lambda: NOW,
    )
    return provider, transport, ledger, backend


@pytest.mark.asyncio
async def test_real_adapter_contract_persists_once_and_writes_local_wav(setup, tmp_path):
    provider, _transport, ledger, backend = setup
    reference = tmp_path / "ref.wav"
    reference.write_bytes(wav())
    request = SynthRequest(
        model_id="voxcpm2",
        text="Hello",
        reference_audio=reference,
        reference_text="Hello",
        params={},
        sample_rate=24000,
        output_path=tmp_path / "out.wav",
    )
    scheduler = NativeScheduler(backend)
    events = []

    async def report(stage, model):
        events.append((stage, model))

    provider.progress = True
    try:
        with progress_scope(report, request_id=str(uuid4())):
            result = await scheduler.synthesize(request)
            # Same operation recovers original provider result; no second /run.
            repeated = await scheduler.synthesize(request)
        assert result.output_path.read_bytes() == wav()
        assert repeated.output_path == result.output_path
        assert events == [("generating", "voxcpm2")]
        assert len([r for r in provider.calls if r.url.path.endswith("/run")]) == 1
        created = provider.raw
        assert created["env"]["HF_HOME"] == "/runpod-volume/voice-clone/hf-cache"
        assert created["workers"] == {"min": 0, "max": 1, "idleTimeout": 60}
        assert ledger.view(backend.session_id)["warm_until"] == NOW + 60
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_lost_creation_never_replayed(setup):
    provider, transport, ledger, backend = setup
    provider.lost_create = True
    try:
        with pytest.raises(NativeAdmissionError):
            await backend.start()
        with pytest.raises(NativeAdmissionError):
            await transport.create_claimed(
                ledger, backend.session_id, now=NOW, cache_prefix=backend.cache_prefix
            )
        assert (
            len(
                [r for r in provider.calls if r.method == "POST" and r.url.path == "/v2/serverless"]
            )
            == 1
        )
        assert ledger.view(backend.session_id)["state"] == "stopping"
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_lost_submission_retains_fence_and_does_not_credit(setup):
    provider, _transport, ledger, backend = setup
    value = FlexInput(operation_id=uuid4(), operation="warm", payload={"model_id": "voxcpm2"})
    try:
        endpoint = await backend.start()
        provider.lost_submit = True
        with pytest.raises(FlexJobError, match="uncertain"):
            await backend.jobs.submit_once(endpoint, value)
        with pytest.raises(FlexJobError):
            await backend.jobs.submit_once(endpoint, value)
        assert ledger.operation_view(str(value.operation_id))["state"] == "submitting"
        assert len([r for r in provider.calls if r.url.path.endswith("/run")]) == 1
        assert ledger.available_micro() < 500000
        backend.clock = lambda: NOW + 1
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_submission_body_must_match_reservation(setup):
    provider, transport, ledger, backend = setup
    try:
        endpoint = await backend.start()
        value = FlexInput(operation_id=uuid4(), operation="warm", payload={"model_id": "voxcpm2"})
        body, digest = prepare_envelope(
            value,
            now=NOW,
            deadline=ledger.view(backend.session_id)["deadline"],
            execution_seconds=100,
        )
        ledger.reserve_intent(
            backend.session_id,
            str(value.operation_id),
            digest,
            now=NOW,
            execution_seconds=100,
            queue_seconds=0,
        )
        body["input"]["payload"]["model_id"] = "chatterbox_ml_v3"
        with pytest.raises(NativeAdmissionError, match="reservation"):
            await transport.submit_claimed(ledger, str(value.operation_id), endpoint, body, now=NOW)
        assert not any(r.url.path.endswith("/run") for r in provider.calls)
        assert ledger.operation_view(str(value.operation_id))["state"] == "prepared"
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_changed_ownership_refuses_delete(setup):
    provider, transport, _ledger, backend = setup
    try:
        await backend.start()
        provider.raw["image"] = "ghcr.io/other/worker@sha256:" + "c" * 64
        with pytest.raises(NativeAdmissionError, match="ownership"):
            await backend.stop()
        assert not any(r.method == "DELETE" for r in provider.calls)
    finally:
        if backend.sweep_task:
            backend.sweep_task.cancel()
            await __import__("asyncio").gather(backend.sweep_task, return_exceptions=True)
        await transport.close()


@pytest.mark.asyncio
async def test_worker_404_does_not_confirm_compute_stop(setup):
    provider, _transport, ledger, backend = setup
    try:
        await backend.start()
        provider.workers_missing = True
        await backend.stop()
        backend.clock = lambda: NOW + 1
        await backend.stop()
        row = ledger.view(backend.session_id)
        assert row["state"] == "stopping" and row["absence_count"] == 0
        assert row["settlement"] is None and row["paid"] == 0
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_missing_allowance_rejects_before_creation(setup, tmp_path):
    provider, _transport, _ledger, backend = setup
    backend.ledger = NativeLedger(tmp_path / "no-allowance.sqlite", "account1")
    try:
        with pytest.raises(NativeAdmissionError, match="approval"):
            await backend.start()
        assert not any(r.method == "POST" for r in provider.calls)
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_default_controller_does_not_use_saved_pod(setup, tmp_path, monkeypatch):
    cloud = CloudController(Settings(data_dir=tmp_path))
    monkeypatch.setattr(cloud, "is_ready", lambda _: True)
    monkeypatch.setattr(
        cloud,
        "storage_binding",
        lambda: type("Binding", (), {"volume_id": "volume1", "region": "US-NE-1"})(),
    )
    cloud.write({"compute": {"pod_id": "savedpod1"}})
    monkeypatch.setattr(cloud, "key", lambda: pytest.fail("Qualification must precede key access"))
    with pytest.raises(GenerationError, match="automatic spending protection"):
        async with cloud.session():
            pytest.fail("Unqualified native session reached inference")
    assert cloud.active == 0 and cloud.native_backend is None


def test_source_deployment_cannot_be_enabled_from_binding():
    binding = type("Binding", (), {"volume_id": "volume1", "region": "US-NE-1"})()
    with pytest.raises(NativeAdmissionError, match="not qualified"):
        deployment_for(binding, HASH, now=NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,bad_model", [(404, False), (200, True)])
async def test_expired_or_mismatched_result_never_replays(setup, status, bad_model):
    provider, _transport, ledger, backend = setup
    value = FlexInput(
        operation_id=uuid4(),
        operation="synthesize",
        payload={"model_id": "voxcpm2"},
        reference_audio_b64=base64.b64encode(wav()).decode(),
    )
    try:
        endpoint = await backend.start()
        await backend.jobs.submit_once(endpoint, value)
        provider.status_code, provider.bad_model = status, bad_model
        with pytest.raises(FlexJobError):
            await backend.poll_once(value.operation_id)
        assert ledger.operation_view(str(value.operation_id))["state"] == "submitted"
        assert ledger.view(backend.session_id)["warm_until"] is None
        assert len([r for r in provider.calls if r.url.path.endswith("/run")]) == 1
    finally:
        await backend.close()
