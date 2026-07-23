"""
AI Voice Clone Studio — Model Manager API Router

Provides REST API endpoints for model downloading, progress tracking, checksum verification,
updates, deletion, and health diagnostic status.
"""

from fastapi import APIRouter, HTTPException
from ..services.model_manager import get_model_manager, ModelManagerError
from ..engines import get_engine, EngineRegistry
from ..utils.exceptions import EngineRegistrationError




router = APIRouter(prefix="/api/models", tags=["Model Manager"])


@router.get("")
async def list_models():
    """List all AI models with size, languages, GPU requirements, and download status."""
    manager = get_model_manager()
    models = await manager.list_models()

    # Include registered engine metadata for backward compatibility
    engines_info = [eng.get_info() for eng in EngineRegistry.list_engines()]
    return {
        "models": models,
        "engines": [
            {
                "name": info.name,
                "display_name": info.display_name,
                "version": info.version,
                "description": info.description,
                "languages": info.supported_languages,
                "requires_gpu": info.requires_gpu,
                "is_loaded": info.is_loaded,
            }
            for info in engines_info
        ],
    }


@router.get("/{model_name}")
async def get_model(model_name: str):
    """Get detailed model information, progress, and health status."""
    manager = get_model_manager()
    models = await manager.list_models()
    matching = [m for m in models if m["name"] == model_name]
    if not matching:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")
    return {"status": "ok", "model": matching[0]}


@router.post("/{model_name}/download")
async def download_model(model_name: str):
    """Start non-blocking background download for an AI model."""
    try:
        manager = get_model_manager()
        res = await manager.start_download(model_name)
        return res
    except ModelManagerError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{model_name}/progress")
async def get_download_progress(model_name: str):
    """Get background download progress percentage for an AI model."""
    manager = get_model_manager()
    prog = manager.get_download_progress(model_name)
    return {"status": "ok", "progress": prog}


@router.delete("/{model_name}")
async def delete_model(model_name: str):
    """Safely delete model files from disk."""
    try:
        manager = get_model_manager()
        res = await manager.delete_model(model_name)
        return res
    except ModelManagerError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{model_name}/update")
async def update_model(model_name: str):
    """Re-download and update model to latest Hugging Face revision."""
    try:
        manager = get_model_manager()
        res = await manager.update_model(model_name)
        return res
    except ModelManagerError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{model_name}/verify")
async def verify_model(model_name: str):
    """Calculate file checksums and verify model integrity."""
    try:
        manager = get_model_manager()
        res = await manager.verify_model_checksums(model_name)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Backward Compatible Engine Health & Unload Endpoints ──


@router.get("/{engine_name}/health")
async def get_engine_health(engine_name: str):
    """Get health status of a registered engine."""
    try:
        engine = get_engine(engine_name)
        health = engine.health_check()
        return {"status": "ok", "health": health}
    except EngineRegistrationError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.post("/{engine_name}/unload")
async def unload_engine(engine_name: str):
    """Manually unload a TTS engine from VRAM."""
    try:
        engine = get_engine(engine_name)
        await engine.unload_model()
        return {"status": "ok", "message": f"Engine '{engine_name}' unloaded successfully."}
    except EngineRegistrationError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
