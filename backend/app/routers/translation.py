"""
AI Voice Clone Studio — Translation API Router

Provides REST API endpoints for neural translation between Urdu, Hindi, and English.
"""

from fastapi import APIRouter, HTTPException
from ..models.schemas import TranslationRequest, TranslationResponse
from ..services.translation_service import get_translation_service, detect_language, TranslationError

router = APIRouter(prefix="/api/translate", tags=["Translation"])


@router.post("", response_model=TranslationResponse)
async def translate_text(request: TranslationRequest):
    """Translate text between Urdu, Hindi, and English using Meta NLLB-200."""
    try:
        service = get_translation_service()
        result = await service.translate(
            text=request.text,
            target_lang=request.target_lang,
            source_lang=request.source_lang,
        )
        return {
            "status": "ok",
            "translated_text": result["translated_text"],
            "source_lang": result["source_lang"],
            "target_lang": result["target_lang"],
            "cached": result["cached"],
        }
    except TranslationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/detect")
async def detect_text_language(payload: dict):
    """Detect language code ('ur', 'hi', 'en') from text."""
    text = payload.get("text", "")
    detected = detect_language(text)
    return {"status": "ok", "detected_language": detected}
