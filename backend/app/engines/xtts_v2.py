"""
AI Voice Clone Studio — XTTS v2 Engine Implementation

XTTS v2 is the fallback engine. Mature, stable, good Hindi support.
Requires fine-tuning for Urdu.
"""

import time
from pathlib import Path
from datetime import datetime

from .base import TTSEngine, EngineInfo, GenerationResult
from ..config import settings
from ..utils.logger import setup_logger
from ..utils.exceptions import EngineLoadError, GenerationError

logger = setup_logger("voiceclone.engine.xtts_v2")


class XTTSv2Engine(TTSEngine):
    """XTTS v2 voice cloning engine (via coqui-tts)."""

    def __init__(self):
        self._tts = None
        self._loaded = False
        self._device = "cpu"

    def get_info(self) -> EngineInfo:
        return EngineInfo(
            name="xtts_v2",
            display_name="XTTS v2",
            version="2.0",
            description="Mature voice cloning engine. Good Hindi support, needs fine-tuning for Urdu.",
            supported_languages=[
                "en", "hi", "zh", "ja", "ko", "ar", "fr", "de",
                "es", "pt", "ru", "tr", "it", "nl", "pl", "cs", "hu",
            ],
            requires_gpu=True,
            model_size_mb=5000,
            is_loaded=self._loaded,
        )

    async def load_model(self, device: str = "cpu") -> None:
        self._device = device
        try:
            from TTS.api import TTS

            logger.info(f"Loading XTTS v2 on {device}...")
            self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
            self._loaded = True
            logger.info("✅ XTTS v2 model loaded")

        except ImportError:
            raise EngineLoadError(
                "xtts_v2",
                "coqui-tts package not installed. Run: uv add coqui-tts"
            )
        except Exception as e:
            raise EngineLoadError("xtts_v2", str(e))

    async def unload_model(self) -> None:
        if self._tts is not None:
            del self._tts
            self._tts = None
            self._loaded = False
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
            logger.info("XTTS v2 model unloaded")

    async def generate(
        self,
        text: str,
        reference_audio: Path,
        language: str = "en",
        output_path: Path | None = None,
        reference_text: str | None = None,
    ) -> GenerationResult:
        if not self._loaded or self._tts is None:
            raise GenerationError("XTTS v2 model not loaded.")

        if not reference_audio.exists():
            raise GenerationError(f"Reference audio not found: {reference_audio}")

        start_time = time.time()
        logger.info(f"Generating with XTTS v2: '{text[:50]}...' [{language}]")

        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = settings.generated_dir / f"xtts_{timestamp}.wav"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._tts.tts_to_file(
                text=text,
                speaker_wav=str(reference_audio),
                language=language,
                file_path=str(output_path),
            )

            gen_time = time.time() - start_time

            # Get duration from output file
            import wave
            with wave.open(str(output_path), "r") as wf:
                duration_sec = wf.getnframes() / wf.getframerate()

            logger.info(f"✅ XTTS v2 generated: {duration_sec:.1f}s in {gen_time:.2f}s")

            return GenerationResult(
                output_path=output_path,
                duration_sec=duration_sec,
                gen_time_sec=gen_time,
                sample_rate=22050,
                engine="xtts_v2",
            )

        except Exception as e:
            raise GenerationError(f"XTTS v2 generation failed: {e}")

    def get_supported_languages(self) -> list[str]:
        return [
            "en", "hi", "zh", "ja", "ko", "ar", "fr", "de",
            "es", "pt", "ru", "tr", "it", "nl", "pl", "cs", "hu",
        ]
