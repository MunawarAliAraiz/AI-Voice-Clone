"""
AI Voice Clone Studio — Reusable Style Manager

Provides 8 production style presets:
- YouTube
- Podcast
- Audiobook
- Storytelling
- News
- Educational
- Gaming
- Corporate

Each preset controls:
- Prosody (Pitch emphasis & tone)
- Speaking Rate (Tempo multiplier)
- Sentence Splitting (Chunking rules)
- Punctuation (Cadence & emphasis formatting)
- Pause (Pause duration marker injection)

100% decoupled from TTS engines.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Any, Optional

from .logger import setup_logger
from .exceptions import VoiceCloneError

logger = setup_logger("voiceclone.style.manager")


class StyleError(VoiceCloneError):
    """Raised when style processing fails."""
    def __init__(self, message: str):
        super().__init__(message, code="STYLE_PROCESSING_ERROR")


@dataclass
class StylePreset:
    """Definition of a speech style preset."""
    name: str
    display_name: str
    description: str
    speaking_rate: float        # Tempo multiplier (0.8x to 1.3x)
    pitch_semitones: float      # Pitch shift guidance in semitones
    sentence_splitting: str     # 'by_sentence', 'by_clause', 'short_chunks', 'none'
    punctuation_emphasis: str   # 'emphatic', 'natural', 'dramatic', 'formal', 'punchy'
    pause_duration_ms: int      # Suggested pause duration between chunks
    pause_marker: str           # Marker string injected for pause shaping (e.g. '...', ', ', ' — ')


STYLE_PRESETS: Dict[str, StylePreset] = {
    "default": StylePreset(
        name="default",
        display_name="Default (Standard)",
        description="Standard natural voice delivery without style modification",
        speaking_rate=1.00,
        pitch_semitones=0.0,
        sentence_splitting="by_sentence",
        punctuation_emphasis="natural",
        pause_duration_ms=300,
        pause_marker=", ",
    ),
    "youtube": StylePreset(
        name="youtube",
        display_name="YouTube (Enthusiastic)",
        description="High-energy, fast-paced delivery with punchy cadence for video content",
        speaking_rate=1.10,
        pitch_semitones=0.8,
        sentence_splitting="short_chunks",
        punctuation_emphasis="punchy",
        pause_duration_ms=180,
        pause_marker="! ",
    ),
    "podcast": StylePreset(
        name="podcast",
        display_name="Podcast (Conversational)",
        description="Relaxed, natural conversational flow with organic breathing pauses",
        speaking_rate=1.00,
        pitch_semitones=0.0,
        sentence_splitting="by_clause",
        punctuation_emphasis="natural",
        pause_duration_ms=350,
        pause_marker="... ",
    ),
    "audiobook": StylePreset(
        name="audiobook",
        display_name="Audiobook (Expressive)",
        description="Immersive, articulate storytelling pace with rich prosody dynamics",
        speaking_rate=0.95,
        pitch_semitones=-0.2,
        sentence_splitting="by_clause",
        punctuation_emphasis="dramatic",
        pause_duration_ms=450,
        pause_marker=" ... ",
    ),
    "storytelling": StylePreset(
        name="storytelling",
        display_name="Storytelling (Dramatic)",
        description="Captivating narrative cadence with strategic dramatic pauses",
        speaking_rate=0.92,
        pitch_semitones=-0.4,
        sentence_splitting="by_clause",
        punctuation_emphasis="dramatic",
        pause_duration_ms=500,
        pause_marker=" ... ",
    ),
    "news": StylePreset(
        name="news",
        display_name="News (Formal Broadcast)",
        description="Crisp, authoritative broadcast delivery with precise sentence boundaries",
        speaking_rate=1.02,
        pitch_semitones=0.0,
        sentence_splitting="by_sentence",
        punctuation_emphasis="formal",
        pause_duration_ms=250,
        pause_marker=". ",
    ),
    "educational": StylePreset(
        name="educational",
        display_name="Educational (Explanatory)",
        description="Clear, steady instructional tone with conceptual pauses for comprehension",
        speaking_rate=0.95,
        pitch_semitones=0.2,
        sentence_splitting="by_sentence",
        punctuation_emphasis="natural",
        pause_duration_ms=400,
        pause_marker=" ... ",
    ),
    "gaming": StylePreset(
        name="gaming",
        display_name="Gaming (High Energy)",
        description="Hyper-energetic, rapid stream style with maximum hype emphasis",
        speaking_rate=1.15,
        pitch_semitones=1.2,
        sentence_splitting="short_chunks",
        punctuation_emphasis="punchy",
        pause_duration_ms=150,
        pause_marker="! ",
    ),
    "corporate": StylePreset(
        name="corporate",
        display_name="Corporate (Professional)",
        description="Polished, executive presentation style with balanced cadence",
        speaking_rate=1.00,
        pitch_semitones=0.0,
        sentence_splitting="by_sentence",
        punctuation_emphasis="formal",
        pause_duration_ms=300,
        pause_marker=", ",
    ),
}


class StyleManager:
    """Independent Style Manager utility."""

    @staticmethod
    def list_styles() -> List[Dict[str, Any]]:
        """List all available style presets."""
        return [
            {
                "name": p.name,
                "display_name": p.display_name,
                "description": p.description,
                "speaking_rate": p.speaking_rate,
                "sentence_splitting": p.sentence_splitting,
                "punctuation_emphasis": p.punctuation_emphasis,
            }
            for p in STYLE_PRESETS.values()
        ]

    @staticmethod
    def get_style(name: str) -> StylePreset:
        """Get a style preset by name. Falls back to 'default' if unknown."""
        key = name.lower().strip()
        if key not in STYLE_PRESETS:
            logger.warning(f"Unknown style '{name}' requested. Falling back to 'default'.")
            return STYLE_PRESETS["default"]
        return STYLE_PRESETS[key]

    @classmethod
    def preprocess_text(cls, text: str, style_name: str) -> str:
        """Preprocess text input according to sentence splitting, punctuation, and pause rules."""
        style = cls.get_style(style_name)
        text = text.strip()
        if not text or style.name == "default":
            return text

        # 1. Punctuation & Emphasis Formatting
        if style.punctuation_emphasis == "punchy":
            # Add exclamation marks to short clauses if missing
            text = re.sub(r"([a-zA-Z0-9]+)\.\s", r"\1! ", text)
        elif style.punctuation_emphasis == "dramatic":
            # Insert pause markers after commas & semicolons
            text = re.sub(r"[,;]\s*", style.pause_marker, text)

        # 2. Sentence Splitting & Pause Injection
        if style.sentence_splitting == "short_chunks":
            # Break long compound sentences into shorter punchy chunks
            text = re.sub(r"( and | or | but )\s*", r", \1", text, flags=re.IGNORECASE)

        return text

    @classmethod
    def apply_style_audio(
        cls,
        input_audio_path: Path,
        style_name: str,
        output_audio_path: Optional[Path] = None,
        sample_rate: int = 22050,
    ) -> Path:
        """Apply speaking rate (tempo) and pitch prosody adjustments to audio output."""
        style = cls.get_style(style_name)
        if style.name == "default" or (style.speaking_rate == 1.0 and style.pitch_semitones == 0.0):
            return input_audio_path

        if output_audio_path is None:
            output_audio_path = input_audio_path.with_stem(input_audio_path.stem + f"_{style.name}")

        from .audio_pipeline import FFmpegEngine
        if not FFmpegEngine.is_ffmpeg_available():
            logger.warning("FFmpeg unavailable for style audio adjustment. Returning original.")
            return input_audio_path

        filters = []
        # Speaking rate (tempo adjustment via atempo filter)
        if style.speaking_rate != 1.0:
            tempo = max(0.5, min(2.0, style.speaking_rate))
            filters.append(f"atempo={tempo}")

        # Pitch adjustment via asetrate filter if semitones specified
        if style.pitch_semitones != 0.0:
            pitch_factor = 2.0 ** (style.pitch_semitones / 12.0)
            new_sr = int(sample_rate * pitch_factor)
            # Compensate tempo after asetrate shift
            comp_tempo = 1.0 / pitch_factor
            filters.insert(0, f"asetrate={new_sr},atempo={comp_tempo:.4f}")

        try:
            FFmpegEngine.run_ffmpeg_pipeline(
                input_path=input_audio_path,
                output_path=output_audio_path,
                audio_filters=filters,
                sample_rate=sample_rate,
            )
            logger.info(f"✅ Applied style '{style.name}' (Rate={style.speaking_rate}x) -> {output_audio_path}")
            return output_audio_path
        except Exception as err:
            logger.warning(f"Style audio adjustment failed ({err}) — returning original.")
            return input_audio_path
