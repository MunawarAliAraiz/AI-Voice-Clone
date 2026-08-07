"""
Voice profiles — enrollment and management.

A reference voice may only be stored with an explicit authorization
confirmation: the product clones a real person's voice, so `consent` is a
required field and enrollment is refused without it. The audio is streamed to
disk, validated, and its quality measured at the door.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

import uuid
from ...audio import store_upload, validate_audio
from ...audio_editor import EditOptions, process_audio_edits
from ...config import Settings
from ...db import Database
from ...exceptions import AudioValidationError, ProfileNotFoundError, ValidationError
from ..deps import get_db, get_settings
from ..media_tokens import make_media_url, verify_token
from ..schemas.voice import VoiceProfileList, VoiceProfileResponse, VoiceProfileUpdate

router = APIRouter(prefix="/voices", tags=["voices"])


def _to_response(row: aiosqlite.Row, settings: Settings) -> VoiceProfileResponse:
    return VoiceProfileResponse(
        id=row["id"], name=row["name"], language=row["language"],
        transcript=row["transcript"], duration_sec=row["duration_sec"],
        sample_rate=row["sample_rate"], is_active=bool(row["is_active"]),
        audio_url=make_media_url(
            f"voice/{row['id']}", settings.media_token_secret, settings.media_token_ttl_sec
        ),
        peak_dbfs=row["peak_dbfs"], is_clipped=bool(row["is_clipped"]),
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


@router.post("/preview-edit")
async def preview_edit(
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File(description="Reference audio.")],
    trim_start: Annotated[float, Form()] = 0.0,
    trim_end: Annotated[float | None, Form()] = None,
    speed: Annotated[float, Form()] = 1.0,
    pitch_semitones: Annotated[float, Form()] = 0.0,
    gain_db: Annotated[float, Form()] = 0.0,
    fade_in_sec: Annotated[float, Form()] = 0.0,
    fade_out_sec: Annotated[float, Form()] = 0.0,
    normalize_lufs: Annotated[bool, Form()] = False,
    remove_silence: Annotated[bool, Form()] = False,
) -> dict:
    # Stream input upload to temporary file
    orig_path = await store_upload(
        file, settings.voices_dir, max_bytes=settings.max_upload_mb * 1024 * 1024
    )
    edited_path = orig_path.with_suffix(".preview.wav")
    options = EditOptions(
        trim_start=trim_start,
        trim_end=trim_end,
        speed=speed,
        pitch_semitones=pitch_semitones,
        gain_db=gain_db,
        fade_in_sec=fade_in_sec,
        fade_out_sec=fade_out_sec,
        normalize_lufs=normalize_lufs,
        remove_silence=remove_silence,
    )
    try:
        meta = process_audio_edits(orig_path, edited_path, options)
    except Exception:
        orig_path.unlink(missing_ok=True)
        edited_path.unlink(missing_ok=True)
        raise

    # Store temp edited preview path under temp voice entry id or returning relative token
    preview_id = edited_path.stem
    token = make_media_url(
        f"voice_edit/{preview_id}", settings.media_token_secret, settings.media_token_ttl_sec
    )
    return {
        "preview_url": token,
        "edited_path": str(edited_path),
        "orig_path": str(orig_path),
        "duration_sec": meta.duration_sec,
        "sample_rate": meta.sample_rate,
        "peak_dbfs": meta.peak_dbfs,
        "is_clipped": meta.is_clipped,
    }


@router.post("", response_model=VoiceProfileResponse, status_code=201)
async def create_voice(
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File(description="Reference audio.")],
    name: Annotated[str, Form(min_length=1, max_length=100)],
    language: Annotated[str, Form()],
    consent: Annotated[bool, Form(description="Authorized-to-clone confirmation.")],
    transcript: Annotated[str | None, Form()] = None,
    trim_start: Annotated[float, Form()] = 0.0,
    trim_end: Annotated[float | None, Form()] = None,
    speed: Annotated[float, Form()] = 1.0,
    pitch_semitones: Annotated[float, Form()] = 0.0,
    gain_db: Annotated[float, Form()] = 0.0,
    fade_in_sec: Annotated[float, Form()] = 0.0,
    fade_out_sec: Annotated[float, Form()] = 0.0,
    normalize_lufs: Annotated[bool, Form()] = False,
    remove_silence: Annotated[bool, Form()] = False,
) -> VoiceProfileResponse:
    if not consent:
        raise ValidationError(
            "You must confirm you are authorized to clone this voice before uploading it."
        )
    orig_path = await store_upload(
        file, settings.voices_dir, max_bytes=settings.max_upload_mb * 1024 * 1024
    )
    
    # Check if any edits are specified
    has_edits = (
        trim_start > 0 or trim_end is not None or speed != 1.0 or pitch_semitones != 0 or
        gain_db != 0 or fade_in_sec > 0 or fade_out_sec > 0 or normalize_lufs or remove_silence
    )
    
    if has_edits:
        final_path = orig_path.with_suffix(".profile.wav")
        options = EditOptions(
            trim_start=trim_start,
            trim_end=trim_end,
            speed=speed,
            pitch_semitones=pitch_semitones,
            gain_db=gain_db,
            fade_in_sec=fade_in_sec,
            fade_out_sec=fade_out_sec,
            normalize_lufs=normalize_lufs,
            remove_silence=remove_silence,
        )
        try:
            meta = process_audio_edits(orig_path, final_path, options)
        except AudioValidationError:
            orig_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            raise
    else:
        try:
            meta = validate_audio(orig_path)
            final_path = meta.path
        except AudioValidationError:
            orig_path.unlink(missing_ok=True)
            raise

    row = await db.create_profile(
        name=name, audio_path=str(final_path), language=language, transcript=transcript,
        duration_sec=meta.duration_sec, sample_rate=meta.sample_rate,
        peak_dbfs=meta.peak_dbfs, is_clipped=meta.is_clipped,
    )
    return _to_response(row, settings)


@router.get("", response_model=VoiceProfileList)
async def list_voices(
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> VoiceProfileList:
    rows = await db.list_profiles()
    return VoiceProfileList(
        profiles=[_to_response(r, settings) for r in rows], total=len(rows)
    )


@router.get("/{profile_id}", response_model=VoiceProfileResponse)
async def get_voice(
    profile_id: int,
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> VoiceProfileResponse:
    row = await db.get_profile(profile_id)
    if row is None:
        raise ProfileNotFoundError(profile_id)
    return _to_response(row, settings)


@router.patch("/{profile_id}", response_model=VoiceProfileResponse)
async def update_voice(
    profile_id: int,
    body: VoiceProfileUpdate,
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> VoiceProfileResponse:
    if await db.get_profile(profile_id) is None:
        raise ProfileNotFoundError(profile_id)
    row = await db.update_profile(
        profile_id, name=body.name, transcript=body.transcript, is_active=body.is_active
    )
    assert row is not None
    return _to_response(row, settings)


@router.delete("/{profile_id}", status_code=204)
async def delete_voice(
    profile_id: int, db: Annotated[Database, Depends(get_db)]
) -> Response:
    row = await db.get_profile(profile_id)
    if row is None:
        raise ProfileNotFoundError(profile_id)
    await db.delete_profile(profile_id)
    Path(row["audio_path"]).unlink(missing_ok=True)  # noqa: ASYNC240 (one-shot unlink)
    return Response(status_code=204)
