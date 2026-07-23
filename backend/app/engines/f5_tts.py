"""
AI Voice Clone Studio — F5-TTS Engine Implementation

F5-TTS is the primary engine for English and Hindi voice cloning.
Uses zero-shot voice cloning with a short reference audio clip.
"""

import time
from pathlib import Path
from datetime import datetime

from .base import TTSEngine, EngineInfo, GenerationResult
from ..config import settings
from ..utils.logger import setup_logger
from ..utils.exceptions import EngineLoadError, GenerationError, ModelNotDownloadedError

logger = setup_logger("voiceclone.engine.f5tts")


class F5TTSEngine(TTSEngine):
    """F5-TTS voice cloning engine."""

    def __init__(self):
        self._model = None
        self._loaded = False
        self._device = "cpu"

    def get_info(self) -> EngineInfo:
        return EngineInfo(
            name="f5_tts",
            display_name="F5-TTS",
            version="1.0",
            description="High-quality zero-shot voice cloning. Best for English and Hindi.",
            supported_languages=["en", "hi", "zh", "ja", "ko", "fr", "de", "es"],
            requires_gpu=True,
            model_size_mb=3000,
            is_loaded=self._loaded,
        )

    async def load_model(self, device: str = "cpu") -> None:
        """Load F5-TTS model."""
        self._device = device
        try:
            # Import only when loading — allows app to start without f5-tts installed
            from f5_tts.api import F5TTS

            logger.info(f"Loading F5-TTS model on {device}...")
            self._model = F5TTS(device=device)
            self._loaded = True
            logger.info("✅ F5-TTS model loaded successfully")

        except ImportError as e:
            raise EngineLoadError(
                "f5_tts",
                f"Failed to import f5_tts: {e}. Run: pip install f5-tts"
            )
        except Exception as e:
            raise EngineLoadError("f5_tts", str(e))

    async def unload_model(self) -> None:
        """Unload model and free memory."""
        if self._model is not None:
            del self._model
            self._model = None
            self._loaded = False

            # Free GPU memory
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

            logger.info("F5-TTS model unloaded")

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

        """Generate speech using F5-TTS."""
        if not self._loaded or self._model is None:
            raise GenerationError("F5-TTS model not loaded. Call load_model() first.")

        if not reference_audio.exists():
            raise GenerationError(f"Reference audio not found: {reference_audio}")

        start_time = time.time()
        logger.info(f"Generating with F5-TTS: '{text[:50]}...' [{language}]")

        # Generate output path
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = settings.generated_dir / f"f5tts_{timestamp}.wav"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # F5-TTS inference
            wav, sr, _ = self._model.infer(
                ref_file=str(reference_audio),
                ref_text=reference_text or "",
                gen_text=text,
                file_wave=str(output_path),
            )

            gen_time = time.time() - start_time
            duration_sec = len(wav) / sr if wav is not None else 0

            logger.info(
                f"✅ F5-TTS generated: {duration_sec:.1f}s audio in {gen_time:.2f}s"
            )

            return GenerationResult(
                output_path=output_path,
                duration_sec=duration_sec,
                gen_time_sec=gen_time,
                sample_rate=sr,
                engine="f5_tts",
            )

        except Exception as e:
            raise GenerationError(f"F5-TTS generation failed: {e}")

    def get_supported_languages(self) -> list[str]:
        return ["en", "hi", "zh", "ja", "ko", "fr", "de", "es"]
