"""
AI Voice Clone Studio — Voice Profile Service
"""

import shutil
import wave
from pathlib import Path
from datetime import datetime

from ..database import get_db, close_db
from ..config import settings
from ..utils.logger import setup_logger
from ..utils.exceptions import AudioValidationError, ProfileNotFoundError

logger = setup_logger("voiceclone.service.voice")

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".webm"}
MAX_DURATION_SEC = 300  # 5 minutes


def _validate_audio_extension(filename: str) -> None:
    """Validate audio file extension."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise AudioValidationError(
            f"Unsupported format '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )


def _get_wav_duration(filepath: Path) -> float | None:
    """Get duration of a WAV file in seconds."""
    try:
        with wave.open(str(filepath), "r") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return None


async def save_voice_recording(
    file_content: bytes,
    filename: str,
    name: str,
    transcript: str | None,
    language: str,
) -> dict:
    """Save an uploaded voice recording and create a profile."""
    _validate_audio_extension(filename)

    # Save file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    ext = Path(filename).suffix.lower()
    audio_filename = f"{safe_name}_{timestamp}{ext}"
    audio_path = settings.voices_dir / audio_filename

    settings.voices_dir.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(file_content)
    logger.info(f"Saved voice recording: {audio_path}")

    # Get duration (for WAV files)
    duration = _get_wav_duration(audio_path) if ext == ".wav" else None

    # Save to database
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO voice_profiles (name, audio_path, transcript, language, duration_sec)
               VALUES (?, ?, ?, ?, ?)""",
            (name, str(audio_path), transcript, language, duration),
        )
        await db.commit()
        profile_id = cursor.lastrowid

        logger.info(f"Created voice profile #{profile_id}: '{name}'")

        return {
            "id": profile_id,
            "name": name,
            "audio_path": str(audio_path),
            "transcript": transcript,
            "language": language,
            "duration_sec": duration,
        }
    finally:
        await close_db(db)


async def get_all_profiles() -> list[dict]:
    """Get all active voice profiles."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM voice_profiles WHERE is_active = 1 ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await close_db(db)


async def get_profile(profile_id: int) -> dict:
    """Get a specific voice profile."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM voice_profiles WHERE id = ? AND is_active = 1",
            (profile_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ProfileNotFoundError(profile_id)
        return dict(row)
    finally:
        await close_db(db)


async def delete_profile(profile_id: int) -> None:
    """Soft-delete a voice profile."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE voice_profiles SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (profile_id,),
        )
        await db.commit()
        logger.info(f"Deleted voice profile #{profile_id}")
    finally:
        await close_db(db)
