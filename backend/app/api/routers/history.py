"""
Generation history — read + favorite.

The route is reconstructed from the columns denormalized onto each row, so a
past generation still reports exactly what produced it even after that model is
removed from the catalog. Audio URLs are freshly signed per response.
"""

from __future__ import annotations

from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, Query

from ...config import Settings
from ...db import Database
from ...exceptions import HistoryNotFoundError
from ...inference.catalog import ModelCatalog
from ..deps import get_catalog, get_db, get_settings
from ..media_tokens import make_media_url
from ..schemas.history import HistoryItem, HistoryList, HistoryUpdate
from ..schemas.tts import RouteInfo

router = APIRouter(prefix="/history", tags=["history"])


def _item(
    row: aiosqlite.Row, profile_name: str | None, catalog: ModelCatalog, settings: Settings
) -> HistoryItem:
    spec = catalog.get(row["model_id"])
    route = RouteInfo(
        model_id=row["model_id"],
        model_display_name=spec.display_name if spec else row["model_id"],
        transform=row["transform"], lossy=bool(row["is_lossy"]),
        rationale=row["route_rationale"], source_script=row["source_script"],
        alternatives=[],
    )
    return HistoryItem(
        id=row["id"], profile_id=row["profile_id"], profile_name=profile_name,
        input_text=row["input_text"], language=row["language"],
        audio_url=make_media_url(
            f"history/{row['id']}", settings.media_token_secret, settings.media_token_ttl_sec
        ),
        output_format=row["output_format"], duration_sec=row["duration_sec"],
        gen_time_sec=row["gen_time_sec"], is_favorite=bool(row["is_favorite"]),
        route=route, created_at=row["created_at"],
    )


@router.get("", response_model=HistoryList)
async def list_history(
    db: Annotated[Database, Depends(get_db)],
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
    settings: Annotated[Settings, Depends(get_settings)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> HistoryList:
    rows = await db.list_generations(limit=page_size, offset=(page - 1) * page_size)
    items = []
    for r in rows:
        prof = await db.get_profile(r["profile_id"])
        items.append(_item(r, prof["name"] if prof else None, catalog, settings))
    return HistoryList(
        items=items, total=await db.count_generations(), page=page, page_size=page_size
    )


@router.get("/{gen_id}", response_model=HistoryItem)
async def get_history(
    gen_id: int,
    db: Annotated[Database, Depends(get_db)],
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HistoryItem:
    row = await db.get_generation(gen_id)
    if row is None:
        raise HistoryNotFoundError(gen_id)
    prof = await db.get_profile(row["profile_id"])
    return _item(row, prof["name"] if prof else None, catalog, settings)


@router.patch("/{gen_id}", response_model=HistoryItem)
async def update_history(
    gen_id: int,
    body: HistoryUpdate,
    db: Annotated[Database, Depends(get_db)],
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HistoryItem:
    row = await db.get_generation(gen_id)
    if row is None:
        raise HistoryNotFoundError(gen_id)
    if body.is_favorite is not None:
        row = await db.set_favorite(gen_id, body.is_favorite)
        assert row is not None
    prof = await db.get_profile(row["profile_id"])
    return _item(row, prof["name"] if prof else None, catalog, settings)
