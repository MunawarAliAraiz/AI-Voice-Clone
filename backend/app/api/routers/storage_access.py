"""Desktop-only, Pod-free storage-key connection. Provider reads only."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from ...config import Settings
from ...runpod.controller import CloudSetupError, controller
from ...runpod.storage_access import (
    StorageAccessClient,
    StorageAccessError,
    StorageAccessStore,
    StorageBinding,
    StorageCredentials,
)
from ...runpod.storage_setup import storage_writes_qualified
from ..deps import get_settings

router = APIRouter(prefix="/runpod/storage-access", tags=["runpod"])


def _store(settings: Settings) -> StorageAccessStore:
    if settings.desktop_static_dir is None:
        raise HTTPException(404, "Storage access is available in the desktop app.")
    return StorageAccessStore(settings.data_dir)


def _binding(settings: Settings) -> StorageBinding:
    cloud = controller(settings)
    key = cloud.key()
    volume = cloud.read().get("volume") or {}
    if not volume:
        raise ValueError("Choose model storage first.")
    return StorageBinding(
        account=hashlib.sha256(key.encode()).hexdigest(),
        volume_id=volume.get("id", ""),
        region=volume.get("dataCenter", ""),
        prefix=(
            "voice-clone/hf-cache/hub/"
            if volume.get("cache_namespace") == "isolated"
            else "hf-cache/hub/"
        ),
    )


@router.get("")
async def connection(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    store = _store(settings)
    try:
        binding = _binding(settings)
        result = await asyncio.to_thread(store.status, binding)
        result["download_supported"] = result["connected"] and storage_writes_qualified(binding)
        return result
    except (CloudSetupError, ValueError) as exc:
        return {
            "connected": False,
            "access_key_masked": None,
            "region": None,
            "download_supported": False,
            "detail": str(exc),
        }
    except (OSError, RuntimeError):
        raise HTTPException(503, "Cannot unlock storage access on this PC.") from None


@router.put("")
async def connect(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    store = _store(settings)
    # Secret-bearing schema validation errors must never echo submitted values.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 2048:
            raise HTTPException(400, "Enter the storage access key and secret shown by Runpod.")
    try:
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"access_key", "secret_key"}
            or any(not isinstance(value, str) for value in payload.values())
        ):
            raise ValueError
        credentials = StorageCredentials(payload["access_key"], payload["secret_key"])
    except (ValueError, TypeError):
        raise HTTPException(
            400, "Enter the storage access key and secret shown by Runpod."
        ) from None
    try:
        binding = _binding(settings)
        client = StorageAccessClient(credentials, binding)
        try:
            await client.validate()
        finally:
            await client.close()
        # A selected volume/account may change while an external read is pending.
        if binding != _binding(settings):
            raise StorageAccessError("Model storage changed. Connect storage access again.")
        await asyncio.to_thread(store.set, credentials, binding)
        await controller(settings).resume_auto_setup()
        result = store.status(binding)
        result["download_supported"] = result["connected"] and storage_writes_qualified(binding)
        return result
    except (CloudSetupError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from None
    except StorageAccessError as exc:
        raise HTTPException(502, str(exc)) from None
    except (OSError, RuntimeError):
        raise HTTPException(503, "Cannot save encrypted storage access on this PC.") from None


@router.delete("", status_code=204)
async def disconnect(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    try:
        cloud = controller(settings)
        if cloud.storage_setup and cloud.setup_task and not cloud.setup_task.done():
            await cloud.cancel_setup()
        await asyncio.to_thread(_store(settings).clear)
    except OSError:
        raise HTTPException(503, "Cannot remove saved storage access on this PC.") from None


@router.post("/check")
async def check_connection(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    store = _store(settings)
    try:
        binding = _binding(settings)
        credentials = await asyncio.to_thread(store.get, binding)
        if credentials is None:
            raise ValueError("Connect storage access first.")
        client = StorageAccessClient(credentials, binding)
        try:
            await client.validate()
        finally:
            await client.close()
        if binding != _binding(settings):
            raise ValueError("Model storage changed. Connect storage access again.")
        return store.status(binding)
    except (CloudSetupError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from None
    except StorageAccessError as exc:
        raise HTTPException(502, str(exc)) from None
    except (OSError, RuntimeError):
        raise HTTPException(503, "Cannot unlock storage access on this PC.") from None


@router.get("/setup/progress")
async def storage_setup_progress(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    _store(settings)
    return await controller(settings).snapshot()


@router.post("/setup", status_code=202)
async def start_storage_setup(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    _store(settings)
    cloud = controller(settings)
    try:
        credentials, _ = cloud._storage_credentials()
        if credentials is None:
            raise CloudSetupError("Connect storage access first.")
        await cloud.set_auto_setup(True)
        return await cloud.snapshot()
    except (CloudSetupError, StorageAccessError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/setup/pause")
async def pause_storage_setup(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    _store(settings)
    try:
        return await controller(settings).cancel_setup()
    except CloudSetupError as exc:
        raise HTTPException(409, str(exc)) from None
