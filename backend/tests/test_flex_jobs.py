"""Durable local recovery with simulated guard/provider. No cloud spend."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.runpod.flex_jobs import (
    DefiniteSubmissionRejection,
    FlexJobError,
    FlexJobs,
    RunpodFlexJobReads,
)
from app.runpod.flex_protocol import MAX_AUDIO_BYTES, FlexInput, FlexOutput, decode_audio


def request(operation="models", **payload):
    return FlexInput(operation_id=uuid4(), operation=operation, payload=payload)


class Guard:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def submit_once(self, endpoint_id, value):
        self.calls.append((endpoint_id, value))
        await asyncio.sleep(0)
        if self.error:
            raise self.error
        return "provider-job-123"


class Reads:
    def __init__(self):
        self.value = {"id": "provider-job-123", "status": "IN_QUEUE"}
        self.calls = []

    async def status(self, endpoint_id, job_id):
        self.calls.append(("GET", endpoint_id, job_id))
        if isinstance(self.value, Exception):
            raise self.value
        return self.value

    async def cancel(self, endpoint_id, job_id):
        self.calls.append(("CANCEL", endpoint_id, job_id))
        return {"status": "CANCELLED"}


@pytest.mark.asyncio
async def test_guard_required_before_creating_intent(tmp_path):
    jobs = FlexJobs(tmp_path / "journal", Reads())
    with pytest.raises(FlexJobError, match="protection is not connected"):
        await jobs.submit_once("endpoint123", request())
    assert not jobs.root.exists()


@pytest.mark.asyncio
async def test_duplicate_submit_and_restart_only_use_saved_id(tmp_path):
    guard, reads, value = Guard(), Reads(), request()
    jobs = FlexJobs(tmp_path, reads, submitter=guard)
    assert await jobs.submit_once("endpoint123", value) == "provider-job-123"
    assert await jobs.submit_once("endpoint123", value) == "provider-job-123"
    restarted = FlexJobs(tmp_path, reads, submitter=guard)
    assert await restarted.submit_once("endpoint123", value) == "provider-job-123"
    assert (await restarted.poll_once(value.operation_id)).status == "IN_QUEUE"
    assert len(guard.calls) == 1
    assert reads.calls == [("GET", "endpoint123", "provider-job-123")]


@pytest.mark.asyncio
async def test_concurrent_adapter_instances_claim_before_one_post(tmp_path):
    guard, reads, value = Guard(), Reads(), request()
    instances = [FlexJobs(tmp_path, reads, submitter=guard) for _ in range(2)]
    results = await asyncio.gather(
        *(j.submit_once("endpoint123", value) for j in instances), return_exceptions=True
    )
    assert sum(result == "provider-job-123" for result in results) >= 1
    assert all(
        result == "provider-job-123" or isinstance(result, FlexJobError) for result in results
    )
    assert len(guard.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [TimeoutError("secret"), httpx.ReadError("private"), ValueError("provider-token")]
)
async def test_lost_submit_never_replays_and_never_echoes_raw_error(tmp_path, error):
    guard, value = Guard(error), request()
    jobs = FlexJobs(tmp_path, Reads(), submitter=guard)
    with pytest.raises(FlexJobError, match="uncertain") as failed:
        await jobs.submit_once("endpoint123", value)
    assert str(error) not in str(failed.value)
    with pytest.raises(FlexJobError, match="uncertain"):
        await FlexJobs(tmp_path, Reads(), submitter=guard).submit_once("endpoint123", value)
    assert len(guard.calls) == 1
    record = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert record["phase"] == "SUBMITTING" and record["provider_job_id"] is None


@pytest.mark.asyncio
async def test_cancelled_submit_retains_uncertain_fence(tmp_path):
    guard, value = Guard(asyncio.CancelledError()), request()
    jobs = FlexJobs(tmp_path, Reads(), submitter=guard)
    with pytest.raises(asyncio.CancelledError):
        await jobs.submit_once("endpoint123", value)
    with pytest.raises(FlexJobError, match="uncertain"):
        await jobs.submit_once("endpoint123", value)
    assert len(guard.calls) == 1


@pytest.mark.asyncio
async def test_definite_rejection_does_not_automatically_retry(tmp_path):
    guard = Guard(DefiniteSubmissionRejection("Guard rejected before work"))
    jobs, value = FlexJobs(tmp_path, Reads(), submitter=guard), request()
    with pytest.raises(DefiniteSubmissionRejection):
        await jobs.submit_once("endpoint123", value)
    with pytest.raises(FlexJobError):
        await jobs.submit_once("endpoint123", value)
    assert len(guard.calls) == 1
    assert jobs._read(value.operation_id)["phase"] == "REJECTED"


@pytest.mark.asyncio
async def test_changed_request_and_endpoint_cannot_reuse_identity(tmp_path):
    guard, value = Guard(), request("analyze", language="en", sentences=["private script"])
    jobs = FlexJobs(tmp_path, Reads(), submitter=guard)
    await jobs.submit_once("endpoint123", value)
    altered = value.model_copy(update={"payload": {"language": "ur", "sentences": ["other"]}})
    with pytest.raises(FlexJobError, match="different work"):
        await jobs.submit_once("endpoint123", altered)
    with pytest.raises(FlexJobError, match="different work"):
        await jobs.submit_once("otherendpoint", value)
    text = next(tmp_path.glob("*.json")).read_text()
    assert "private script" not in text and "sentences" not in text
    assert "payload" not in text and "api_key" not in text
    assert len(guard.calls) == 1


@pytest.mark.asyncio
async def test_known_status_network_failure_only_retries_reads(tmp_path):
    reads, guard, value = Reads(), Guard(), request()
    jobs = FlexJobs(tmp_path, reads, submitter=guard)
    await jobs.submit_once("endpoint123", value)
    reads.value = FlexJobError("Status unavailable")
    with pytest.raises(FlexJobError):
        await jobs.poll_once(value.operation_id)
    reads.value = {"id": "provider-job-123", "status": "IN_PROGRESS"}
    assert (await jobs.poll_once(value.operation_id)).status == "IN_PROGRESS"
    assert len(guard.calls) == 1 and len(reads.calls) == 2


@pytest.mark.asyncio
async def test_real_progress_is_scoped_and_not_an_eta(tmp_path):
    reads, value = Reads(), request("warm", model_id="voxcpm2")
    jobs = FlexJobs(tmp_path, reads, submitter=Guard())
    await jobs.submit_once("endpoint123", value)
    reads.value = {
        "id": "provider-job-123",
        "status": "IN_PROGRESS",
        "progress": {
            "protocol_version": 1,
            "operation_id": str(value.operation_id),
            "stage": "loading_model",
            "model_id": "voxcpm2",
        },
        "delayTime": 1234,
        "executionTime": float("nan"),
    }
    view = await jobs.poll_once(value.operation_id)
    assert view.stage == "loading_model" and view.model_id == "voxcpm2"
    assert view.delay_ms == 1234 and view.execution_ms is None
    reads.value["progress"]["operation_id"] = str(uuid4())
    assert (await jobs.poll_once(value.operation_id)).stage is None
    reads.value["progress"]["operation_id"] = str(value.operation_id)
    reads.value["progress"]["stage"] = ["loading_model"]
    assert (await jobs.poll_once(value.operation_id)).stage is None


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["CANCELLED", "FAILED", "TIMED_OUT"])
async def test_cancel_waits_for_original_job_and_terminal_cancel_is_idempotent(tmp_path, state):
    reads, value = Reads(), request()
    jobs = FlexJobs(tmp_path, reads, submitter=Guard())
    await jobs.submit_once("endpoint123", value)
    reads.value = {"id": "provider-job-123", "status": state}
    assert (await jobs.cancel(value.operation_id)).status == state
    assert sum(call[0] == "CANCEL" for call in reads.calls) == 1
    # A terminal repeat must not deadlock trying to acquire the same lock.
    assert (await asyncio.wait_for(jobs.cancel(value.operation_id), 1)).status == state
    assert sum(call[0] == "CANCEL" for call in reads.calls) == 1


@pytest.mark.asyncio
async def test_cancel_acknowledgement_without_terminal_status_is_not_completion(tmp_path):
    reads, value = Reads(), request()
    jobs = FlexJobs(tmp_path, reads, submitter=Guard())
    await jobs.submit_once("endpoint123", value)
    with pytest.raises(FlexJobError, match="not confirmed"):
        await jobs.cancel(value.operation_id, timeout_sec=0.01)
    assert jobs._read(value.operation_id)["phase"] not in {"CANCELLED", "COMPLETED"}


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [[], "UNKNOWN"])
async def test_malformed_provider_status_is_rejected(tmp_path, state):
    reads, value = Reads(), request()
    jobs = FlexJobs(tmp_path, reads, submitter=Guard())
    await jobs.submit_once("endpoint123", value)
    reads.value = {"id": "provider-job-123", "status": state}
    with pytest.raises(FlexJobError, match="mismatched"):
        await jobs.poll_once(value.operation_id)


@pytest.mark.asyncio
async def test_completed_output_must_match_identity_and_cannot_leak_extra_headers(tmp_path):
    reads, value = Reads(), request()
    jobs = FlexJobs(tmp_path, reads, submitter=Guard())
    await jobs.submit_once("endpoint123", value)
    output = FlexOutput(operation_id=value.operation_id, status_code=200, body={"models": []})
    reads.value = {
        "id": "provider-job-123",
        "status": "COMPLETED",
        "output": output.model_dump(mode="json"),
    }
    assert (await jobs.poll_once(value.operation_id)).output.body == {"models": []}
    reads.value["output"]["operation_id"] = str(uuid4())
    with pytest.raises(FlexJobError, match="another operation"):
        await jobs.poll_once(value.operation_id)
    reads.value["output"]["operation_id"] = str(value.operation_id)
    reads.value["output"]["headers"] = {"Authorization": "secret"}
    with pytest.raises(FlexJobError, match="could not be verified"):
        await jobs.poll_once(value.operation_id)


@pytest.mark.asyncio
async def test_reads_never_call_health_run_retry_or_creation_and_hide_provider_errors():
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(500, json={"detail": "TOKEN-SECRET"})

    reads = RunpodFlexJobReads("private", transport=httpx.MockTransport(respond))
    try:
        for operation in (reads.status, reads.cancel):
            with pytest.raises(FlexJobError) as error:
                await operation("endpoint123", "provider-job-123")
            assert "TOKEN-SECRET" not in str(error.value)
    finally:
        await reads.close()
    assert calls == [
        ("GET", "/v2/endpoint123/status/provider-job-123"),
        ("POST", "/v2/endpoint123/cancel/provider-job-123"),
    ]


@pytest.mark.parametrize("audio", ["", "not-base64", "YQ==\n"])
def test_audio_encoding_is_strict(audio):
    with pytest.raises(ValueError):
        decode_audio(audio)


def test_protocol_blocks_arbitrary_operations_credentials_and_unbounded_audio():
    for extra in ({"operation": "delete"}, {"operation": "models", "api_key": "secret"}):
        with pytest.raises(ValidationError):
            FlexInput(operation_id=uuid4(), **extra)
    with pytest.raises(ValueError):
        decode_audio("A" * (4 * ((MAX_AUDIO_BYTES + 2) // 3) + 1))
    with pytest.raises(ValidationError):
        FlexInput(operation_id=uuid4(), operation="models", payload={"bad": float("nan")})


def test_protocol_rejects_nested_nonfinite_result_values():
    with pytest.raises(ValidationError):
        FlexOutput(operation_id=uuid4(), status_code=200, body={"nested": [{"bad": float("inf")}]})


@pytest.mark.asyncio
async def test_mismatched_model_progress_is_unknown(tmp_path):
    reads, value = Reads(), request("warm", model_id="voxcpm2")
    jobs = FlexJobs(tmp_path, reads, submitter=Guard())
    await jobs.submit_once("endpoint123", value)
    reads.value = {
        "id": "provider-job-123",
        "status": "IN_PROGRESS",
        "progress": {
            "protocol_version": 1,
            "operation_id": str(value.operation_id),
            "stage": "loading_model",
            "model_id": "omnivoice_urdu",
        },
    }
    result = await jobs.poll_once(value.operation_id)
    assert result.stage is None and result.model_id is None


@pytest.mark.asyncio
async def test_completed_synthesis_rejects_truncated_wav_with_valid_header(tmp_path):
    import base64
    import io
    import wave

    reads, guard = Reads(), Guard()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x01\x00" * 8000)
    original = buffer.getvalue()
    value = FlexInput(
        operation_id=uuid4(),
        operation="synthesize",
        payload={"model_id": "voxcpm2"},
        reference_audio_b64=base64.b64encode(original).decode(),
    )
    jobs = FlexJobs(tmp_path, reads, submitter=guard)
    await jobs.submit_once("endpoint123", value)
    result = FlexOutput(
        operation_id=value.operation_id,
        status_code=200,
        headers={
            "x-model-id": "voxcpm2",
            "x-request-id": str(value.operation_id),
            "x-audio-duration-sec": "1",
            "x-generation-time-sec": "2",
            "x-load-time-sec": "3",
            "content-type": "audio/wav",
        },
        audio_b64=base64.b64encode(original[:-4000]).decode(),
    )
    reads.value = {
        "id": "provider-job-123",
        "status": "COMPLETED",
        "output": result.model_dump(mode="json"),
    }
    with pytest.raises(FlexJobError, match="could not be verified"):
        await jobs.poll_once(value.operation_id)
    reads.value["output"]["audio_b64"] = base64.b64encode(original).decode()
    assert (await jobs.poll_once(value.operation_id)).output is not None


@pytest.mark.asyncio
async def test_provider_redirect_body_is_never_accepted_as_status():
    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(
            302,
            headers={"Location": "https://example.com"},
            json={"id": "provider-job-123", "status": "COMPLETED"},
        )

    reads = RunpodFlexJobReads("private", transport=httpx.MockTransport(redirect))
    try:
        with pytest.raises(FlexJobError, match="could not confirm"):
            await reads.status("endpoint123", "provider-job-123")
    finally:
        await reads.close()
    assert len(calls) == 1


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_protocol_version_is_strict_across_python_and_javascript(version):
    with pytest.raises(ValidationError):
        FlexInput(operation_id=uuid4(), operation="models", protocol_version=version)
    with pytest.raises(ValidationError):
        FlexOutput(operation_id=uuid4(), status_code=200, protocol_version=version)


def test_nil_input_operation_uuid_cannot_bypass_guard_identity_contract():
    from uuid import UUID

    with pytest.raises(ValidationError):
        FlexInput(operation_id=UUID(int=0), operation="models")
