"""
AI Voice Clone Studio — History API Router
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pathlib import Path

from ..database import get_db, close_db

router = APIRouter(prefix="/api/history", tags=["History"])


@router.get("")
async def list_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List generation history with pagination."""
    db = await get_db()
    try:
        offset = (page - 1) * page_size

        # Get total count
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM generation_history")
        total = (await cursor.fetchone())["cnt"]

        # Get paginated items with profile name
        cursor = await db.execute(
            """SELECT gh.*, vp.name as profile_name
               FROM generation_history gh
               LEFT JOIN voice_profiles vp ON gh.profile_id = vp.id
               ORDER BY gh.created_at DESC
               LIMIT ? OFFSET ?""",
            (page_size, offset),
        )
        rows = await cursor.fetchall()
        items = [dict(row) for row in rows]

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    finally:
        await close_db(db)


@router.get("/{history_id}")
async def get_history_item(history_id: int):
    """Get a specific history entry."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT gh.*, vp.name as profile_name
               FROM generation_history gh
               LEFT JOIN voice_profiles vp ON gh.profile_id = vp.id
               WHERE gh.id = ?""",
            (history_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="History entry not found")
        return {"item": dict(row)}
    finally:
        await close_db(db)


@router.get("/{history_id}/audio")
async def get_history_audio(history_id: int):
    """Stream a history entry's audio."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT output_path FROM generation_history WHERE id = ?",
            (history_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="History entry not found")

        audio_path = Path(row["output_path"])
        if not audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found on disk")

        return FileResponse(str(audio_path), media_type="audio/wav", filename=audio_path.name)
    finally:
        await close_db(db)


@router.delete("/{history_id}")
async def delete_history_item(history_id: int):
    """Delete a history entry and its audio file."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT output_path FROM generation_history WHERE id = ?",
            (history_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="History entry not found")

        # Delete audio file
        audio_path = Path(row["output_path"])
        if audio_path.exists():
            audio_path.unlink()

        # Delete from database
        await db.execute("DELETE FROM generation_history WHERE id = ?", (history_id,))
        await db.commit()

        return {"status": "ok", "message": f"History entry {history_id} deleted"}
    finally:
        await close_db(db)


@router.patch("/{history_id}/favorite")
async def toggle_favorite(history_id: int):
    """Toggle favorite status on a history entry."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT is_favorite FROM generation_history WHERE id = ?",
            (history_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="History entry not found")

        new_status = not row["is_favorite"]
        await db.execute(
            "UPDATE generation_history SET is_favorite = ? WHERE id = ?",
            (new_status, history_id),
        )
        await db.commit()

        return {"status": "ok", "is_favorite": new_status}
    finally:
        await close_db(db)
