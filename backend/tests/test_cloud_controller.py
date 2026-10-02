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
from app.runpod.controller import (
    CloudController,
    CloudSetupError,
    gpu_candidates,
    installer_candidates,
    verified_models,
)
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
    return {
        "manifest_id": module.release_manifest_id(),
        "ready": True,
        "models": models,
        "state": "complete",
        "scan_complete": True,
        "free_bytes": 100_000_000_000,
        "required_free_bytes": 10_000_000_000,
        "sufficient": True,
        "app_owned": True,
    }


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
    cloud.write(
        {
            "volume": {"id": "storage"},
            "compute": {"kind": "installer", "pod_id": None, "status": "provisioning"},
        }
    )
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


@pytest.mark.parametrize(
    "code,rejected", [("GRAPHQL_VALIDATION_FAILED", True), ("FORBIDDEN", False), (None, False)]
)
@pytest.mark.asyncio
async def test_graphql_rejection_is_classified_without_exposing_provider_body(code, rejected):
    def respond(request):
        return httpx.Response(
            200,
            json={
                "errors": [{"message": "private-provider-details", "extensions": {"code": code}}]
            },
        )

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
            return [
                {
                    "id": "NVIDIA test",
                    "memory": 48,
                    "price": {"secure": 1.0},
                    "dataCenters": [{"id": "EU", "availability": "HIGH"}],
                }
            ]

        async def balance(self):
            return {"balance_usd": 9.0}

        async def create_guarded_pod(self, **kwargs):
            attempts.append(kwargs)
            raise RunpodApiError("Deployment failed", request_rejected=rejected)

        async def close(self):
            pass

    cloud.client_factory = Provider
    cloud.write(
        {
            "volume": {"id": "volume", "dataCenter": "EU"},
            "policy": {"max_session_usd": 1.0, "max_hourly_usd": 2.0},
        }
    )
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


def cancellation_provider(cloud, monkeypatch, *, empty=False, deny=False, renamed=False):
    calls = []
    stopped = False

    class Provider:
        def __init__(self, key):
            pass

        async def list_pods(self):
            calls.append("list")
            return [] if empty else [{"id": "abcdef123", "name": "owned-installer"}]

        async def get_pod(self, pod_id):
            if stopped:
                raise RunpodApiError("gone", 404)
            return {"id": pod_id, "name": "other-project" if renamed else "owned-installer"}

        async def terminate_pod(self, pod_id):
            nonlocal stopped
            calls.append(("delete", pod_id))
            if deny:
                raise RunpodApiError("denied", 403)
            stopped = True

        async def close(self):
            pass

    class Pairs:
        def __init__(self, path):
            pass

        def clear(self):
            calls.append("clear-pair")

    cloud.client_factory = Provider
    monkeypatch.setattr(module, "WorkerPairStore", Pairs)
    return calls


def pending_installer(*, pod_id=None):
    return {
        "kind": "installer",
        "name": "owned-installer",
        "pod_id": pod_id,
        "status": "provisioning",
        "deadline": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }


@pytest.mark.asyncio
async def test_cancel_quiesces_downloading_and_keeps_storage_models_and_partial_files(
    cloud, monkeypatch
):
    calls = cancellation_provider(cloud, monkeypatch)
    cloud.write(
        {"volume": {"id": "persistent-volume"}, "compute": pending_installer(pod_id="abcdef123")}
    )
    partial = cloud.settings.data_dir / "model.vcs-incomplete"
    partial.write_bytes(b"stored downloaded bytes")
    cloud.progress = {
        "models": {
            "done": {"state": "installed"},
            "active": {"state": "downloading", "bytes_completed": 12, "bytes_total": 20},
        }
    }
    running = asyncio.Event()
    quiescent = asyncio.Event()

    async def downloading():
        running.set()
        try:
            await asyncio.Event().wait()
        finally:
            quiescent.set()

    cloud.setup_task = asyncio.create_task(downloading())
    await running.wait()
    snapshot = await cloud.cancel_setup()
    assert quiescent.is_set()
    assert snapshot["setup_phase"] == "cancelled"
    assert snapshot["setup_running"] is False
    assert snapshot["cleanup_pending"] is False
    assert snapshot["compute"] is None
    assert snapshot["volume"]["id"] == "persistent-volume"
    assert snapshot["models"]["done"]["state"] == "installed"
    assert snapshot["models"]["active"]["state"] == "cancelled"
    assert snapshot["models"]["active"]["bytes_completed"] == 12
    assert partial.read_bytes() == b"stored downloaded bytes"
    assert calls.count(("delete", "abcdef123")) == 1
    recovered = await CloudController(cloud.settings).snapshot()
    assert recovered["setup_phase"] == "cancelled"
    assert recovered["models"]["active"]["bytes_completed"] == 12


@pytest.mark.asyncio
async def test_cancel_during_ambiguous_create_reconciles_late_owned_pod(cloud, monkeypatch):
    calls = cancellation_provider(cloud, monkeypatch)
    entered = asyncio.Event()

    async def provision(**kwargs):
        state = cloud.read()
        state["compute"] = pending_installer()
        cloud.write(state)
        entered.set()
        await asyncio.Event().wait()  # Provider created it; HTTP response never arrived.

    monkeypatch.setattr(cloud, "_provision", provision)
    cloud.write({"volume": {"id": "persistent-volume"}})
    await cloud.start_setup()
    await entered.wait()
    snapshot = await asyncio.wait_for(cloud.cancel_setup(), timeout=2)
    assert snapshot["compute"] is None
    assert snapshot["setup_phase"] == "cancelled"
    assert calls.count(("delete", "abcdef123")) == 1


@pytest.mark.parametrize("mode", ["absent", "delete_denied", "renamed"])
@pytest.mark.asyncio
async def test_cancel_unknown_or_unstoppable_pod_retains_cleanup_fence(cloud, monkeypatch, mode):
    calls = cancellation_provider(
        cloud,
        monkeypatch,
        empty=mode == "absent",
        deny=mode == "delete_denied",
        renamed=mode == "renamed",
    )
    cloud.write({"volume": {"id": "storage"}, "compute": pending_installer()})
    snapshot = await cloud.cancel_setup()
    assert snapshot["setup_phase"] == "cancelled"
    assert snapshot["ready"] is False
    assert snapshot["cleanup_pending"] is True
    assert snapshot["compute"]
    assert "not confirmed" in snapshot["setup_error"]
    if mode != "delete_denied":
        assert not any(isinstance(call, tuple) and call[0] == "delete" for call in calls)
    with pytest.raises(CloudSetupError, match="pending machine"):
        await cloud.start_setup()


@pytest.mark.asyncio
async def test_cancel_never_stops_generation_compute(cloud):
    cloud.write({"compute": {"kind": "generation", "pod_id": "another123"}, "ready": True})
    with pytest.raises(CloudSetupError, match="generation is active"):
        await cloud.cancel_setup()
    assert cloud.read()["compute"]["pod_id"] == "another123"


@pytest.mark.parametrize("status", [200, 400])
@pytest.mark.asyncio
async def test_graphql_schema_rejection_from_http_400_or_200_is_proven(status):
    client = RunpodClient(
        "private-key",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                status,
                json={
                    "errors": [
                        {
                            "message": "private details",
                            "extensions": {"code": "GRAPHQL_VALIDATION_FAILED"},
                        }
                    ]
                },
            )
        ),
    )
    try:
        with pytest.raises(RunpodApiError) as failure:
            await client.graphql("mutation { example }")
        assert failure.value.request_rejected is True
        assert "private details" not in str(failure.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_unclassified_http_400_resolver_error_never_drops_creation_fence():
    client = RunpodClient(
        "private-key",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(400, json={"errors": [{"message": "private details"}]})
        ),
    )
    try:
        with pytest.raises(RunpodApiError) as failure:
            await client.graphql("mutation { example }")
        assert failure.value.request_rejected is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cancel_cleanup_survives_request_disconnect_and_concurrent_stop(cloud, monkeypatch):
    cloud.write({"volume": {"id": "storage"}, "compute": pending_installer()})
    entered, finish = asyncio.Event(), asyncio.Event()
    releases = []

    async def release():
        releases.append(True)
        entered.set()
        await finish.wait()
        state = cloud.read()
        state.pop("compute")
        cloud.write(state)

    monkeypatch.setattr(cloud, "_release", release)
    first_request = asyncio.create_task(cloud.cancel_setup())
    await entered.wait()
    assert (await cloud.snapshot())["setup_phase"] == "cancelling"
    first_request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_request
    assert cloud.cancel_task and not cloud.cancel_task.done()
    second_request = asyncio.create_task(cloud.cancel_setup())
    finish.set()
    snapshot = await second_request
    assert snapshot["setup_phase"] == "cancelled"
    assert snapshot["compute"] is None
    assert len(releases) == 1


@pytest.mark.asyncio
async def test_installer_uses_cpu_stock_without_requiring_gpu_stock(cloud):
    creations = []

    class Provider:
        def __init__(self, key):
            pass

        async def list_cpu_types(self):
            return [
                {
                    "id": "cpu3c",
                    "ramGbPerVcpu": 2,
                    "price": {"securePerVcpu": 0.03},
                    "vcpu": {"min": 2, "max": 32},
                    "dataCenters": [{"id": "EU", "availability": "HIGH"}],
                }
            ]

        async def list_gpu_types(self):
            pytest.fail("GPU stock is irrelevant when downloading model files")

        async def balance(self):
            return {"balance_usd": 9.0}

        async def create_guarded_pod(self, **kwargs):
            creations.append(kwargs)
            raise RunpodApiError("fixture rejection", request_rejected=True)

        async def close(self):
            pass

    cloud.client_factory = Provider
    cloud.write({"volume": {"id": "volume", "dataCenter": "EU"}})
    with pytest.raises(RunpodApiError):
        await cloud._provision(installer=True)
    assert len(creations) == 1
    assert creations[0]["gpu_id"] is None
    assert creations[0]["cpu_instance_id"] == "cpu3c-2-4"
    deadline = datetime.fromisoformat(creations[0]["terminate_at"])
    seconds = (deadline - datetime.now(UTC)).total_seconds()
    assert 7100 < seconds <= 7200
    assert cloud.read()["auto_setup_attempt_consumed"] is True
    assert cloud.read().get("compute") is None


def test_installer_candidates_quote_exact_instance_and_skip_ineligible_configurations():
    def cpu(flavor, ratio=2, count=2, maximum=32, stock="HIGH", region="EU", price=0.03):
        return {
            "id": flavor,
            "ramGbPerVcpu": ratio,
            "vcpu": {"min": count, "max": maximum},
            "price": {"securePerVcpu": price},
            "dataCenters": [{"id": region, "availability": stock}],
        }

    rows = [
        cpu("cpu3c"),
        cpu("cpu3g", ratio=4, price=0.04),
        cpu("wrongregion", region="US"),
        cpu("unavailable", stock="NONE"),
        cpu("toosmall", ratio=1),
        cpu("overmax", maximum=1),
        cpu("badprice", price=float("nan")),
        cpu("invalid-id!"),
        cpu("missingratio", ratio=None),
    ]
    assert installer_candidates(rows, "EU") == [
        {"instance_id": "cpu3c-2-4", "hourly_usd": 0.06, "vcpu": 2, "memory_gb": 4},
        {"instance_id": "cpu3g-2-8", "hourly_usd": 0.08, "vcpu": 2, "memory_gb": 8},
    ]


@pytest.mark.asyncio
async def test_cpu_creation_uses_dedicated_mutation_exact_instance_and_atomic_deadline():
    requests = []

    def respond(request):
        assert request.url.query == b""
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200, json={"data": {"deployCpuPod": {"id": "abcdef123", "costPerHr": 0.06}}}
        )

    client = RunpodClient("hidden-token", transport=httpx.MockTransport(respond))
    deadline = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    try:
        pod = await client.create_guarded_pod(
            name="owned-installer",
            image="image@sha256:" + "b" * 64,
            data_center="EU",
            volume_id="persistent-volume",
            worker_token="w" * 32,
            terminate_at=deadline,
            cpu_instance_id="cpu3c-2-4",
        )
    finally:
        await client.close()
    assert pod["costPerHr"] == 0.06
    assert "deployCpuPodInput!" in requests[0]["query"]
    assert "deployCpuPod(input:" in requests[0]["query"]
    body = requests[0]["variables"]["input"]
    assert body["instanceId"] == "cpu3c-2-4"
    assert body["terminateAfter"] == deadline
    assert body["networkVolumeId"] == "persistent-volume"
    assert body["imageName"] == "image@sha256:" + "b" * 64
    excluded = {"gpuCount", "computeType", "gpuTypeId", "minVcpuCount", "minMemoryInGb"}
    assert not excluded & body.keys()


@pytest.mark.asyncio
async def test_cpu_creation_requires_explicit_instance_before_sending_any_request():
    def respond(request):
        pytest.fail("must not send a deployment without a qualified CPU configuration")

    client = RunpodClient("hidden-token", transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ValueError, match="quoted instance"):
            await client.create_guarded_pod(
                name="owned",
                image="image@sha256:" + "b" * 64,
                data_center="EU",
                volume_id="volume",
                worker_token="w" * 32,
                terminate_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_auto_setup_retries_only_pre_creation_availability_then_verifies(cloud, monkeypatch):
    attempts, waits = [], []

    async def provision(**kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise module.CloudAvailabilityError("Waiting for a download machine")
        return WorkerPair("abcdef123", "w" * 32)

    async def worker(*args):
        return evidence()

    async def release():
        pass

    async def wait(seconds):
        waits.append(seconds)
        state = cloud.read()
        assert state["auto_setup_waiting"] is True
        assert datetime.fromisoformat(state["auto_setup_retry_at"]).tzinfo is not None
        assert not state.get("compute")

    monkeypatch.setattr(cloud, "_provision", provision)
    monkeypatch.setattr(cloud, "_worker_call", worker)
    monkeypatch.setattr(cloud, "_release", release)
    monkeypatch.setattr(module.asyncio, "sleep", wait)
    cloud.write({"volume": {"id": "volume"}, "auto_setup_enabled": True})
    await cloud._auto_setup()
    assert len(attempts) == 2
    assert waits == [60]
    assert (await cloud.snapshot())["ready"] is True


@pytest.mark.asyncio
async def test_auto_setup_paid_failure_never_retries_without_explicit_resume(cloud, monkeypatch):
    attempts = []

    async def provision(**kwargs):
        attempts.append(True)
        state = cloud.read()
        state.update(
            compute=pending_installer(pod_id="abcdef123"), auto_setup_attempt_consumed=True
        )
        cloud.write(state)
        raise CloudSetupError("Worker failed after creation")

    async def release():
        state = cloud.read()
        state["last_compute"] = state.pop("compute")
        cloud.write(state)

    monkeypatch.setattr(cloud, "_provision", provision)
    monkeypatch.setattr(cloud, "_release", release)
    cloud.write({"volume": {"id": "volume"}, "auto_setup_enabled": True})
    await cloud._auto_setup()
    assert len(attempts) == 1
    snapshot = await cloud.snapshot()
    assert snapshot["auto_setup_enabled"] is False
    assert snapshot["auto_setup_requires_resume"] is True
    await cloud.resume_auto_setup()
    assert cloud.auto_task is None


@pytest.mark.asyncio
async def test_cancel_disables_auto_waiting_intent_after_restart(cloud):
    cloud.write(
        {"volume": {"id": "volume"}, "auto_setup_enabled": True, "auto_setup_waiting": True}
    )
    cloud.auto_task = asyncio.create_task(asyncio.Event().wait())
    snapshot = await cloud.cancel_setup()
    assert snapshot["auto_setup_enabled"] is False
    assert snapshot["auto_setup_waiting"] is False
    assert snapshot["auto_setup_retry_at"] is None
    recovered = CloudController(cloud.settings)
    await recovered.resume_auto_setup()
    assert recovered.auto_task is None


@pytest.mark.asyncio
async def test_legacy_no_machine_record_allows_explicit_auto_intent_migration(cloud):
    cloud.write(
        {
            "volume": {"id": "volume"},
            "last_compute": {"pod_id": None, "status": "cancelled_no_machine_found"},
        }
    )
    snapshot = await cloud.snapshot()
    assert snapshot["auto_setup_enabled"] is None
    assert snapshot["auto_setup_requires_resume"] is False


@pytest.mark.asyncio
async def test_storage_quote_uses_measured_minimum_custom_size_name_and_tier_price(
    cloud, monkeypatch
):
    async def discover():
        return {"balance_usd": 20, "regions": [{"id": "EU"}]}

    monkeypatch.setattr(cloud, "discover", discover)
    quote = await cloud.quote_storage("EU", 60, "My voice models")
    assert quote["storage_gb"] == 60
    assert quote["monthly_usd"] == 4.2
    assert quote["storage_name"] == "My voice models"
    assert quote["model_files_bytes"] + quote["reserve_bytes"] <= 60_000_000_000
    assert (await cloud.quote_storage("EU", 2000))["monthly_usd"] == 120
    with pytest.raises(CloudSetupError, match="60"):
        await cloud.quote_storage("EU", 59)
    monkeypatch.setattr(module, "CAPACITY_MANIFEST_ID", "outdated")
    with pytest.raises(CloudSetupError, match="requirements changed"):
        await cloud.quote_storage("EU")


@pytest.mark.asyncio
async def test_storage_choice_requires_explicit_other_app_selection_and_uses_isolated_cache(cloud):
    class Provider:
        def __init__(self, key):
            pass

        async def list_volumes(self):
            return [
                {
                    "id": "video",
                    "name": "video-models",
                    "size": 60,
                    "type": "STANDARD",
                    "dataCenter": "EU",
                }
            ]

        async def close(self):
            pass

    cloud.client_factory = Provider
    with pytest.raises(CloudSetupError, match="Advanced"):
        await cloud.select_storage("video")
    selected = await cloud.select_storage("video", True)
    assert selected["cache_namespace"] == "isolated"
    assert selected["app_owned"] is False
    assert selected["explicitly_selected"] is True
    assert (await cloud.snapshot())["storage_min_gb"] == 60


@pytest.mark.asyncio
async def test_advanced_new_storage_keeps_old_volume_and_binds_replacement_quote(
    cloud, monkeypatch
):
    created = []

    class Provider:
        def __init__(self, key):
            pass

        async def balance(self):
            return {"balance_usd": 20}

        async def list_volumes(self):
            return []

        async def create_volume(self, **kwargs):
            created.append(kwargs)
            return {
                "id": "new",
                "size": kwargs["size_gb"],
                "name": kwargs["name"],
                "dataCenter": kwargs["data_center"],
                "type": "STANDARD",
            }

        async def close(self):
            pass

    async def discover():
        return {"balance_usd": 20, "regions": [{"id": "EU"}]}

    cloud.client_factory = Provider
    monkeypatch.setattr(cloud, "discover", discover)
    cloud.write({"volume": {"id": "old", "size": 200}, "ready": True})
    quote = await cloud.quote_storage("EU", 80, "New voice storage")
    with pytest.raises(CloudSetupError, match="already selected"):
        await cloud.purchase_storage(quote["id"])
    quote = await cloud.quote_storage("EU", 80, "New voice storage", True)
    result = await cloud.purchase_storage(quote["id"])
    assert result["id"] == "new"
    assert created == [{"name": "New voice storage", "data_center": "EU", "size_gb": 80}]
    assert not cloud.read()["ready"]


@pytest.mark.asyncio
async def test_legacy_completed_purchase_never_adopts_old_200gb_for_new_60gb_quote(
    cloud, monkeypatch
):
    old = {
        "id": "old",
        "name": "voice-clone-legacy",
        "size": 200,
        "dataCenter": "EU",
        "type": "STANDARD",
    }
    created = []

    class Provider:
        def __init__(self, key):
            pass

        async def balance(self):
            return {"balance_usd": 20}

        async def list_volumes(self):
            return [old]

        async def create_volume(self, **kwargs):
            created.append(kwargs)
            return {
                "id": "new",
                "name": kwargs["name"],
                "size": kwargs["size_gb"],
                "dataCenter": kwargs["data_center"],
                "type": "STANDARD",
            }

        async def close(self):
            pass

    async def discover():
        return {"balance_usd": 20, "regions": [{"id": "EU"}]}

    cloud.client_factory = Provider
    monkeypatch.setattr(cloud, "discover", discover)
    cloud.write(
        {
            "volume": old,
            "volume_operation": old["name"],
            "volume_operation_sent": True,
            "ready": True,
        }
    )
    quote = await cloud.quote_storage("EU", 60, "New dedicated voice storage", True)
    selected = await cloud.purchase_storage(quote["id"])
    assert selected["id"] == "new"
    assert selected["size"] == 60
    assert created == [{"name": "New dedicated voice storage", "data_center": "EU", "size_gb": 60}]
    assert old["size"] == 200
    assert not cloud.read().get("volume_operation")


@pytest.mark.parametrize("changed", ["storage_name", "region", "storage_gb", "previous_volume_id"])
@pytest.mark.asyncio
async def test_ambiguous_storage_operation_cannot_be_adopted_for_changed_quote(cloud, changed):
    created = []

    class Provider:
        def __init__(self, key):
            pass

        async def balance(self):
            return {"balance_usd": 20}

        async def list_volumes(self):
            return created

        async def create_volume(self, **kwargs):
            created.append(
                {
                    "id": "uncertain-new",
                    "name": kwargs["name"],
                    "size": kwargs["size_gb"],
                    "dataCenter": kwargs["data_center"],
                    "type": "STANDARD",
                }
            )
            raise RunpodApiError("lost response", 503)

        async def close(self):
            pass

    cloud.client_factory = Provider
    cloud.quote = {
        "id": "first",
        "expires_at": time.time() + 300,
        "required_credit_reserve_usd": 1.5,
        "region": "EU",
        "storage_gb": 60,
        "storage_name": "first-storage",
        "previous_volume_id": None,
    }
    with pytest.raises(RunpodApiError):
        await cloud.purchase_storage("first")
    original = cloud.read()
    cloud.quote = {
        **cloud.quote,
        "id": "second",
        changed: {
            "storage_name": "other-storage",
            "region": "US",
            "storage_gb": 80,
            "previous_volume_id": "other-volume",
        }[changed],
    }
    with pytest.raises(CloudSetupError, match="earlier storage purchase is uncertain"):
        await cloud.purchase_storage("second")
    assert cloud.read()["volume_operation_request"] == original["volume_operation_request"]
    assert cloud.read()["volume_operation_sent"] is True
    assert not cloud.read().get("volume")
    assert len(created) == 1


@pytest.mark.asyncio
async def test_unconfirmed_legacy_purchase_fence_is_not_migrated_from_name_alone(cloud):
    old = {
        "id": "other",
        "name": "voice-clone-legacy",
        "size": 200,
        "dataCenter": "EU",
        "type": "STANDARD",
    }

    class Provider:
        def __init__(self, key):
            pass

        async def balance(self):
            return {"balance_usd": 20}

        async def list_volumes(self):
            return [old]

        async def create_volume(self, **kwargs):
            pytest.fail("Unknown legacy purchase must not be replayed")

        async def close(self):
            pass

    cloud.client_factory = Provider
    cloud.write(
        {
            "volume": {**old, "id": "selected-different-id"},
            "volume_operation": old["name"],
            "volume_operation_sent": True,
        }
    )
    cloud.quote = {
        "id": "new",
        "expires_at": time.time() + 300,
        "required_credit_reserve_usd": 1.5,
        "region": "EU",
        "storage_gb": 60,
        "replace_current": True,
        "previous_volume_id": "selected-different-id",
    }
    with pytest.raises(CloudSetupError, match="earlier storage purchase is uncertain"):
        await cloud.purchase_storage("new")
    assert cloud.read()["volume_operation_sent"] is True
    assert cloud.read()["volume_operation"] == old["name"]


@pytest.mark.asyncio
async def test_app_exit_pause_preserves_intent_and_resumes_after_confirmed_cleanup(
    cloud, monkeypatch
):
    cloud.write(
        {
            "volume": {"id": "volume"},
            "auto_setup_enabled": True,
            "auto_setup_attempt_consumed": True,
            "setup_phase": "downloading",
            "compute": pending_installer(),
        }
    )

    async def release():
        state = cloud.read()
        state["last_compute"] = state.pop("compute")
        state["cleanup_pending"] = False
        cloud.write(state)

    monkeypatch.setattr(cloud, "_release", release)
    snapshot = await cloud.pause_for_app_exit()
    assert snapshot["pause_reason"] == "app_exit"
    assert snapshot["auto_setup_enabled"] is True
    assert snapshot["compute"] is None
    started = asyncio.Event()

    async def auto_setup():
        started.set()

    monkeypatch.setattr(cloud, "_auto_setup", auto_setup)
    await cloud.resume_auto_setup()
    await cloud.auto_task
    assert started.is_set()
    assert cloud.read()["auto_setup_attempt_consumed"] is False


@pytest.mark.asyncio
async def test_app_exit_keeps_ambiguous_fence_and_never_replays_creation(cloud, monkeypatch):
    cloud.write(
        {
            "volume": {"id": "volume"},
            "auto_setup_enabled": True,
            "auto_setup_attempt_consumed": True,
            "setup_phase": "starting_worker",
            "compute": pending_installer(),
        }
    )

    async def release():
        raise CloudSetupError("Uncertain start")

    monkeypatch.setattr(cloud, "_release", release)
    snapshot = await cloud.pause_for_app_exit()
    assert snapshot["compute"]
    assert snapshot["cleanup_pending"]
    assert snapshot["auto_setup_enabled"] is True
    await cloud.resume_auto_setup()
    assert cloud.auto_task is None
    assert cloud.read()["compute"]


@pytest.mark.asyncio
async def test_trusted_stopped_queue_allows_idle_generation_release_on_exit(cloud, monkeypatch):
    cloud.write(
        {"volume": {"id": "volume"}, "compute": {**pending_installer(), "kind": "generation"}}
    )

    async def pending():
        return True

    async def release():
        state = cloud.read()
        state.pop("compute")
        state["cleanup_pending"] = False
        cloud.write(state)

    cloud.pending = pending
    monkeypatch.setattr(cloud, "_release", release)
    with pytest.raises(CloudSetupError, match="generation"):
        await cloud.pause_for_app_exit()
    assert (await cloud.pause_for_app_exit(queue_paused=True))["compute"] is None
