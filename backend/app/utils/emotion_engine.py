"""
AI Voice Clone Studio — Independent Emotion Engine

Provides emotion processing across 7 supported emotion modes:
- neutral
- happy
- sad
- angry
- calm
- excited
- narration

Truthfully differentiates native model emotion support from acoustic signal adaptation fallback.
Never fakes native model capability while ensuring graceful fallback delivery.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional

from .logger import setup_logger
from .exceptions import VoiceCloneError

logger = setup_logger("voiceclone.emotion.engine")

ALL_EMOTION_MODES = [
    "neutral",
    "happy",
    "sad",
    "angry",
    "calm",
    "excited",
    "narration",
]


class EmotionError(VoiceCloneError):
    """Raised when emotion processing fails."""
    def __init__(self, message: str):
        super().__init__(message, code="EMOTION_PROCESSING_ERROR")


@dataclass
class EmotionProcessingResult:
    """Result of emotion processing pass."""
    applied_emotion: str
    native_applied: bool
    degraded: bool
    output_path: Path
    metadata: Dict[str, Any]


class EmotionEngine:
    """Independent Emotion Engine with native support detection & acoustic fallback."""

    @staticmethod
    def get_supported_emotions() -> List[str]:
        """Get list of all supported emotion modes."""
        return list(ALL_EMOTION_MODES)

    @staticmethod
    def validate_emotion(emotion: str) -> str:
        """Validate and normalize requested emotion name."""
        norm = emotion.lower().strip()
        if norm not in ALL_EMOTION_MODES:
            logger.warning(f"Unsupported emotion '{emotion}' requested. Falling back to 'neutral'.")
            return "neutral"
        return norm

    @staticmethod
    def build_acoustic_filter(emotion: str, sample_rate: int = 22050) -> List[str]:
        """Build FFmpeg acoustic filter graph for graceful fallback adaptation."""
        filters = []
        sr = max(8000, min(96000, sample_rate))

        if emotion == "happy":
            # Pitch shift up ~1.2 semitones via sample rate shift + tempo compensation
            filters.append(f"asetrate={int(sr * 1.07)},atempo=1.04,equalizer=f=3500:width_type=h:width=1000:g=2")
        elif emotion == "sad":
            # Pitch shift down ~1.5 semitones, slower tempo, softer volume
            filters.append(f"asetrate={int(sr * 0.92)},atempo=0.93,volume=-1.5dB")
        elif emotion == "angry":
            # Loudness boost, punchy tempo, mid-bass EQ
            filters.append(f"volume=+2.5dB,asetrate={int(sr * 1.03)},atempo=1.08,equalizer=f=250:width_type=h:width=150:g=3")
        elif emotion == "calm":
            # Relaxed tempo, soft volume, gentle low-pass warmth
            filters.append(f"asetrate={int(sr * 0.96)},atempo=0.91,volume=-1.0dB,lowpass=f=3800")
        elif emotion == "excited":
            # Energetic pitch & tempo boost, treble enhancement
            filters.append(f"asetrate={int(sr * 1.10)},atempo=1.12,volume=+1.5dB,equalizer=f=4000:width_type=h:width=1000:g=3")
        elif emotion == "narration":
            # Clear mid-range EQ, steady narration cadence
            filters.append("equalizer=f=2000:width_type=h:width=500:g=1.5,volume=+0.5dB")

        return filters

    @classmethod
    def apply_acoustic_adaptation(
        cls,
        input_audio_path: Path,
        emotion: str,
        output_audio_path: Optional[Path] = None,
        sample_rate: int = 22050,
    ) -> EmotionProcessingResult:
        """Apply graceful acoustic signal adaptation for engines without native emotion support."""
        norm_emotion = cls.validate_emotion(emotion)

        if output_audio_path is None:
            output_audio_path = input_audio_path.with_stem(input_audio_path.stem + f"_{norm_emotion}")

        # Neutral or empty filter requires no post-processing
        filters = cls.build_acoustic_filter(norm_emotion, sample_rate=sample_rate)
        if norm_emotion == "neutral" or not filters:
            return EmotionProcessingResult(
                applied_emotion=norm_emotion,
                native_applied=False,
                degraded=False,
                output_path=input_audio_path,
                metadata={"note": "Neutral emotion — original audio retained"},
            )

        # Check FFmpeg availability
        from .audio_pipeline import FFmpegEngine
        if not FFmpegEngine.is_ffmpeg_available():
            logger.warning(f"FFmpeg unavailable for acoustic emotion adaptation ('{norm_emotion}'). Returning original.")
            return EmotionProcessingResult(
                applied_emotion=norm_emotion,
                native_applied=False,
                degraded=True,
                output_path=input_audio_path,
                metadata={"note": "FFmpeg missing — acoustic adaptation skipped"},
            )

        # Run FFmpeg acoustic adaptation pass
        try:
            FFmpegEngine.run_ffmpeg_pipeline(
                input_path=input_audio_path,
                output_path=output_audio_path,
                audio_filters=filters,
                sample_rate=sample_rate,
            )
            logger.info(f"✅ Acoustic emotion adaptation applied ('{norm_emotion}') -> {output_audio_path}")
            return EmotionProcessingResult(
                applied_emotion=norm_emotion,
                native_applied=False,
                degraded=True,
                output_path=output_audio_path,
                metadata={"filters_applied": filters, "mode": "acoustic_adaptation_fallback"},
            )
        except Exception as err:
            logger.warning(f"Acoustic adaptation failed ({err}) — returning original audio.")
            return EmotionProcessingResult(
                applied_emotion=norm_emotion,
                native_applied=False,
                degraded=True,
                output_path=input_audio_path,
                metadata={"error": str(err)},
            )
