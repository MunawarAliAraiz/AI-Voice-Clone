"""
AI Voice Clone Studio — Mock TTS Engine

Returns pre-generated silent audio for development/testing without GPU.
This allows full UI development and API testing on machines without NVIDIA GPUs.
"""

import time
import wave
import struct
import math
from pathlib import Path
from datetime import datetime

from .base import TTSEngine, EngineInfo, GenerationResult
from .registry import register_engine
from ..config import settings
from ..utils.logger import setup_logger

logger = setup_logger("voiceclone.engine.mock")


@register_engine("mock")
class MockTTSEngine(TTSEngine):
    """Mock TTS engine that generates a sine-wave tone for testing."""

    def __init__(self):
        self._loaded = False

    def get_info(self) -> EngineInfo:
        return EngineInfo(
            name="mock",
            display_name="Mock Engine (Development)",
            version="1.0.0",
            description="Generates test audio for development. No GPU required.",
            supported_languages=["en", "ur", "hi"],
            requires_gpu=False,
            model_size_mb=0,
            is_loaded=self._loaded,
        )

    async def load_model(self, device: str = "cpu") -> None:
        logger.info("Loading mock TTS engine (instant)")
        self._loaded = True

    async def unload_model(self) -> None:
        self._loaded = False
        logger.info("Mock TTS engine unloaded")

    async def generate(
        self,
        text: str,
        reference_audio: Path,
        language: str = "en",
        output_path: Path | None = None,
        reference_text: str | None = None,
        emotion: str = "neutral",
        style: str | None = None,
        **kwargs,
    ) -> GenerationResult:
        start_time = time.time()
        logger.info(f"Mock generating: '{text[:50]}...' [{language}]")

        # Estimate duration: ~0.3 seconds per word
        word_count = len(text.split())
        duration_sec = max(1.0, word_count * 0.3)

        # Generate output path if not provided
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = settings.generated_dir / f"mock_{timestamp}.wav"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Generate a simple sine wave as placeholder audio
        sample_rate = 22050
        num_samples = int(duration_sec * sample_rate)
        frequency = 440.0  # A4 note

        with wave.open(str(output_path), "w") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(sample_rate)

            for i in range(num_samples):
                # Sine wave with fade in/out envelope
                t = i / sample_rate
                envelope = min(t / 0.05, 1.0) * min((duration_sec - t) / 0.05, 1.0)
                envelope = max(0.0, envelope)
                sample = int(
                    16000 * envelope * math.sin(2.0 * math.pi * frequency * t)
                )
                wav_file.writeframes(struct.pack("<h", max(-32768, min(32767, sample))))

        gen_time = time.time() - start_time
        logger.info(f"Mock generated: {output_path} ({duration_sec:.1f}s audio in {gen_time:.2f}s)")

        return GenerationResult(
            output_path=output_path,
            duration_sec=duration_sec,
            gen_time_sec=gen_time,
            sample_rate=sample_rate,
            engine="mock",
            metadata={"word_count": word_count, "note": "Mock audio — sine wave placeholder"},
        )

    def get_supported_languages(self) -> list[str]:
        return ["en", "ur", "hi"]
