"""
AI Voice Clone Studio — TTS Orchestration Service
"""

import time
from pathlib import Path

from ..engines import get_engine, select_engine_for_language
from ..database import get_db, close_db
from ..config import settings
from ..utils.logger import setup_logger
from ..utils.exceptions import ProfileNotFoundError, GenerationError

logger = setup_logger("voiceclone.service.tts")


async def generate_speech(
    text: str,
    profile_id: int,
    language: str = "en",
    engine_name: str = "auto",
    output_format: str = "wav",
) -> dict:
    """Generate speech from text using a voice profile.

    Args:
        text: Text to convert to speech
        profile_id: Voice profile to use
        language: Target language ('en', 'ur', 'hi')
        engine_name: Engine to use ('auto', 'f5_tts', 'fish_speech', 'xtts_v2', 'mock')
        output_format: Output format ('wav', 'mp3')

    Returns:
        Dict with generation results
    """
    # Get voice profile
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM voice_profiles WHERE id = ? AND is_active = 1",
            (profile_id,),
        )
        profile = await cursor.fetchone()
        if profile is None:
            raise ProfileNotFoundError(profile_id)
        profile = dict(profile)
    finally:
        await close_db(db)

    reference_audio = Path(profile["audio_path"])
    if not reference_audio.exists():
        raise GenerationError(f"Profile audio file missing: {reference_audio}")

    # Select engine
    if engine_name == "auto":
        engine_name = select_engine_for_language(language)

    engine = get_engine(engine_name)
    info = engine.get_info()

    # Load engine if not loaded
    if not info.is_loaded:
        from ..utils.gpu import get_gpu_info
        gpu = get_gpu_info()
        device = gpu.device if gpu.available else "cpu"
        await engine.load_model(device=device)

    # Generate
    logger.info(f"Generating: engine={engine_name}, lang={language}, profile={profile['name']}")
    start_time = time.time()

    result = await engine.generate(
        text=text,
        reference_audio=reference_audio,
        language=language,
        reference_text=profile.get("transcript"),
    )

    # Save to history
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO generation_history
               (profile_id, input_text, language, engine, output_path, output_format, duration_sec, gen_time_sec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_id,
                text,
                language,
                engine_name,
                str(result.output_path),
                output_format,
                result.duration_sec,
                result.gen_time_sec,
            ),
        )
        await db.commit()
        history_id = cursor.lastrowid
    finally:
        await close_db(db)

    logger.info(f"✅ Generation #{history_id} complete: {result.duration_sec:.1f}s audio in {result.gen_time_sec:.2f}s")

    return {
        "id": history_id,
        "output_path": str(result.output_path),
        "duration_sec": result.duration_sec,
        "gen_time_sec": result.gen_time_sec,
        "engine": engine_name,
        "language": language,
    }


async def get_supported_languages() -> list[dict]:
    """Get all supported languages with their available engines."""
    languages = {
        "en": {"code": "en", "name": "English", "engines": ["f5_tts", "fish_speech", "xtts_v2", "mock"]},
        "ur": {"code": "ur", "name": "Urdu (اردو)", "engines": ["fish_speech", "mock"]},
        "hi": {"code": "hi", "name": "Hindi (हिन्दी)", "engines": ["f5_tts", "xtts_v2", "fish_speech", "mock"]},
    }
    return list(languages.values())
