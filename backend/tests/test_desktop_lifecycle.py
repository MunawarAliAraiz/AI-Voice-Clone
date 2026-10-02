"""Graceful close fencing and provider cleanup acknowledgement, without spend."""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.api.deps import ApiKeyMiddleware
from app.api.routers import desktop_lifecycle, desktop_updates
from app.config import Settings


@pytest.fixture
def close_app(tmp_path, monkeypatch):
    class Jobs:
        calls = None

        def __init__(self):
            self.calls = []

        async def stop(self, *, drain_timeout_sec):
            self.calls.append(("stop", drain_timeout_sec))

        async def reap_stale(self):
            self.calls.append(("reap",))

        async def start(self):
            self.calls.append(("start",))

    class Cloud:
        def __init__(self):
            self.calls = 0
            self.compute = None
            self.cleanup_pending = False
            self.entered = asyncio.Event()
            self.finish = asyncio.Event()
            self.finish.set()
            self.error = False
            self.queue_paused = False

        async def pause_for_app_exit(self, *, queue_paused=False):
            self.calls += 1
            self.queue_paused = queue_paused
            if queue_paused:
                assert app.state.jobs.calls[-1][0] == "stop"
            self.entered.set()
            await self.finish.wait()
            if self.error:
                raise RuntimeError("private-provider-diagnostic")

        async def snapshot(self):
            return {"compute": self.compute, "cleanup_pending": self.cleanup_pending}

    app = FastAPI()
    app.state.settings = Settings(data_dir=tmp_path, desktop_static_dir=tmp_path / "assets")
    app.state.desktop_mutation_lock = asyncio.Lock()
    app.state.desktop_updating = False
    app.state.desktop_exiting = False
    app.state.desktop_exit_task = None
    app.state.desktop_exit_jobs_stopped = False
    app.state.owns_jobs = True
    app.state.jobs = Jobs()
    app.state.cloud = Cloud()
    monkeypatch.setattr(desktop_lifecycle, "controller", lambda _: app.state.cloud)
    app.include_router(desktop_lifecycle.router, prefix="/api")
    app.include_router(desktop_updates.router, prefix="/api")

    @app.post("/api/mutate")
    async def mutate():
        return {"accepted": True}

    @app.get("/api/read")
    async def read():
        return {"available": True}

    app.add_middleware(ApiKeyMiddleware, api_key="isolated-session")
    return app


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local",
                            headers={"X-API-Key": "isolated-session"})


@pytest.mark.asyncio
async def test_exit_stops_consumers_before_cloud_and_is_idempotent(close_app):
    async with client(close_app) as http:
        result = await http.post("/api/desktop/lifecycle/prepare-exit")
        assert result.json() == {"prepared": True, "stop_confirmed": True}
        assert close_app.state.jobs.calls == [("stop", 2)]
        assert close_app.state.cloud.calls == 1
        assert close_app.state.cloud.queue_paused
        assert (await http.post("/api/desktop/lifecycle/prepare-exit")).json() == result.json()
        assert close_app.state.cloud.calls == 1
        assert (await http.get("/api/read")).status_code == 200
        assert (await http.post("/api/mutate")).status_code == 409
        assert (await http.put("/api/desktop/lifecycle/cancel-exit")).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", ["compute", "cleanup_pending"])
async def test_uncertain_provider_stop_keeps_exit_fenced_and_can_retry(close_app, pending):
    setattr(close_app.state.cloud, pending, {"pod_id": "owned"} if pending == "compute" else True)
    async with client(close_app) as http:
        result = await http.post("/api/desktop/lifecycle/prepare-exit")
        assert result.status_code == 409
        assert "confirm" in result.json()["detail"]
        assert close_app.state.desktop_exiting
        setattr(close_app.state.cloud, pending, None if pending == "compute" else False)
        assert (await http.post("/api/desktop/lifecycle/prepare-exit")).json()["stop_confirmed"]
        assert close_app.state.cloud.calls == 2


@pytest.mark.asyncio
async def test_return_to_app_restores_queue_without_clearing_update_fence(close_app):
    async with client(close_app) as http:
        await http.post("/api/desktop/lifecycle/prepare-exit")
        close_app.state.desktop_updating = True
        result = await http.post("/api/desktop/lifecycle/cancel-exit")
        assert result.json() == {"prepared": False}
        assert not close_app.state.desktop_exiting
        assert close_app.state.desktop_updating
        assert close_app.state.jobs.calls == [("stop", 2), ("reap",), ("start",)]
        assert (await http.post("/api/mutate")).status_code == 409
        await http.post("/api/desktop/updates/cancel")
        assert (await http.post("/api/mutate")).status_code == 200


@pytest.mark.asyncio
async def test_provider_error_is_safe_and_return_to_app_is_available(close_app):
    close_app.state.cloud.error = True
    async with client(close_app) as http:
        result = await http.post("/api/desktop/lifecycle/prepare-exit")
        assert result.status_code == 503
        assert "private-provider" not in result.text
        assert (await http.post("/api/desktop/lifecycle/cancel-exit")).status_code == 200
        assert (await http.post("/api/mutate")).status_code == 200


@pytest.mark.asyncio
async def test_disconnected_exit_request_does_not_cancel_cloud_cleanup(close_app):
    cloud = close_app.state.cloud
    cloud.finish.clear()
    async with client(close_app) as http:
        request = asyncio.create_task(http.post("/api/desktop/lifecycle/prepare-exit"))
        await asyncio.wait_for(cloud.entered.wait(), 2)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert not close_app.state.desktop_exit_task.done()
        assert close_app.state.desktop_exiting
        # The HTTP disconnect releases admission, but does not undo its fence.
        assert (await http.post("/api/mutate")).status_code == 409
        assert (await http.post("/api/desktop/lifecycle/cancel-exit")).status_code == 409
        cloud.finish.set()
        result = await http.post("/api/desktop/lifecycle/prepare-exit")
        assert result.json()["stop_confirmed"]
        assert cloud.calls == 1


@pytest.mark.asyncio
async def test_close_requires_session_auth_and_desktop_mode(close_app):
    async with client(close_app) as http:
        for path in ("prepare-exit", "cancel-exit"):
            assert (await http.post(f"/api/desktop/lifecycle/{path}",
                                   headers={"X-API-Key": "wrong"})).status_code == 401
        assert not close_app.state.desktop_exiting
        close_app.state.settings.desktop_static_dir = None
        for path in ("prepare-exit", "cancel-exit"):
            assert (await http.post(f"/api/desktop/lifecycle/{path}")).status_code == 404


@pytest.mark.asyncio
async def test_update_fence_does_not_prevent_an_idle_app_exit(close_app):
    close_app.state.desktop_updating = True
    async with client(close_app) as http:
        assert (await http.post("/api/desktop/lifecycle/prepare-exit")).json()["prepared"]
        assert close_app.state.desktop_updating


@pytest.mark.asyncio
async def test_unowned_consumers_do_not_grant_queue_pause_override(close_app):
    close_app.state.owns_jobs = False
    async with client(close_app) as http:
        assert (await http.post("/api/desktop/lifecycle/prepare-exit")).status_code == 200
        assert not close_app.state.cloud.queue_paused
        assert close_app.state.jobs.calls == []
