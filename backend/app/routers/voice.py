"""
AI Voice Clone Studio — Voice Profile API Router
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path

from ..services.voice_service import (
    save_voice_recording,
    get_all_profiles,
    get_profile,
    delete_profile,
)
from ..utils.exceptions import AudioValidationError, ProfileNotFoundError

router = APIRouter(prefix="/api/voice", tags=["Voice Profiles"])


@router.post("/upload")
async def upload_voice(
    file: UploadFile = File(...),
    name: str = Form(...),
    transcript: str = Form(None),
    language: str = Form("en"),
):
    """Upload a voice recording and create a profile."""
    try:
        content = await file.read()
        result = await save_voice_recording(
            file_content=content,
            filename=file.filename or "recording.wav",
            name=name,
            transcript=transcript,
            language=language,
        )
        return {"status": "ok", "profile": result}

    except AudioValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/record")
async def save_recording(
    file: UploadFile = File(...),
    name: str = Form(...),
    transcript: str = Form(None),
    language: str = Form("en"),
):
    """Save an in-app voice recording as a profile."""
    try:
        content = await file.read()
        result = await save_voice_recording(
            file_content=content,
            filename=file.filename or "recording.webm",
            name=name,
            transcript=transcript,
            language=language,
        )
        return {"status": "ok", "profile": result}

    except AudioValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/profiles")
async def list_profiles():
    """List all voice profiles."""
    profiles = await get_all_profiles()
    return {"profiles": profiles, "total": len(profiles)}


@router.get("/profiles/{profile_id}")
async def get_profile_by_id(profile_id: int):
    """Get a specific voice profile."""
    try:
        profile = await get_profile(profile_id)
        return {"profile": profile}
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.delete("/profiles/{profile_id}")
async def delete_profile_by_id(profile_id: int):
    """Delete a voice profile."""
    try:
        await delete_profile(profile_id)
        return {"status": "ok", "message": f"Profile {profile_id} deleted"}
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.get("/profiles/{profile_id}/audio")
async def get_profile_audio(profile_id: int):
    """Stream a profile's reference audio."""
    try:
        profile = await get_profile(profile_id)
        audio_path = Path(profile["audio_path"])
        if not audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found")
        return FileResponse(
            str(audio_path),
            media_type="audio/wav",
            filename=audio_path.name,
        )
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
