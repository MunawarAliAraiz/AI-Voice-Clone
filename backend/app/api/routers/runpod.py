"""Desktop-only Runpod reads and rate-based cost previews."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...config import Settings
from ...exceptions import GenerationError
from ...inference.catalog import CATALOG
from ...inference.remote_scheduler import RemoteScheduler
from ...remote_worker.model_pins import AUXILIARY_PINS
from ...runpod.account_analytics import account_analytics
from ...runpod.client import POD_START_BLOCKED_REASON, RunpodApiError, RunpodClient
from ...runpod.controller import CloudSetupError, controller
from ...runpod.estimate import estimate_tts_costs
from ...runpod.secrets import RunpodKeyStore
from ...runpod.worker_pair import WorkerPair, WorkerPairStore
from ..deps import get_settings

router = APIRouter(prefix="/runpod", tags=["runpod"])


class ConnectionInput(BaseModel):
    api_key: str = Field(min_length=8, max_length=512)


class WorkerPairInput(BaseModel):
    pod_id: str = Field(min_length=6, max_length=32)
    worker_token: str = Field(min_length=24, max_length=512)


class StorageChoice(BaseModel):
    volume_id: str = Field(min_length=1, max_length=64)
    allow_other_app_volume: bool = False


class StorageQuoteRequest(BaseModel):
    region: str = Field(min_length=1, max_length=64)
    size_gb: int | None = Field(default=None, ge=1, le=4000)
    name: str | None = Field(default=None, min_length=1, max_length=64)
    replace_current: bool = False


class StoragePurchase(BaseModel):
    quote_id: str = Field(min_length=32, max_length=32)


class ComputePolicy(BaseModel):
    max_session_usd: float = Field(ge=0.1, le=20, allow_inf_nan=False)
    max_hourly_usd: float = Field(ge=0.1, le=10, allow_inf_nan=False)


class AutoSetupInput(BaseModel):
    enabled: bool


async def _cloud(settings: Settings, method: str, *args):
    _store(settings)
    if method in {"purchase_storage", "start_setup"} or (
        method == "set_auto_setup" and args and args[0]
    ):
        raise HTTPException(409, POD_START_BLOCKED_REASON)
    try:
        return await getattr(controller(settings), method)(*args)
    except (CloudSetupError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except RunpodApiError as exc:
        raise HTTPException(502, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "Cannot access protected cloud settings on this PC") from exc


@router.get("/setup")
async def cloud_setup(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    return await _cloud(settings, "snapshot")


@router.get("/setup/discover")
async def discover_storage(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    return await _cloud(settings, "discover")


@router.post("/setup/storage/quote")
async def storage_quote(
    body: StorageQuoteRequest, settings: Annotated[Settings, Depends(get_settings)]
) -> dict:
    return await _cloud(
        settings, "quote_storage", body.region, body.size_gb, body.name, body.replace_current
    )


@router.post("/setup/storage/purchase", status_code=201)
async def storage_purchase(
    body: StoragePurchase, settings: Annotated[Settings, Depends(get_settings)]
) -> dict:
    return await _cloud(settings, "purchase_storage", body.quote_id)


@router.put("/setup/storage")
async def storage_select(
    body: StorageChoice, settings: Annotated[Settings, Depends(get_settings)]
) -> dict:
    return await _cloud(settings, "select_storage", body.volume_id, body.allow_other_app_volume)


@router.put("/setup/policy", status_code=204)
async def compute_policy(
    body: ComputePolicy, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    await _cloud(settings, "set_policy", body.max_session_usd, body.max_hourly_usd)


@router.post("/setup/install", status_code=202)
async def auto_install(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    await _cloud(settings, "start_setup")
    return await _cloud(settings, "snapshot")


@router.post("/setup/release", status_code=204)
async def release_compute(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    await _cloud(settings, "release")


@router.post("/setup/cancel")
async def cancel_model_setup(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    return await _cloud(settings, "cancel_setup")


@router.put("/setup/auto")
async def automatic_model_setup(
    body: AutoSetupInput, settings: Annotated[Settings, Depends(get_settings)]
) -> dict:
    return await _cloud(settings, "set_auto_setup", body.enabled)


def _store(settings: Settings) -> RunpodKeyStore:
    if settings.desktop_static_dir is None:
        raise HTTPException(404, "Runpod management is available in the desktop app")
    return RunpodKeyStore(settings.data_dir)


def _key(settings: Settings) -> str:
    try:
        value = _store(settings).get_key()
    except (OSError, UnicodeError) as exc:
        raise HTTPException(503, "Cannot unlock the saved Runpod key") from exc
    if value is None:
        raise HTTPException(409, "Connect a Runpod account first")
    return value


async def _call(client: RunpodClient, method: str, *args: object) -> object:
    try:
        return await getattr(client, method)(*args)
    except RunpodApiError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        await client.close()


@router.get("/connection")
async def connection(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, bool]:
    return {"connected": _store(settings).has_key()}


@router.put("/connection")
async def connect(
    body: ConnectionInput, settings: Annotated[Settings, Depends(get_settings)]
) -> dict[str, bool]:
    state = await _cloud(settings, "snapshot")
    if state.get("compute"):
        raise HTTPException(409, "Release this app's cloud compute before changing the API key")
    client = RunpodClient(body.api_key)
    await _call(client, "list_gpu_types")  # Validate before saving.
    try:
        _store(settings).set_key(body.api_key)
    except OSError as exc:
        raise HTTPException(503, "Cannot protect the Runpod key on this PC") from exc
    cloud = controller(settings)
    saved = cloud.read()
    fingerprint = hashlib.sha256(body.api_key.strip().encode()).hexdigest()
    if saved.get("account") and saved["account"] != fingerprint:
        # No app-owned compute exists (checked above). A rotated/new key starts
        # local setup again; discover and explicitly adopt its existing volume.
        cloud.write({})
        cloud.progress = {}
        cloud.detail = "Select persistent storage to verify this account's models"
        cloud.quote = None
        WorkerPairStore(settings.data_dir).clear()
    return {"connected": True}


@router.delete("/connection", status_code=204)
async def disconnect(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    state = await _cloud(settings, "snapshot")
    if state.get("compute"):
        raise HTTPException(409, "Release this app's cloud compute before disconnecting")
    _store(settings).clear()


@router.get("/worker")
async def worker_connection(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    _store(settings)  # Desktop-only guard.
    try:
        pair = WorkerPairStore(settings.data_dir).get()
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, "Cannot unlock the saved Pod connection") from exc
    return {"paired": pair is not None, "pod_id": pair.pod_id if pair else None}


@router.put("/worker")
async def pair_worker(
    body: WorkerPairInput, settings: Annotated[Settings, Depends(get_settings)]
) -> dict[str, object]:
    _store(settings)
    try:
        pair = WorkerPair(body.pod_id, body.worker_token)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    remote = RemoteScheduler(pair.url, pair.token, CATALOG)
    try:
        await remote.status()  # Confirms authentication and protocol version.
    except GenerationError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        await remote.shutdown()
    try:
        WorkerPairStore(settings.data_dir).set(pair)
    except OSError as exc:
        raise HTTPException(503, "Cannot protect the Pod connection on this PC") from exc
    return {"paired": True, "pod_id": pair.pod_id}


@router.delete("/worker", status_code=204)
async def unpair_worker(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    _store(settings)
    WorkerPairStore(settings.data_dir).clear()


def _paired(settings: Settings) -> WorkerPair:
    _store(settings)
    try:
        pair = WorkerPairStore(settings.data_dir).get()
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, "Cannot unlock the saved Pod connection") from exc
    if pair is None:
        raise HTTPException(409, "Pair a generation Pod first")
    return pair


@router.get("/worker/models")
async def worker_models(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
    pair = _paired(settings)
    remote = RemoteScheduler(pair.url, pair.token, CATALOG)
    try:
        return {"models": await remote.model_installs()}
    except GenerationError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        await remote.shutdown()


@router.post("/worker/models/{model_id}/install", status_code=202)
async def install_worker_model(
    model_id: str, settings: Annotated[Settings, Depends(get_settings)]
) -> dict[str, str]:
    if CATALOG.get(model_id) is None and model_id not in AUXILIARY_PINS:
        raise HTTPException(404, "Unknown model")
    _store(settings)
    raise HTTPException(409, POD_START_BLOCKED_REASON)


@router.get("/estimate")
async def estimate(
    settings: Annotated[Settings, Depends(get_settings)],
    model_id: Annotated[str, Query(min_length=1)],
    text: Annotated[str, Query(min_length=1, max_length=6000)],
) -> dict[str, object]:
    spec = CATALOG.get(model_id)
    if spec is None:
        raise HTTPException(404, "Unknown model")
    gpus = await _call(RunpodClient(_key(settings)), "list_gpu_types")
    return {
        "model_id": model_id,
        "minimum_full_feature_vram_gb": 48,
        "estimates": estimate_tts_costs(gpus, spec=spec, text=text),
    }


@router.get("/analytics")
async def analytics(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
    key = _key(settings)
    pods = await _call(RunpodClient(key), "list_pods")
    volumes = await _call(RunpodClient(key), "list_volumes")
    # The provider Pod object may contain env (including worker tokens), SSH
    # addresses and other account details. Only return fields used by the UI.
    safe_pods = [
        {field: pod.get(field) for field in ("id", "name", "status", "cost", "dataCenterId", "gpu")}
        for pod in pods
    ]
    safe_volumes = [
        {field: volume.get(field) for field in ("id", "name", "size", "dataCenter", "type")}
        for volume in volumes
    ]
    return {"pods": safe_pods, "volumes": safe_volumes}


@router.get("/analytics/pods/{pod_id}")
async def pod_usage(
    pod_id: str, settings: Annotated[Settings, Depends(get_settings)]
) -> dict[str, object]:
    if not pod_id.isalnum():
        raise HTTPException(400, "Invalid Pod ID")
    result = await _call(RunpodClient(_key(settings)), "pod_billing", pod_id)
    return {
        "pod_id": pod_id,
        "records": result.get("records", []),
        "totals": result.get("metadata", {}).get("totals", {}),
    }


@router.get("/analytics/volumes/{volume_id}")
async def volume_usage(
    volume_id: str, settings: Annotated[Settings, Depends(get_settings)]
) -> dict[str, object]:
    if not volume_id.isalnum():
        raise HTTPException(400, "Invalid volume ID")
    result = await _call(RunpodClient(_key(settings)), "volume_billing", volume_id)
    return {
        "volume_id": volume_id,
        "records": result.get("records", []),
        "totals": result.get("metadata", {}).get("totals", {}),
    }


@router.get("/analytics/account")
async def account_spending(
    settings: Annotated[Settings, Depends(get_settings)],
    period: Annotated[Literal["24h", "7d", "30d"], Query()] = "24h",
) -> dict:
    client = RunpodClient(_key(settings))
    try:
        return await account_analytics(client, period)
    except RunpodApiError:
        raise HTTPException(
            502, "Cannot read Runpod account billing. Check API access and try again."
        ) from None
    finally:
        await client.close()
