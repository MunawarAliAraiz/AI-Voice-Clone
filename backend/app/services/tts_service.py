"""
AI Voice Clone Studio — TTS Orchestration Service
"""

import asyncio
import time
from pathlib import Path

from ..engines import get_engine, select_engine_for_language, EngineRegistry
from ..database import get_db, close_db
from ..config import settings
from ..utils.logger import setup_logger
from ..utils.exceptions import ProfileNotFoundError, GenerationError

logger = setup_logger("voiceclone.service.tts")



def _convert_wav_to_mp3(wav_path: Path) -> Path:
    """Convert a WAV file to MP3 using pydub (requires ffmpeg).

    Returns the MP3 path on success, or the original WAV path if pydub
    is not installed (graceful fallback — never breaks generation).
    """
    mp3_path = wav_path.with_suffix(".mp3")
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_wav(str(wav_path))
        audio.export(str(mp3_path), format="mp3", bitrate="192k")
        # Remove the WAV to save disk space
        wav_path.unlink()
        logger.info(f"Converted to MP3: {mp3_path}")
        return mp3_path
    except ImportError:
        logger.warning(
            "pydub not installed — returning WAV instead of MP3. "
            "Install with: pip install pydub"
        )
        return wav_path
    except Exception as e:
        logger.warning(f"MP3 conversion failed ({e}) — returning WAV instead.")
        return wav_path


from ..utils.emotion_engine import EmotionEngine
from ..utils.style_manager import StyleManager


async def generate_speech(
    text: str,
    profile_id: int,
    language: str = "en",
    engine_name: str = "auto",
    output_format: str = "wav",
    emotion: str = "neutral",
    style: str = "default",
) -> dict:
    """Generate speech from text using a voice profile.

    Args:
        text: Text to convert to speech
        profile_id: Voice profile to use
        language: Target language ('en', 'ur', 'hi')
        engine_name: Engine to use ('auto', 'f5_tts', 'fish_speech', 'xtts_v2', 'mock')
        output_format: Output format ('wav', 'mp3')
        emotion: Target emotion ('neutral', 'happy', 'sad', 'angry', 'calm', 'excited', 'narration')
        style: Style preset ('default', 'youtube', 'podcast', 'audiobook', 'storytelling', 'news', 'educational', 'gaming', 'corporate')

    Returns:
        Dict with generation results
    """
    # Validate emotion & style
    norm_emotion = EmotionEngine.validate_emotion(emotion)
    style_preset = StyleManager.get_style(style)

    # Preprocess text according to style rules (sentence splitting, pause injection, punctuation)
    processed_text = StyleManager.preprocess_text(text, style_name=style_preset.name)

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

    # Load engine if not loaded (with automatic VRAM offloading for GPU safety)
    if not info.is_loaded:
        from ..utils.gpu import get_gpu_info
        gpu = get_gpu_info()
        device = gpu.device if gpu.available else "cpu"
        engine = await EngineRegistry.manage_vram_and_load(engine_name, device=device)

    # Check if active engine supports native emotion mode
    native_emotion_supported = hasattr(engine, "supports_emotion") and engine.supports_emotion(norm_emotion)
    logger.info(
        f"Generating: engine={engine_name}, lang={language}, profile={profile['name']}, "
        f"emotion={norm_emotion} (Native: {native_emotion_supported}), style={style_preset.name}"
    )

    engine_kwargs = {
        "text": processed_text,
        "reference_audio": reference_audio,
        "language": language,
        "reference_text": profile.get("transcript"),
    }
    if native_emotion_supported:
        engine_kwargs["emotion"] = norm_emotion

    result = await engine.generate(**engine_kwargs)


    final_path = result.output_path
    native_applied = native_emotion_supported and norm_emotion != "neutral"
    degraded = False

    # If engine does not support native emotion, apply graceful acoustic adaptation fallback
    if not native_emotion_supported and norm_emotion != "neutral":
        emotion_res = EmotionEngine.apply_acoustic_adaptation(
            input_audio_path=result.output_path,
            emotion=norm_emotion,
            sample_rate=result.sample_rate,
        )
        final_path = emotion_res.output_path
        degraded = emotion_res.degraded
        native_applied = emotion_res.native_applied

    # Apply Style Audio Adjustments (Speaking rate tempo multiplier & pitch prosody)
    if style_preset.name != "default":
        final_path = StyleManager.apply_style_audio(
            input_audio_path=final_path,
            style_name=style_preset.name,
            sample_rate=result.sample_rate,
        )

    # Convert to MP3 if requested
    if output_format == "mp3":
        final_path = _convert_wav_to_mp3(final_path)
        output_format = final_path.suffix.lstrip(".")



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
                str(final_path),
                output_format,
                result.duration_sec,
                result.gen_time_sec,
            ),
        )
        await db.commit()
        history_id = cursor.lastrowid
    finally:
        await close_db(db)

    logger.info(f"[OK] Generation #{history_id} complete: {result.duration_sec:.1f}s audio in {result.gen_time_sec:.2f}s")


    return {
        "id": history_id,
        "output_path": str(final_path),
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
