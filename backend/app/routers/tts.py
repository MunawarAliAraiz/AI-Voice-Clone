"""
AI Voice Clone Studio — TTS Generation API Router
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path

from ..models.schemas import TTSGenerateRequest
from ..services.tts_service import generate_speech, get_supported_languages
from ..utils.exceptions import VoiceCloneError

router = APIRouter(prefix="/api/tts", tags=["TTS Generation"])


@router.post("/generate")
async def generate(request: TTSGenerateRequest):
    """Generate speech from text using a voice profile."""
    try:
        result = await generate_speech(
            text=request.text,
            profile_id=request.profile_id,
            language=request.language,
            engine_name=request.engine,
            output_format=request.output_format,
            emotion=request.emotion,
            style=request.style,
        )
        return {"status": "ok", "result": result}

    except VoiceCloneError as e:
        raise HTTPException(status_code=400, detail={"message": e.message, "code": e.code})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/generate/{history_id}/audio")
async def get_generated_audio(history_id: int):
    """Stream a generated audio file."""
    from ..database import get_db, close_db

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT output_path FROM generation_history WHERE id = ?",
            (history_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Generation not found")

        audio_path = Path(row["output_path"])
        if not audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found")

        return FileResponse(
            str(audio_path),
            media_type="audio/wav",
            filename=audio_path.name,
        )
    finally:
        await close_db(db)


@router.get("/languages")
async def languages():
    """List supported languages with their available engines."""
    langs = await get_supported_languages()
    return {"languages": langs}


@router.get("/emotions")
async def get_emotions():
    """List supported emotion modes."""
    from ..utils.emotion_engine import EmotionEngine
    return {"emotions": EmotionEngine.get_supported_emotions()}


@router.get("/styles")
async def get_styles():
    """List available speech style presets."""
    from ..utils.style_manager import StyleManager
    return {"styles": StyleManager.list_styles()}


