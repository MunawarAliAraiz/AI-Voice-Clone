"""
AI Voice Clone Studio — Voice Profile Service
"""

import subprocess
import wave
from pathlib import Path
from datetime import datetime

from ..database import get_db, close_db
from ..config import settings
from ..utils.logger import setup_logger
from ..utils.exceptions import AudioValidationError, ProfileNotFoundError

logger = setup_logger("voiceclone.service.voice")

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".webm", ".m4a"}
MAX_DURATION_SEC = 300  # 5 minutes


def _validate_audio_extension(filename: str) -> None:
    """Validate audio file extension."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise AudioValidationError(
            f"Unsupported format '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )


def _convert_to_wav(input_path: Path) -> Path:
    """Convert any audio format to a clean WAV file for AI model compatibility.

    AI models (F5-TTS, XTTS v2, Fish Speech) require:
    - Format: PCM WAV
    - Sample rate: 22050 Hz
    - Channels: Mono (1)
    - Bit depth: 16-bit

    Browser recordings arrive as .webm (Opus codec) and must be converted.
    If input is already a valid WAV, it is re-encoded to ensure correct specs.
    Requires ffmpeg to be installed on the system.
    """
    output_path = input_path.with_suffix(".wav")

    # If input is already a WAV with the right name, still re-encode to
    # guarantee sample rate / channel / format are correct.
    if input_path == output_path:
        # Encode to a temp file first to avoid reading/writing the same file
        tmp_path = input_path.with_stem(input_path.stem + "_tmp")
        final_input = input_path
        final_output = tmp_path
    else:
        final_input = input_path
        final_output = output_path

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",                    # overwrite without asking
                "-i", str(final_input),  # input file
                "-ar", "22050",          # sample rate (22kHz — F5-TTS default)
                "-ac", "1",              # mono
                "-sample_fmt", "s16",    # 16-bit PCM
                "-f", "wav",             # force WAV output
                str(final_output),
            ],
            capture_output=True,
            timeout=60,
        )

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="ignore")
            raise AudioValidationError(
                f"Audio conversion failed (ffmpeg error): {stderr[-500:]}"
            )

        # If we wrote to a temp file, replace the original
        if final_output != output_path:
            final_output.replace(output_path)

        # Remove the original non-WAV file to save disk space
        if input_path != output_path and input_path.exists():
            input_path.unlink()

        logger.info(f"Converted audio to WAV: {output_path}")
        return output_path

    except FileNotFoundError:
        # ffmpeg not installed — log a warning and keep original
        logger.warning(
            "⚠️  ffmpeg not found — skipping audio conversion. "
            "Install ffmpeg for browser recording support: "
            "Ubuntu: apt-get install ffmpeg | Windows: winget install ffmpeg"
        )
        return input_path
    except subprocess.TimeoutExpired:
        raise AudioValidationError("Audio conversion timed out (file may be too large).")


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
