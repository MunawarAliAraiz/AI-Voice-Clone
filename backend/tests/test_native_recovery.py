"""Crash and cleanup regression checks; provider activity is synthetic only."""

import asyncio
import base64
from uuid import uuid4

import pytest

from app.inference.native_scheduler import NativeScheduler
from app.runpod.flex_protocol import FlexInput
from app.runpod.native_admission import NativeAdmissionError
from app.runpod.native_backend import NativeBackend
from tests.test_native_admission import NOW
from tests.test_native_transport import wav

pytest_plugins = ["tests.test_native_transport"]


def synthesis():
    return FlexInput(
        operation_id=uuid4(), operation="synthesize",
        payload={"model_id": "voxcpm2"},
        reference_audio_b64=base64.b64encode(wav()).decode(),
    )


def paid_calls(provider):
    return [r for r in provider.calls if r.method == "POST" and
            (r.url.path == "/v2/serverless" or r.url.path.endswith("/run"))]


@pytest.mark.asyncio
async def test_ledger_id_repairs_file_journal_without_replay(setup, monkeypatch):
    provider, _, ledger, backend = setup
    value = synthesis()
    try:
        endpoint = await backend.start()
        original = backend.jobs._patch

        def interrupted_patch(*args, **kwargs):
            raise OSError("synthetic crash after ledger commit")

        monkeypatch.setattr(backend.jobs, "_patch", interrupted_patch)
        with pytest.raises(OSError):
            await backend.jobs.submit_once(endpoint, value)
        assert ledger.operation_view(str(value.operation_id))["job"] == "providerjob1"
        assert backend.jobs._read(value.operation_id)["provider_job_id"] is None
        before = len(paid_calls(provider))
        monkeypatch.setattr(backend.jobs, "_patch", original)
        assert await backend.recover_operation(value) == endpoint
        assert backend.jobs._read(value.operation_id)["provider_job_id"] == "providerjob1"
        assert len(paid_calls(provider)) == before
        assert (await backend.poll_once(value.operation_id)).status == "COMPLETED"
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_stopping_restart_schedules_cleanup_before_refusal(setup):
    provider, transport, ledger, backend = setup
    await backend.start()
    sid = backend.session_id
    ledger.request_stop(sid)
    backend.sweep_task.cancel()
    await asyncio.gather(backend.sweep_task, return_exceptions=True)
    recovered = NativeBackend(
        transport, ledger, backend.qualification, backend.deployment,
        root=backend.jobs.root, cache_prefix=backend.cache_prefix, clock=lambda: NOW,
    )
    try:
        before = len(paid_calls(provider))
        with pytest.raises(NativeAdmissionError, match="cleanup"):
            await recovered.start()
        assert recovered.session_id == sid
        assert recovered.sweep_task is not None and not recovered.sweep_task.done()
        assert len(paid_calls(provider)) == before
    finally:
        await recovered.close()


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_close_after_confirmed_absence(setup):
    provider, _, ledger, backend = setup
    await backend.start()
    await backend.stop()
    assert ledger.view(backend.session_id)["state"] == "stopping"
    backend.clock = lambda: NOW + 1
    await backend.stop()
    assert ledger.view(backend.session_id)["state"] == "stopped"
    before = len(provider.calls)
    await backend.stop()
    await backend.close()
    assert len(provider.calls) == before


@pytest.mark.asyncio
async def test_saved_result_retrieval_after_admission_deadline(setup):
    provider, _, ledger, backend = setup
    value = synthesis()
    try:
        endpoint = await backend.start()
        await backend.jobs.submit_once(endpoint, value)
        backend.clock = lambda: ledger.view(backend.session_id)["deadline"] + 1
        before = len(paid_calls(provider))
        output = await NativeScheduler(backend)._execute(value)
        assert output.status_code == 200
        assert output.audio_b64
        assert len(paid_calls(provider)) == before
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_cancel_preserves_cancelled_error_and_tracks_cleanup(setup, monkeypatch):
    _, _, _, backend = setup
    value = synthesis()
    entered, release = asyncio.Event(), asyncio.Event()

    async def waiting_poll(_operation):
        entered.set()
        await asyncio.Event().wait()

    async def cleanup(_operation):
        await release.wait()
        await backend.stop()

    monkeypatch.setattr(backend, "poll_once", waiting_poll)
    monkeypatch.setattr(backend, "cancel_operation", cleanup)
    task = asyncio.create_task(NativeScheduler(backend)._execute(value))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        for _ in range(20):
            await asyncio.sleep(0)
            if backend.cleanup_tasks:
                break
        assert len(backend.cleanup_tasks) == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        await asyncio.sleep(0)
        assert not backend.cleanup_tasks
        backend.clock = lambda: NOW + 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await backend.close()
