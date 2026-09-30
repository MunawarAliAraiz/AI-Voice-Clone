"""Desktop-only Runpod reads and rate-based cost previews."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...config import Settings
from ...exceptions import GenerationError
from ...inference.catalog import CATALOG
from ...inference.remote_scheduler import RemoteScheduler
from ...remote_worker.model_pins import AUXILIARY_PINS
from ...runpod.client import RunpodApiError, RunpodClient
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
    client = RunpodClient(body.api_key)
    await _call(client, "list_gpu_types")  # Validate before saving.
    try:
        _store(settings).set_key(body.api_key)
    except OSError as exc:
        raise HTTPException(503, "Cannot protect the Runpod key on this PC") from exc
    return {"connected": True}


@router.delete("/connection", status_code=204)
async def disconnect(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    _store(settings).clear()


@router.get("/worker")
async def worker_connection(
    settings: Annotated[Settings, Depends(get_settings)]
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
async def worker_models(
    settings: Annotated[Settings, Depends(get_settings)]
) -> dict[str, object]:
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
    pair = _paired(settings)
    remote = RemoteScheduler(pair.url, pair.token, CATALOG)
    try:
        return await remote.install(model_id)
    except GenerationError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        await remote.shutdown()


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
    return {"model_id": model_id, "minimum_full_feature_vram_gb": 48,
            "estimates": estimate_tts_costs(gpus, spec=spec, text=text)}


@router.get("/analytics")
async def analytics(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
    key = _key(settings)
    pods = await _call(RunpodClient(key), "list_pods")
    volumes = await _call(RunpodClient(key), "list_volumes")
    # The provider Pod object may contain env (including worker tokens), SSH
    # addresses and other account details. Only return fields used by the UI.
    safe_pods = [{field: pod.get(field) for field in
                  ("id", "name", "status", "cost", "dataCenterId", "gpu")}
                 for pod in pods]
    safe_volumes = [{field: volume.get(field) for field in
                     ("id", "name", "size", "dataCenter", "type")}
                    for volume in volumes]
    return {"pods": safe_pods, "volumes": safe_volumes}


@router.get("/analytics/pods/{pod_id}")
async def pod_usage(
    pod_id: str, settings: Annotated[Settings, Depends(get_settings)]
) -> dict[str, object]:
    if not pod_id.isalnum():
        raise HTTPException(400, "Invalid Pod ID")
    result = await _call(RunpodClient(_key(settings)), "pod_billing", pod_id)
    return {"pod_id": pod_id, "records": result.get("records", []),
            "totals": result.get("metadata", {}).get("totals", {})}


@router.get("/analytics/volumes/{volume_id}")
async def volume_usage(
    volume_id: str, settings: Annotated[Settings, Depends(get_settings)]
) -> dict[str, object]:
    if not volume_id.isalnum():
        raise HTTPException(400, "Invalid volume ID")
    result = await _call(RunpodClient(_key(settings)), "volume_billing", volume_id)
    return {"volume_id": volume_id, "records": result.get("records", []),
            "totals": result.get("metadata", {}).get("totals", {})}
