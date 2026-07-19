"""
AI Voice Clone Studio — Settings & System API Router
"""

from fastapi import APIRouter, HTTPException

from ..database import get_db, close_db
from ..config import settings as app_settings
from ..engines import get_available_engines
from ..utils.gpu import get_gpu_info

router = APIRouter(prefix="/api", tags=["Settings & System"])


# ── Settings ──


@router.get("/settings")
async def get_settings():
    """Get all application settings."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT key, value, category FROM settings ORDER BY category, key")
        rows = await cursor.fetchall()
        settings_dict = {}
        for row in rows:
            settings_dict[row["key"]] = {
                "value": row["value"],
                "category": row["category"],
            }
        return {"settings": settings_dict}
    finally:
        await close_db(db)


@router.put("/settings")
async def update_settings(updates: dict[str, str]):
    """Update application settings."""
    db = await get_db()
    try:
        for key, value in updates.items():
            await db.execute(
                """INSERT INTO settings (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP""",
                (key, value, value),
            )
        await db.commit()
        return {"status": "ok", "updated": list(updates.keys())}
    finally:
        await close_db(db)


# ── Models ──


@router.get("/models")
async def list_models():
    """List all available TTS engines and their status."""
    engines = get_available_engines()
    return {
        "engines": [
            {
                "name": e.name,
                "display_name": e.display_name,
                "version": e.version,
                "description": e.description,
                "supported_languages": e.supported_languages,
                "requires_gpu": e.requires_gpu,
                "model_size_mb": e.model_size_mb,
                "is_loaded": e.is_loaded,
            }
            for e in engines
        ]
    }


# ── System ──


@router.get("/system/status")
async def system_status():
    """Get system health and status."""
    gpu = get_gpu_info()

    db = await get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM voice_profiles WHERE is_active = 1")
        profiles_count = (await cursor.fetchone())["cnt"]

        cursor = await db.execute("SELECT COUNT(*) as cnt FROM generation_history")
        generations_count = (await cursor.fetchone())["cnt"]
    finally:
        await close_db(db)

    return {
        "status": "ok",
        "version": app_settings.app_version,
        "gpu_available": gpu.available,
        "gpu_name": gpu.name,
        "gpu_vram_mb": gpu.vram_total_mb,
        "cuda_version": gpu.cuda_version,
        "active_engine": app_settings.default_engine,
        "profiles_count": profiles_count,
        "generations_count": generations_count,
    }


@router.get("/system/gpu")
async def gpu_info():
    """Get detailed GPU information."""
    gpu = get_gpu_info()
    return {
        "available": gpu.available,
        "name": gpu.name,
        "vram_total_mb": gpu.vram_total_mb,
        "vram_free_mb": gpu.vram_free_mb,
        "cuda_version": gpu.cuda_version,
        "device": gpu.device,
    }
