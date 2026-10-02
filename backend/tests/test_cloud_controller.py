"""Billing and integrity failure paths without provider mutations."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from app.runpod import controller as module
from app.runpod.client import RunpodApiError, RunpodClient
from app.runpod.controller import CloudController, CloudSetupError, gpu_candidates, verified_models
from app.runpod.worker_pair import WorkerPair


def evidence():
    models = {}
    for model_id in module.REQUIRED_MODELS:
        files = [
            {
                "repo": repo,
                "revision": revision,
                "path": "model.safetensors",
                "size_bytes": 10,
                "sha256": "a" * 64,
            }
            for repo, revision in module.model_graph(model_id)
        ]
        models[model_id] = {
            "state": "installed",
            "files_total": len(files),
            "files_verified": len(files),
            "bytes_total": 10 * len(files),
            "bytes_completed": 10 * len(files),
            "evidence": {
                "version": 2,
                "model_id": model_id,
                "revision": files[0]["revision"],
                "bytes_total": 10 * len(files),
                "files": files,
            },
        }
    return {"manifest_id": module.release_manifest_id(), "ready": True, "models": models}


@pytest.mark.parametrize("defect", ["whisper", "counts", "hash", "revision", "manifest"])
def test_readiness_requires_complete_pinned_graph(defect):
    value = evidence()
    assert verified_models(value)
    item = value["models"]["omnivoice_urdu"]
    if defect == "whisper":
        item["evidence"]["files"].pop()
    elif defect == "counts":
        item["files_verified"] = 0
    elif defect == "hash":
        item["evidence"]["files"][0]["sha256"] = "z" * 64
    elif defect == "revision":
        item["evidence"]["files"][0]["revision"] = "0" * 40
    else:
        value["manifest_id"] = "old"
    assert not verified_models(value)


@pytest.mark.parametrize(
    "defect", ["root", "models", "model", "evidence", "files", "repo", "revision", "path"]
)
def test_malformed_readiness_evidence_fails_closed(defect):
    value = evidence()
    item = value["models"]["omnivoice_urdu"]
    if defect == "root":
        value = []
    elif defect == "models":
        value["models"] = []
    elif defect == "model":
        value["models"]["omnivoice_urdu"] = None
    elif defect == "evidence":
        item["evidence"] = []
    elif defect == "files":
        item["evidence"]["files"] = [None]
    else:
        item["evidence"]["files"][0][defect] = []
    assert not verified_models(value)


def test_gpu_selection_checks_regional_stock_memory_and_rate():
    def gpu(name, rate, region="EU", stock="HIGH", memory=48):
        return {
            "id": name,
            "memory": memory,
            "price": {"secure": rate},
            "dataCenters": [{"id": region, "availability": stock}],
        }

    values = [
        gpu("NVIDIA best", 0.49),
        gpu("NVIDIA cheaper elsewhere", 0.1, region="US"),
        gpu("NVIDIA too small", 0.1, memory=24),
        gpu("NVIDIA unavailable", 0.1, stock="NONE"),
        gpu("AMD cheap", 0.1),
        gpu("NVIDIA expensive", 4),
        gpu("NVIDIA second", 0.6),
    ]
    assert [g["id"] for g in gpu_candidates(values, "EU", 2)] == ["NVIDIA best", "NVIDIA second"]


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    class Keys:
        def __init__(self, path):
            pass

        def get_key(self):
            return "test-key"

        def has_key(self):
            return True

    monkeypatch.setattr(module, "RunpodKeyStore", Keys)
    result = CloudController(Settings(data_dir=tmp_path))
    monkeypatch.setattr(
        result,
        "images",
        lambda: {"gpu": "g@sha256:" + "a" * 64, "installer": "i@sha256:" + "b" * 64},
    )
    return result


@pytest.mark.parametrize("balance", [None, 0])
@pytest.mark.asyncio
async def test_storage_purchase_blocks_unknown_or_insufficient_balance(cloud, balance):
    class Provider:
        def __init__(self, key):
            pass

        async def balance(self):
            return {"balance_usd": balance}

        async def close(self):
            pass

        async def create_volume(self, **kwargs):
            pytest.fail("must not buy storage")

    cloud.client_factory = Provider
    cloud.quote = {
        "id": "quote",
        "expires_at": time.time() + 300,
        "required_credit_reserve_usd": 1.5,
        "region": "EU",
    }
    with pytest.raises(CloudSetupError):
        await cloud.purchase_storage("quote")
    assert not cloud.read().get("volume")


@pytest.mark.parametrize("status_code", [None, 408, 409, 500, 503])
@pytest.mark.asyncio
async def test_ambiguous_storage_purchase_never_replays_post(cloud, status_code):
    attempts = []

    class Provider:
        def __init__(self, key):
            pass

        async def balance(self):
            return {"balance_usd": 10}

        async def list_volumes(self):
            return []

        async def close(self):
            pass

        async def create_volume(self, **kwargs):
            attempts.append(kwargs)
            raise RunpodApiError("uncertain failure", status_code)

    cloud.client_factory = Provider
    cloud.quote = {
        "id": "quote",
        "expires_at": time.time() + 300,
        "required_credit_reserve_usd": 1.5,
        "region": "EU",
    }
    with pytest.raises(RunpodApiError):
        await cloud.purchase_storage("quote")
    assert cloud.read()["volume_operation_sent"]
    # Refreshing approval cannot lift the uncertainty fence.
    cloud.quote["id"] = "fresh-quote"
    with pytest.raises(CloudSetupError, match="uncertain"):
        await cloud.purchase_storage("fresh-quote")
    assert len(attempts) == 1


@pytest.mark.parametrize("status_code", [400, 401, 402, 403, 422, 429])
@pytest.mark.asyncio
async def test_rejected_storage_purchase_can_refresh_requote_and_retry(
    cloud, monkeypatch, status_code
):
    attempts = []
    balance = 10

    class Provider:
        def __init__(self, key):
            pass

        async def balance(self):
            return {"balance_usd": balance}

        async def list_volumes(self):
            return []

        async def close(self):
            pass

        async def create_volume(self, **kwargs):
            attempts.append(kwargs)
            if len(attempts) == 1:
                raise RunpodApiError("request rejected", status_code)
            return {
                "id": "created-volume",
                "name": kwargs["name"],
                "dataCenter": kwargs["data_center"],
                "size": kwargs["size_gb"],
                "type": "STANDARD",
            }

    async def discover():
        return {"balance_usd": balance, "regions": [{"id": "EU"}]}

    cloud.client_factory = Provider
    monkeypatch.setattr(cloud, "discover", discover)
    first_quote = await cloud.quote_storage("EU")
    with pytest.raises(CloudSetupError, match="refresh the storage quote"):
        await cloud.purchase_storage(first_quote["id"])
    assert cloud.read()["volume_operation_sent"] is False
    assert cloud.quote is None
    assert not cloud.read().get("volume")
    with pytest.raises(CloudSetupError, match="expired"):
        await cloud.purchase_storage(first_quote["id"])
    assert len(attempts) == 1

    # A new discovery/quote reflects the user's newly available funds and
    # requires a new approval ID before a second POST is possible.
    balance = 20
    fresh_quote = await cloud.quote_storage("EU")
    assert fresh_quote["balance_usd"] == 20
    assert fresh_quote["id"] != first_quote["id"]
    result = await cloud.purchase_storage(fresh_quote["id"])
    assert result["id"] == "created-volume"
    assert len(attempts) == 2
    assert attempts[1]["name"] == attempts[0]["name"]


@pytest.mark.asyncio
async def test_ambiguous_purchase_adopts_reconciled_volume_without_second_post(cloud):
    created = []

    class Provider:
        def __init__(self, key):
            pass

        async def balance(self):
            return {"balance_usd": 10}

        async def list_volumes(self):
            return created

        async def close(self):
            pass

        async def create_volume(self, **kwargs):
            assert not created, "reconciliation must avoid a second purchase"
            created.append(
                {
                    "id": "created-despite-lost-response",
                    "name": kwargs["name"],
                    "dataCenter": kwargs["data_center"],
                    "size": kwargs["size_gb"],
                    "type": "STANDARD",
                }
            )
            raise RunpodApiError("lost response", 503)

    cloud.client_factory = Provider
    cloud.quote = {
        "id": "quote",
        "expires_at": time.time() + 300,
        "required_credit_reserve_usd": 1.5,
        "region": "EU",
    }
    with pytest.raises(RunpodApiError):
        await cloud.purchase_storage("quote")
    assert cloud.read()["volume_operation_sent"]
    result = await cloud.purchase_storage("quote")
    assert result["id"] == "created-despite-lost-response"
    assert cloud.read()["volume"]["id"] == result["id"]
    assert len(created) == 1


@pytest.mark.asyncio
async def test_session_finally_releases_admission_after_transport_shutdown_failure(
    cloud, monkeypatch
):
    class PairStore:
        def __init__(self, path):
            pass

        def get(self):
            return WorkerPair("abcdef123", "x" * 32)

    class Remote:
        def __init__(self, *args):
            pass

        async def shutdown(self):
            raise OSError("transport shutdown failed")

    monkeypatch.setattr(module, "WorkerPairStore", PairStore)
    cloud.remote_factory = Remote
    cloud.write(
        {
            "ready": True,
            "evidence_version": 2,
            "manifest_id": module.release_manifest_id(),
            "compute": {"pod_id": "abcdef123"},
        }
    )
    with pytest.raises(OSError):
        async with cloud.session():
            assert cloud.active == 1
    assert cloud.active == 0
    assert cloud.release_task is not None
    cloud.release_task.cancel()
    await asyncio.gather(cloud.release_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_pod_deadline_is_atomic_with_creation_and_secret_not_in_url():
    seen = []

    def respond(request):
        assert request.url.query == b""
        body = json.loads(request.content)
        seen.append(body["variables"]["input"])
        return httpx.Response(
            200, json={"data": {"podFindAndDeployOnDemand": {"id": "abcdef123", "costPerHr": 0.49}}}
        )

    client = RunpodClient("secret", transport=httpx.MockTransport(respond))
    deadline = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    try:
        await client.create_guarded_pod(
            name="owned",
            image="image@sha256:" + "a" * 64,
            data_center="EU",
            volume_id="volume",
            worker_token="w" * 32,
            terminate_at=deadline,
            gpu_id="NVIDIA A40",
        )
    finally:
        await client.close()
    assert seen[0]["terminateAfter"] == deadline
    assert seen[0]["networkVolumeId"] == "volume"
    assert seen[0]["computeType"] == "GPU"


@pytest.mark.asyncio
async def test_interrupted_installer_is_failed_not_downloading(cloud):
    cloud.write({"volume": {"id": "storage"}, "compute": {
        "kind": "installer", "pod_id": None, "status": "provisioning"}})
    snapshot = await cloud.snapshot()
    assert snapshot["setup_phase"] == "failed"
    assert snapshot["setup_running"] is False
    assert snapshot["setup_error"]
    assert snapshot["cleanup_pending"] is True
    assert snapshot["compute"]["creation_confirmed"] is False
    assert snapshot["progress_pct"] is None


@pytest.mark.asyncio
async def test_failed_setup_error_survives_controller_restart(cloud, monkeypatch):
    async def fail(**kwargs):
        raise CloudSetupError("Worker could not start")

    monkeypatch.setattr(cloud, "_provision", fail)
    await cloud._setup()
    recovered = CloudController(cloud.settings)
    snapshot = await recovered.snapshot()
    assert snapshot["setup_phase"] == "failed"
    assert snapshot["setup_error"] == "Worker could not start"
    assert snapshot["setup_running"] is False
    assert snapshot["cleanup_pending"] is False


@pytest.mark.asyncio
async def test_verified_setup_only_reports_ready_after_worker_stops(cloud, monkeypatch):
    async def provision(**kwargs):
        cloud.write({"compute": {"kind": "installer", "pod_id": "abcdef123"}})
        return WorkerPair("abcdef123", "w" * 32)

    async def worker(*args):
        return evidence()

    async def release():
        snapshot = await cloud.snapshot()
        assert snapshot["setup_phase"] == "stopping_worker"
        assert snapshot["ready"] is False
        state = cloud.read()
        state.pop("compute")
        cloud.write(state)

    monkeypatch.setattr(cloud, "_provision", provision)
    monkeypatch.setattr(cloud, "_worker_call", worker)
    monkeypatch.setattr(cloud, "_release", release)
    cloud.setup_task = asyncio.create_task(cloud._setup())
    await cloud.setup_task
    snapshot = await cloud.snapshot()
    assert snapshot["setup_phase"] == "ready"
    assert snapshot["ready"] is True
    assert snapshot["compute"] is None
    assert snapshot["setup_error"] is None


@pytest.mark.parametrize("code,rejected", [
    ("GRAPHQL_VALIDATION_FAILED", True), ("FORBIDDEN", False), (None, False)])
@pytest.mark.asyncio
async def test_graphql_rejection_is_classified_without_exposing_provider_body(code, rejected):
    def respond(request):
        return httpx.Response(200, json={"errors": [{
            "message": "private-provider-details", "extensions": {"code": code}}]})

    client = RunpodClient("private-key", transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(RunpodApiError) as failure:
            await client.graphql("query { myself { clientBalance } }")
        assert failure.value.request_rejected is rejected
        assert "private-provider-details" not in str(failure.value)
        assert "private-key" not in str(failure.value)
        if code is None:
            assert "API key" not in str(failure.value)
    finally:
        await client.close()


@pytest.mark.parametrize("rejected", [True, False])
@pytest.mark.asyncio
async def test_deployment_rejection_clears_only_proven_pre_execution_failures(cloud, rejected):
    attempts = []

    class Provider:
        def __init__(self, key):
            pass

        async def list_gpu_types(self):
            return [{"id": "NVIDIA test", "memory": 48, "price": {"secure": 1.0},
                     "dataCenters": [{"id": "EU", "availability": "HIGH"}]}]

        async def balance(self):
            return {"balance_usd": 9.0}

        async def create_guarded_pod(self, **kwargs):
            attempts.append(kwargs)
            raise RunpodApiError("Deployment failed", request_rejected=rejected)

        async def close(self):
            pass

    cloud.client_factory = Provider
    cloud.write({"volume": {"id": "volume", "dataCenter": "EU"},
                 "policy": {"max_session_usd": 1.0, "max_hourly_usd": 2.0}})
    with pytest.raises(RunpodApiError):
        await cloud._provision(installer=False)
    assert bool(cloud.read().get("compute")) is not rejected
    if not rejected:
        with pytest.raises(CloudSetupError, match="cleanup"):
            await cloud._provision(installer=False)
        assert len(attempts) == 1


@pytest.mark.asyncio
async def test_restart_before_deployment_record_shows_retryable_error(cloud):
    cloud._setup_status("starting_worker")
    snapshot = await cloud.snapshot()
    assert snapshot["setup_phase"] == "failed"
    assert snapshot["setup_running"] is False
    assert snapshot["setup_error"]
    assert snapshot["compute"] is None
    assert snapshot["cleanup_pending"] is False


@pytest.mark.asyncio
async def test_installer_cleanup_failure_cannot_unlock_generation(cloud, monkeypatch):
    async def provision(**kwargs):
        cloud.write({"compute": {"kind": "installer", "pod_id": "abcdef123"}})
        return WorkerPair("abcdef123", "w" * 32)

    async def worker(*args):
        return evidence()

    async def release():
        raise CloudSetupError("Termination not confirmed")

    monkeypatch.setattr(cloud, "_provision", provision)
    monkeypatch.setattr(cloud, "_worker_call", worker)
    monkeypatch.setattr(cloud, "_release", release)
    cloud.setup_task = asyncio.create_task(cloud._setup())
    await cloud.setup_task
    snapshot = await cloud.snapshot()
    assert snapshot["setup_phase"] == "failed"
    assert snapshot["ready"] is False
    assert snapshot["cleanup_pending"] is True
    assert snapshot["compute"]["creation_confirmed"] is True
