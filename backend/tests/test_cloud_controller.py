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


@pytest.mark.asyncio
async def test_ambiguous_storage_purchase_never_replays_post(cloud):
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
            raise RunpodApiError("network failure")

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
    with pytest.raises(CloudSetupError, match="uncertain"):
        await cloud.purchase_storage("quote")


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
