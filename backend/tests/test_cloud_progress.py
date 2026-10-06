"""Progress reads never duplicate accepted work; warm idle remains bounded."""
import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from app.inference.catalog import CATALOG
from app.inference.progress import progress_scope
from app.inference.remote_scheduler import RemoteScheduler
from app.runpod.controller import CloudController


@pytest.mark.asyncio
async def test_progress_recovers_without_resubmitting_request():
    stages, paths = [], []
    seen_loading = asyncio.Event()
    status_reads = 0

    async def emit(stage, model_id):
        stages.append((stage, model_id))
        if stage == "generating":
            seen_loading.set()

    async def transport(request):
        nonlocal status_reads
        paths.append(request.url.path)
        if request.method == "POST":
            assert request.headers["X-Request-Id"] == "a" * 32
            await asyncio.wait_for(seen_loading.wait(), 10)
            return httpx.Response(200, json={"finished": True})
        status_reads += 1
        if status_reads == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"protocol_version": 1, "operations": [
            {"request_id": "b" * 32, "model_id": "voxcpm2", "stage": "converting"},
            {"request_id": "a" * 32, "model_id": "voxcpm2", "stage": "generating"},
        ]})

    remote = RemoteScheduler("https://pod.example", "secret", CATALOG,
                             transport=httpx.MockTransport(transport))
    try:
        with progress_scope(emit, request_id="a" * 32):
            response = await remote._response("POST", "/v1/synthesize", json={})
        assert response.status_code == 200
        assert paths.count("/v1/synthesize") == 1
        assert stages == [("progress_unavailable", None), ("generating", "voxcpm2")]
    finally:
        await remote.shutdown()


@pytest.mark.asyncio
async def test_old_worker_progress_is_unknown_not_fake_generation():
    stages = []
    complete = asyncio.Event()

    async def emit(stage, model_id):
        stages.append(stage)
        complete.set()

    async def transport(request):
        if request.method == "POST":
            await asyncio.wait_for(complete.wait(), 5)
            return httpx.Response(200)
        return httpx.Response(404)

    remote = RemoteScheduler("https://pod.example", "secret", CATALOG,
                             transport=httpx.MockTransport(transport))
    try:
        with progress_scope(emit, request_id="a" * 32):
            await remote._response("POST", "/v1/transliterate", json={})
        assert stages == ["worker_busy"]
    finally:
        await remote.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [120, 9])
async def test_idle_grace_capped_by_provider_deadline(tmp_path, monkeypatch, remaining):
    cloud = CloudController(Settings(data_dir=tmp_path, cloud_idle_grace_sec=60))
    cloud.write({"compute": {"deadline": (datetime.now(UTC) + timedelta(seconds=remaining)).isoformat()}})
    delays, stopped = [], []

    async def sleep(delay):
        delays.append(delay)

    async def release():
        stopped.append(True)

    monkeypatch.setattr("app.runpod.controller.asyncio.sleep", sleep)
    monkeypatch.setattr(cloud, "_release", release)
    await cloud._idle_release()
    assert len(delays) == 1
    assert 0 < delays[0] <= min(60, remaining)
    assert stopped == [True]


@pytest.mark.asyncio
async def test_active_generation_prevents_idle_release(tmp_path, monkeypatch):
    cloud = CloudController(Settings(data_dir=tmp_path))
    cloud.active = 1
    async def sleep(_):
        pass
    async def release():
        pytest.fail("Must not stop active work")
    monkeypatch.setattr("app.runpod.controller.asyncio.sleep", sleep)
    monkeypatch.setattr(cloud, "_release", release)
    await cloud._idle_release()
