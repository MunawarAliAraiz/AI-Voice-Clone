"""
AI Voice Clone Studio — Voice Profile Service
"""

import subprocess
import wave
from typing import Optional
from pathlib import Path
from datetime import datetime


from ..database import get_db, close_db
from ..config import settings
from ..utils.logger import setup_logger
from ..utils.exceptions import AudioValidationError, ProfileNotFoundError

logger = setup_logger("voiceclone.service.voice")

ALLOWED_EXTENSIONS = {
    ".wav", ".mp3", ".ogg", ".flac", ".webm", ".m4a",
    ".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv",
}
MAX_DURATION_SEC = 300  # 5 minutes


def _validate_audio_extension(filename: str) -> None:
    """Validate audio or video file extension."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise AudioValidationError(
            f"Unsupported file format '{ext}'. Allowed audio/video formats: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )



from ..utils.audio_pipeline import AudioPipeline, AudioPipelineConfig


def _convert_to_wav(input_path: Path, config: Optional[AudioPipelineConfig] = None) -> Path:
    """Convert any audio format to a clean WAV file for AI model compatibility.

    Delegates to modular AudioPipeline for optional format conversion,
    noise reduction, silence trimming, and EBU R128 loudness normalization.
    """
    if config is None:
        # Default reference conversion config: 22.05kHz mono PCM WAV, normalized & trimmed
        config = AudioPipelineConfig(
            convert_wav=True,
            sample_rate=22050,
            channels=1,
            normalize_loudness=True,
            trim_silence=True,
            reduce_noise=False,
        )

    return AudioPipeline.process_pipeline(input_path=input_path, config=config)



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
    """Save an uploaded voice recording and create a profile.

    Automatically converts any audio format (webm, mp3, ogg, flac) to
    a clean 22kHz mono WAV that is compatible with all AI models.
    """
    _validate_audio_extension(filename)

    # Save raw uploaded file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    ext = Path(filename).suffix.lower()
    audio_filename = f"{safe_name}_{timestamp}{ext}"
    audio_path = settings.voices_dir / audio_filename

    settings.voices_dir.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(file_content)
    logger.info(f"Saved raw upload: {audio_path}")

    # Convert to AI-compatible WAV (handles .webm from browser, .mp3, etc.)
    audio_path = _convert_to_wav(audio_path)

    # Get duration from the final WAV
    duration = _get_wav_duration(audio_path)

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

        logger.info(f"✅ Created voice profile #{profile_id}: '{name}' ({duration:.1f}s)" if duration else f"✅ Created voice profile #{profile_id}: '{name}'")

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
