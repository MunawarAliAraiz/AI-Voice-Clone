"""
Media serving — signed, expiring, range-capable.

An `<audio>` element cannot send an API key, so these routes are exempt from the
key check and authenticate with the `?t=` signature instead. The token is bound
to `{kind}/{id}`, so it cannot be replayed against another item, and it expires.
`FileResponse` handles Range requests, so the player can seek.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from ...config import Settings
from ...db import Database
from ...exceptions import HistoryNotFoundError, MediaTokenError, ProfileNotFoundError
from ..deps import get_db, get_settings
from ..media_tokens import verify_token

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/{kind}/{item_id}")
async def get_media(
    kind: str,
    item_id: int,
    t: str,
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    # Verify BEFORE any lookup, so a bad token reveals nothing about existence.
    verify_token(f"{kind}/{item_id}", t, settings.media_token_secret)

    if kind == "voice":
        row = await db.get_profile(item_id)
        if row is None:
            raise ProfileNotFoundError(item_id)
        path = row["audio_path"]
    elif kind == "history":
        row = await db.get_generation(item_id)
        if row is None:
            raise HistoryNotFoundError(item_id)
        path = row["output_path"]
    else:
        raise MediaTokenError(f"Unknown media kind {kind!r}.")

    if not Path(path).exists():  # noqa: ASYNC240 (one-shot stat)
        raise (HistoryNotFoundError if kind == "history" else ProfileNotFoundError)(item_id)
    return FileResponse(path)
