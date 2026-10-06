"""Desktop optional connection routes; no provider writes or shared credentials."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from ...config import Settings
from ...integrations.cloudflare import (
    INVALID_INPUT,
    CloudflareClient,
    CloudflareConnectionError,
    CloudflareConnectionStore,
    CloudflareCredentials,
)
from ..deps import get_settings

router = APIRouter(prefix="/integrations/cloudflare", tags=["integrations"])


def _store(settings: Settings) -> CloudflareConnectionStore:
    if settings.desktop_static_dir is None:
        raise HTTPException(404, "Cloudflare connection is available in the desktop app.")
    return CloudflareConnectionStore(settings.data_dir)


@router.get("")
async def connection(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    store = _store(settings)
    try:
        return await asyncio.to_thread(store.status)
    except CloudflareConnectionError as exc:
        return {
            "connected": False,
            "account_id_masked": None,
            "protection_ready": False,
            "detail": str(exc),
        }
    except (OSError, RuntimeError):
        raise HTTPException(503, "Cannot unlock Cloudflare access on this PC.") from None


@router.put("")
async def connect(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    store = _store(settings)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 2048:
            raise HTTPException(400, INVALID_INPUT)
        raw.extend(chunk)
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {"account_id", "api_token"}:
            raise ValueError
        credentials = CloudflareCredentials(payload["account_id"], payload["api_token"])
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(400, INVALID_INPUT) from None
    client = CloudflareClient(credentials)
    try:
        try:
            await client.validate()
        finally:
            await client.close()
        await asyncio.to_thread(store.set, credentials)
        return await asyncio.to_thread(store.status)
    except CloudflareConnectionError as exc:
        raise HTTPException(502, str(exc)) from None
    except (OSError, RuntimeError):
        raise HTTPException(503, "Cannot save encrypted Cloudflare access on this PC.") from None


@router.delete("", status_code=204)
async def disconnect(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    store = _store(settings)
    try:
        await asyncio.to_thread(store.clear)
    except OSError:
        raise HTTPException(503, "Cannot remove saved Cloudflare access on this PC.") from None
