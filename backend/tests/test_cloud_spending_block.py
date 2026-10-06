"""No-cost proof that rejected setup cannot create local or cloud work."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.deps import require_cloud_ready
from app.api.routers import runpod
from app.config import Settings
from app.runpod.client import POD_START_BLOCKED_REASON
from app.runpod.controller import CloudController


@pytest.mark.asyncio
@pytest.mark.parametrize("method,args", [
    ("purchase_storage", ("a" * 32,)), ("start_setup", ()),
    ("set_auto_setup", (True,)),
])
async def test_paid_setup_rejected_before_controller(tmp_path, monkeypatch, method, args):
    settings = Settings(data_dir=tmp_path, desktop_static_dir=tmp_path)
    monkeypatch.setattr(runpod, "controller", lambda _: pytest.fail("controller reached"))
    with pytest.raises(HTTPException) as error:
        await runpod._cloud(settings, method, *args)
    assert error.value.status_code == 409
    assert error.value.detail == POD_START_BLOCKED_REASON


@pytest.mark.asyncio
@pytest.mark.parametrize("method,args", [
    ("snapshot", ()), ("release", ()), ("cancel_setup", ()),
    ("set_auto_setup", (False,)),
])
async def test_reads_and_cleanup_still_reach_controller(tmp_path, monkeypatch, method, args):
    settings = Settings(data_dir=tmp_path, desktop_static_dir=tmp_path)
    calls = []
    async def allowed(*values):
        calls.append(values)
        return {"ok": True}
    monkeypatch.setattr(runpod, "controller", lambda _: SimpleNamespace(**{method: allowed}))
    assert await runpod._cloud(settings, method, *args) == {"ok": True}
    assert calls == [args]


@pytest.mark.asyncio
@pytest.mark.parametrize("files_ready", [False, True])
async def test_snapshot_separates_file_readiness_from_start_block(
    tmp_path, monkeypatch, files_ready,
):
    settings = Settings(data_dir=tmp_path)
    cloud = CloudController(settings)
    monkeypatch.setattr(cloud, "is_ready", lambda _: files_ready)
    value = await cloud.snapshot()
    assert value["ready"] is files_ready
    assert value["compute_start_blocked_reason"] == POD_START_BLOCKED_REASON
    assert value["compute"] is None


def test_web_generation_admission_is_unchanged():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=SimpleNamespace(desktop_static_dir=None))))
    assert require_cloud_ready(request) is None



@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["start", "restart"])
async def test_legacy_action_cannot_start_paid_compute(action):
    import httpx

    from app.runpod.client import RunpodApiError, RunpodClient

    requests = []
    def respond(request):
        requests.append(request)
        pytest.fail("Blocked action reached the provider")
    client = RunpodClient("test-only", transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(RunpodApiError) as error:
            await client.pod_action("existingpod", action)
        assert error.value.request_rejected is True
        assert error.value.args == (POD_START_BLOCKED_REASON,)
    finally:
        await client.close()
    assert requests == []


@pytest.mark.asyncio
async def test_legacy_stop_remains_available():
    import httpx

    from app.runpod.client import RunpodClient

    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "STOPPED"})
    client = RunpodClient("test-only", transport=httpx.MockTransport(respond))
    try:
        assert await client.pod_action("existingpod", "stop") == {"status": "STOPPED"}
    finally:
        await client.close()
    assert len(requests) == 1
    assert requests[0].method == "POST" and requests[0].url.path.endswith("/action")
    assert requests[0].content == b'{"action":"stop"}'



@pytest.mark.asyncio
async def test_paired_model_install_cannot_bypass_download_block(tmp_path, monkeypatch):
    monkeypatch.setattr(runpod, "_paired", lambda _: pytest.fail("Paired worker reached"))
    settings = Settings(data_dir=tmp_path, desktop_static_dir=tmp_path)
    with pytest.raises(HTTPException) as error:
        await runpod.install_worker_model("voxcpm2", settings)
    assert error.value.status_code == 409
    assert error.value.detail == POD_START_BLOCKED_REASON


@pytest.mark.asyncio
async def test_expired_local_timer_cannot_clear_unknown_machine(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.runpod.controller import CloudSetupError

    calls = []
    async def empty_list():
        calls.append("list")
        return []
    async def close():
        calls.append("close")
    cloud = CloudController(Settings(data_dir=tmp_path))
    cloud.client_factory = lambda _: SimpleNamespace(list_pods=empty_list, close=close)
    monkeypatch.setattr(cloud, "key", lambda: "test-only")
    compute = {"name": "owned-uncertain", "pod_id": None, "kind": "generation",
               "deadline": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
    cloud.write({"compute": compute, "cleanup_pending": True})
    with pytest.raises(CloudSetupError, match="expired local stop target"):
        await cloud._release()
    assert cloud.read()["compute"] == compute
    assert cloud.read()["cleanup_pending"] is True
    assert calls == ["list", "close"]
