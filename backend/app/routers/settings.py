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
    """List all available AI models and registered TTS engines."""
    from ..services.model_manager import get_model_manager
    manager = get_model_manager()
    models_catalog = await manager.list_models()
    engines = get_available_engines()
    return {
        "models": models_catalog,
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



@router.get("/models/{engine_name}/health")
async def engine_health(engine_name: str):
    """Get detailed health check and diagnostic status for a specific engine."""
    from ..engines import get_engine
    try:
        engine = get_engine(engine_name)
        return {"status": "ok", "health": engine.health_check()}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/models/{engine_name}/unload")
async def unload_engine_model(engine_name: str):
    """Manually unload an engine model from memory to free VRAM/RAM."""
    from ..engines import get_engine
    try:
        engine = get_engine(engine_name)
        await engine.unload_model()
        return {"status": "ok", "message": f"Engine '{engine_name}' unloaded from memory"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))



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
async def get_gpu_status():
    """Get detailed GPU metrics, temperature, and operating mode."""
    from ..utils.gpu_manager import get_gpu_manager
    mgr = get_gpu_manager()
    metrics = mgr.get_vram_metrics()
    return {
        "status": "ok",
        "available": metrics.available,
        "name": metrics.gpu_name,
        "vram_total_mb": metrics.vram_total_mb,
        "vram_used_mb": metrics.vram_used_mb,
        "vram_free_mb": metrics.vram_free_mb,
        "vram_allocated_mb": metrics.vram_allocated_mb,
        "vram_reserved_mb": metrics.vram_reserved_mb,
        "usage_pct": metrics.usage_pct,
        "temperature_celsius": metrics.temperature_celsius,
        "device": metrics.device,
        "mode": mgr.mode.value,
    }


@router.post("/system/gpu/cleanup")
async def cleanup_gpu_memory():
    """Trigger manual VRAM memory cleanup pass."""
    from ..utils.gpu_manager import get_gpu_manager
    mgr = get_gpu_manager()
    res = mgr.cleanup_memory()
    return res


@router.post("/system/gpu/mode")
async def set_gpu_mode(payload: dict):
    """Set GPU operating mode ('one_active_model' or 'multi_model')."""
    from ..utils.gpu_manager import get_gpu_manager
    mode = payload.get("mode", "one_active_model")
    mgr = get_gpu_manager()
    try:
        mgr.set_mode(mode)
        return {"status": "ok", "mode": mgr.mode.value}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

