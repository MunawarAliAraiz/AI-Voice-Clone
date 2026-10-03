"""Actual scheduler boundaries appear in authenticated worker activity."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.inference.analyzer_scheduler import AnalyzerScheduler
from app.inference.catalog import CATALOG
from app.inference.progress import current_request_id, emit_progress, progress_scope
from app.inference.protocol import WireOp, WireResponse
from app.inference.scheduler import InferenceScheduler, SchedulerConfig
from app.inference.transliterator_scheduler import TransliteratorScheduler
from app.remote_worker.activity import ActivityStore, request_identity
from app.remote_worker.main import create_worker_app
from tests.fakes import FakeWorker


async def test_progress_scopes_isolate_concurrent_and_nested_requests():
    observed = []

    async def run(identity):
        async def record(stage, model):
            observed.append((current_request_id(), stage, model))

        with progress_scope(record, request_id=identity):
            await asyncio.sleep(0)
            await emit_progress("loading_model", "voxcpm2")
            with progress_scope(record):
                assert current_request_id() == identity
                await emit_progress("generating", "voxcpm2")
        assert current_request_id() is None

    await asyncio.gather(run("a" * 32), run("b" * 32))
    assert len(observed) == 4
    for identity in ("a" * 32, "b" * 32):
        assert [stage for request, stage, _ in observed if request == identity] == [
            "loading_model", "generating",
        ]
    await emit_progress("generating", "voxcpm2")  # No listener is harmless.


@pytest.mark.parametrize("value", ["private-script", "a" * 200, "/private/path", "g" * 32])
def test_request_identity_rejects_unbounded_or_non_uuid_values(value):
    with pytest.raises(HTTPException) as caught:
        request_identity(value)
    assert caught.value.status_code == 422
    assert value not in caught.value.detail


async def test_activity_is_bounded_allowlisted_and_removed_on_error():
    store = ActivityStore(frozenset({"voxcpm2"}), capacity=1)
    with pytest.raises(RuntimeError, match="test failure"):
        with store.track("a" * 32, "voxcpm2"):
            assert store.snapshot()[0]["stage"] == "queued"
            await emit_progress("loading_model", "voxcpm2")
            assert store.snapshot()[0]["stage"] == "loading_model"
            await emit_progress("private-user-script", "voxcpm2")
            await emit_progress("generating", "private-model")
            assert store.snapshot()[0]["stage"] == "loading_model"
            with pytest.raises(HTTPException) as full:
                with store.track("b" * 32, "voxcpm2"):
                    pass
            assert full.value.status_code == 503
            with pytest.raises(HTTPException) as duplicate:
                with store.track("a" * 32, "voxcpm2"):
                    pass
            assert duplicate.value.status_code == 409
            raise RuntimeError("test failure")
    assert store.snapshot() == []
    assert current_request_id() is None


async def test_http_activity_tracks_real_cold_and_resident_boundaries(tmp_path: Path):
    class ControlledWorker(FakeWorker):
        def __init__(self):
            super().__init__("voxcpm", load_delay_sec=0, synth_delay_sec=0)
            self.load_entered, self.load_release = asyncio.Event(), asyncio.Event()
            self.synth_entered, self.synth_release = asyncio.Event(), asyncio.Event()

        async def _load(self, rid, payload):
            self.load_entered.set()
            await self.load_release.wait()
            return await super()._load(rid, payload)

        async def _synth(self, rid, payload):
            self.synth_entered.set()
            await self.synth_release.wait()
            result = await super()._synth(rid, payload)
            Path(payload["output_path"]).write_bytes(b"FAKE-NOT-AUDIO")
            return result

    worker = ControlledWorker()

    async def factory(runtime):
        return worker

    scheduler = InferenceScheduler(CATALOG, factory, SchedulerConfig(budget_mb=16000))
    app = create_worker_app(
        scheduler=scheduler, settings=Settings(data_dir=tmp_path), token="test-secret",
    )
    headers = {"Authorization": "Bearer test-secret", "X-Request-Id": "a" * 32}
    body = {"request": json.dumps({"model_id": "voxcpm2", "text": "private script"})}
    files = {"reference_audio": ("private-reference.wav", b"test", "audio/wav")}
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://worker",
    ) as client:
        try:
            assert (await client.get("/v1/activity")).status_code == 401
            empty = (await client.get("/v1/activity", headers=headers)).json()
            assert empty["operations"] == [] and empty["loaded_model_ids"] == []
            task = asyncio.create_task(client.post(
                "/v1/synthesize", headers=headers, data=body, files=files,
            ))
            await asyncio.wait_for(worker.load_entered.wait(), timeout=2)
            loading = (await client.get("/v1/activity", headers=headers)).json()
            assert loading["operations"][0]["stage"] == "loading_model"
            assert loading["operations"][0]["request_id"] == "a" * 32
            assert loading["loaded_model_ids"] == []
            assert "private" not in json.dumps(loading)
            worker.load_release.set()
            await asyncio.wait_for(worker.synth_entered.wait(), timeout=2)
            running = (await client.get("/v1/activity", headers=headers)).json()
            assert running["operations"][0]["stage"] == "generating"
            assert running["loaded_model_ids"] == ["voxcpm2"]
            assert running["worker_instance_id"] == empty["worker_instance_id"]
            assert running["operations"][0]["elapsed_sec"] >= 0
            worker.synth_release.set()
            response = await task
            assert response.status_code == 200
            assert response.headers["X-Request-Id"] == "a" * 32
            assert (await client.get("/v1/activity", headers=headers)).json()["operations"] == []

            worker.synth_entered.clear()
            worker.synth_release.clear()
            task = asyncio.create_task(client.post(
                "/v1/synthesize", headers=headers, data=body, files=files,
            ))
            await asyncio.wait_for(worker.synth_entered.wait(), timeout=2)
            resident = (await client.get("/v1/activity", headers=headers)).json()
            assert resident["operations"][0]["stage"] == "generating"
            assert worker.load_calls == 1
            worker.synth_release.set()
            assert (await task).status_code == 200
            await worker.kill()
            assert (await client.get("/v1/activity", headers=headers)).json()[
                "loaded_model_ids"
            ] == []
        finally:
            worker.load_release.set()
            worker.synth_release.set()
            await scheduler.shutdown()


@pytest.mark.parametrize("kind,run_stage", [("analyzer", "analyzing"), ("converter", "converting")])
async def test_helper_actual_progress_skips_load_when_resident(kind, run_stage, monkeypatch):
    class Worker:
        is_alive = True

        async def call(self, op, payload, *, timeout):
            if op is WireOp.LOAD:
                return WireResponse(1, True, result={"load_time_sec": 0.02})
            return WireResponse(2, True, result={"text": "test", "rows": [], "gen_time_sec": 0.01})

        async def kill(self):
            self.is_alive = False

    class Slot:
        @asynccontextmanager
        async def reserve_slot(self, reason):
            yield

    helper = (
        AnalyzerScheduler(python_executable="unused") if kind == "analyzer"
        else TransliteratorScheduler(python_executable="unused", inference_scheduler=Slot())
    )

    async def start():
        helper._worker = Worker()
        helper._loaded = False

    monkeypatch.setattr(helper, "_start_worker", start)
    stages = []

    async def record(stage, model):
        stages.append(stage)

    async def run():
        if kind == "analyzer":
            await helper.classify(language="en", sentences=("test",))
        else:
            await helper.convert_many(texts=["test"])

    try:
        with progress_scope(record, request_id="a" * 32):
            await run()
            assert stages == ["loading_model", run_stage]
            stages.clear()
            await run()
            assert stages == [run_stage]
    finally:
        await helper.shutdown()
