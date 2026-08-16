"""
The user's pronunciation dictionary — CRUD.

WHY THIS EXISTS
---------------
§9e measured that 17.2% of English loanword instances are mispronounced by
OmniVoice, and 9 of the 11 offenders fail *every* time, so a respelling
genuinely fixes them. The maintainer can only verify the handful they can hear;
everything else has to be the user's own call, made against their own text and
their own ear.

WHAT IT IS NOT
--------------
Not a model, not a transform, not a routing decision. Entries are DATA for the
pure `apply_text_normalizations()`, loaded by `deps.get_lexicon` and passed into
`resolve()` as an argument. Nothing here can change which model runs.

An entry takes effect on the NEXT generation, not on jobs already queued: the
route (including `resolved_text`) is decided once at enqueue and stored on the
row, and re-deriving it at claim time is golden rule 4's bug wearing a queue.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, Query

from ...db import Database
from ...exceptions import PronunciationConflictError, PronunciationNotFoundError
from ..deps import get_db
from ..schemas.pronunciations import (
    PronunciationCreate,
    PronunciationItem,
    PronunciationList,
    PronunciationUpdate,
)

router = APIRouter(prefix="/pronunciations", tags=["pronunciations"])


def _item(row: aiosqlite.Row) -> PronunciationItem:
    return PronunciationItem(
        id=row["id"],
        key_text=row["key_text"],
        replacement=row["replacement"],
        language=row["language"],
        is_enabled=bool(row["is_enabled"]),
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _conflict(
    db: Database, *, key_text: str, language: str, ignoring: int | None = None
) -> PronunciationConflictError | None:
    """
    The pre-check that turns a UNIQUE violation into a useful 409.

    The database constraint remains the actual guard — this only exists so the
    response can name the row the user already has instead of reporting a bare
    clash. `ignoring` is the row being edited, which must not conflict with
    itself.
    """
    existing = await db.find_pronunciation(key_text=key_text, language=language)
    if existing is None or existing["id"] == ignoring:
        return None
    return PronunciationConflictError(key_text, language, existing_id=existing["id"])


@router.get("", response_model=PronunciationList)
async def list_pronunciations(
    db: Annotated[Database, Depends(get_db)],
    language: Annotated[str | None, Query()] = None,
) -> PronunciationList:
    """
    Every entry, newest first — **including disabled ones**.

    Disabled rows are not noise to be filtered: a disabled row whose key matches
    a shipped default is the only way to switch that default off, so hiding them
    would hide the mechanism.
    """
    rows = await db.list_pronunciations(language=language)
    return PronunciationList(items=[_item(r) for r in rows], total=len(rows))


@router.get("/{entry_id}", response_model=PronunciationItem)
async def get_pronunciation(
    entry_id: int, db: Annotated[Database, Depends(get_db)]
) -> PronunciationItem:
    row = await db.get_pronunciation(entry_id)
    if row is None:
        raise PronunciationNotFoundError(entry_id)
    return _item(row)


@router.post("", response_model=PronunciationItem, status_code=201)
async def create_pronunciation(
    body: PronunciationCreate, db: Annotated[Database, Depends(get_db)]
) -> PronunciationItem:
    conflict = await _conflict(db, key_text=body.key_text, language=body.language)
    if conflict is not None:
        raise conflict
    try:
        row = await db.create_pronunciation(
            key_text=body.key_text, replacement=body.replacement,
            language=body.language, is_enabled=body.is_enabled, notes=body.notes,
        )
    except sqlite3.IntegrityError as exc:
        # The pre-check above lost a race. The constraint is what actually
        # guarantees uniqueness, so this is a real path, not a belt-and-braces
        # one — and it must not surface as a 500.
        raise PronunciationConflictError(body.key_text, body.language) from exc
    return _item(row)


@router.patch("/{entry_id}", response_model=PronunciationItem)
async def update_pronunciation(
    entry_id: int, body: PronunciationUpdate, db: Annotated[Database, Depends(get_db)]
) -> PronunciationItem:
    current = await db.get_pronunciation(entry_id)
    if current is None:
        raise PronunciationNotFoundError(entry_id)

    # A key or language change can collide with a different row; renaming an
    # entry to what it already is cannot, hence `ignoring`.
    if body.key_text is not None or body.language is not None:
        conflict = await _conflict(
            db,
            key_text=body.key_text or current["key_text"],
            language=body.language or current["language"],
            ignoring=entry_id,
        )
        if conflict is not None:
            raise conflict

    try:
        row = await db.update_pronunciation(
            entry_id, key_text=body.key_text, replacement=body.replacement,
            language=body.language, is_enabled=body.is_enabled, notes=body.notes,
        )
    except sqlite3.IntegrityError as exc:
        raise PronunciationConflictError(
            body.key_text or current["key_text"], body.language or current["language"]
        ) from exc
    assert row is not None  # existence checked above, and nothing deletes here
    return _item(row)


@router.delete("/{entry_id}", status_code=204)
async def delete_pronunciation(
    entry_id: int, db: Annotated[Database, Depends(get_db)]
) -> None:
    """
    Deleting a user's row restores whatever the shipped default was for that
    key, if any — which is the difference between delete and disable, and why
    both exist.
    """
    if not await db.delete_pronunciation(entry_id):
        raise PronunciationNotFoundError(entry_id)
