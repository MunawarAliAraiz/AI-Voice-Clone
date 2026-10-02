"""Updater admission fences real concurrent HTTP mutations, without cloud access."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from app.api.deps import ApiKeyMiddleware
from app.api.routers import desktop_updates
from app.config import Settings
from app.jobs.types import JobKind
from fastapi import FastAPI


@pytest.fixture
def update_app(tmp_path, monkeypatch):
    class Database:
        def __init__(self):
            self.pending = {}
            self.calls = []

        async def count_pending(self, kind):
            self.calls.append(kind)
            return self.pending.get(kind, 0)

    class Cloud:
        active = 0
        setup_task = None
        release_task = None
        compute = None
        snapshots = 0

        async def snapshot(self):
            self.snapshots += 1
            return {"compute": self.compute}

    app = FastAPI()
    app.state.settings = Settings(data_dir=tmp_path, desktop_static_dir=tmp_path / "assets")
    app.state.db = Database()
    app.state.cloud = Cloud()
    app.state.desktop_mutation_lock = asyncio.Lock()
    app.state.desktop_updating = False
    app.state.mutations = 0
    app.state.mutation_started = asyncio.Event()
    app.state.mutation_finish = asyncio.Event()
    app.state.hold_mutation = False
    monkeypatch.setattr(desktop_updates, "controller", lambda _: app.state.cloud)
    app.include_router(desktop_updates.router, prefix="/api")

    @app.api_route("/api/mutate", methods=["POST", "PUT", "PATCH", "DELETE"])
    async def mutate():
        app.state.mutation_started.set()
        if app.state.hold_mutation:
            await app.state.mutation_finish.wait()
            app.state.db.pending = {JobKind.SYNTHESIZE.value: 1}
        app.state.mutations += 1
        return {"accepted": True}

    @app.get("/api/read")
    async def read():
        return {"available": True}

    app.add_middleware(ApiKeyMiddleware, api_key="isolated-session")
    return app


def client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://local",
        headers={"X-API-Key": "isolated-session"},
    )


@pytest.mark.asyncio
async def test_idle_prepare_is_idempotent_reads_survive_and_cancel_restores_mutations(update_app):
    async with client(update_app) as http:
        assert (await http.post("/api/desktop/updates/prepare")).json() == {"prepared": True}
        assert set(update_app.state.db.calls) == {kind.value for kind in JobKind}
        calls = len(update_app.state.db.calls)
        assert (await http.post("/api/desktop/updates/prepare")).json() == {"prepared": True}
        assert len(update_app.state.db.calls) == calls
        assert (await http.get("/api/read")).status_code == 200
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert (await http.request(method, "/api/mutate")).status_code == 409
        assert update_app.state.mutations == 0
        # A different verb cannot use the update-control allowlist.
        assert (await http.put("/api/desktop/updates/cancel")).status_code == 409
        assert (await http.post("/api/desktop/updates/cancel")).json() == {"prepared": False}
        assert (await http.post("/api/mutate")).status_code == 200
        assert update_app.state.mutations == 1


@pytest.mark.parametrize("kind", list(JobKind))
@pytest.mark.asyncio
async def test_each_pending_job_kind_blocks_preparation(update_app, kind):
    update_app.state.db.pending = {kind.value: 1}
    async with client(update_app) as http:
        result = await http.post("/api/desktop/updates/prepare")
        assert result.status_code == 409 and "jobs" in result.json()["detail"]
        assert not update_app.state.desktop_updating
        assert (await http.post("/api/mutate")).status_code == 200


@pytest.mark.parametrize("busy", ["compute", "active", "setup_task", "release_task"])
@pytest.mark.asyncio
async def test_cloud_compute_and_inflight_lifecycle_block_preparation(update_app, busy):
    cloud = update_app.state.cloud
    if busy == "compute":
        cloud.compute = {"pod_id": "owned-fixture"}
    elif busy == "active":
        cloud.active = 1
    else:
        setattr(cloud, busy, SimpleNamespace(done=lambda: False))
    async with client(update_app) as http:
        result = await http.post("/api/desktop/updates/prepare")
        assert result.status_code == 409 and "cloud session" in result.json()["detail"]
        assert not update_app.state.desktop_updating


@pytest.mark.asyncio
async def test_completed_lifecycle_tasks_do_not_block_preparation(update_app):
    cloud = update_app.state.cloud
    cloud.setup_task = cloud.release_task = SimpleNamespace(done=lambda: True)
    async with client(update_app) as http:
        assert (await http.post("/api/desktop/updates/prepare")).status_code == 200


@pytest.mark.asyncio
async def test_existing_mutation_finishes_before_prepare_can_inspect_queue(update_app):
    update_app.state.hold_mutation = True
    async with client(update_app) as http:
        mutation = asyncio.create_task(http.post("/api/mutate"))
        await asyncio.wait_for(update_app.state.mutation_started.wait(), timeout=2)
        prepare = asyncio.create_task(http.post("/api/desktop/updates/prepare"))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(prepare), timeout=0.05)
            assert update_app.state.db.calls == []
        finally:
            update_app.state.mutation_finish.set()
        assert (await asyncio.wait_for(mutation, timeout=2)).status_code == 200
        assert (await asyncio.wait_for(prepare, timeout=2)).status_code == 409
        assert not update_app.state.desktop_updating


@pytest.mark.asyncio
async def test_prepare_winning_race_refuses_waiting_mutation(update_app):
    entered, finish = asyncio.Event(), asyncio.Event()

    async def snapshot():
        entered.set()
        await finish.wait()
        return {"compute": None}

    update_app.state.cloud.snapshot = snapshot
    async with client(update_app) as http:
        prepare = asyncio.create_task(http.post("/api/desktop/updates/prepare"))
        await asyncio.wait_for(entered.wait(), timeout=2)
        mutation = asyncio.create_task(http.post("/api/mutate"))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(mutation), timeout=0.05)
            assert update_app.state.mutations == 0
        finally:
            finish.set()
        assert (await asyncio.wait_for(prepare, timeout=2)).status_code == 200
        assert (await asyncio.wait_for(mutation, timeout=2)).status_code == 409
        assert update_app.state.mutations == 0


@pytest.mark.asyncio
async def test_prepare_cancel_require_auth_even_when_prepared(update_app):
    async with client(update_app) as http:
        for endpoint in ("prepare", "cancel"):
            assert (
                await http.post(f"/api/desktop/updates/{endpoint}", headers={"X-API-Key": "wrong"})
            ).status_code == 401
        assert not update_app.state.desktop_updating
        await http.post("/api/desktop/updates/prepare")
        assert (
            await http.post("/api/desktop/updates/cancel", headers={"X-API-Key": "wrong"})
        ).status_code == 401
        assert update_app.state.desktop_updating


@pytest.mark.asyncio
async def test_update_routes_refused_in_web_mode(update_app, tmp_path):
    update_app.state.settings = Settings(data_dir=tmp_path)
    async with client(update_app) as http:
        for endpoint in ("prepare", "cancel"):
            assert (await http.post(f"/api/desktop/updates/{endpoint}")).status_code == 404
        assert (await http.post("/api/mutate")).status_code == 200
        assert not update_app.state.desktop_updating


@pytest.mark.asyncio
async def test_bare_app_middleware_keeps_auth_without_settings():
    app = FastAPI()

    @app.api_route("/api/fixture", methods=["GET", "POST"])
    async def fixture():
        return {"available": True}

    app.add_middleware(ApiKeyMiddleware, api_key="isolated-session")
    async with client(app) as http:
        for method in ("GET", "POST"):
            assert (await http.request(method, "/api/fixture")).status_code == 200
            assert (
                await http.request(method, "/api/fixture", headers={"X-API-Key": "wrong"})
            ).status_code == 401
