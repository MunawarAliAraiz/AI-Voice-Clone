"""
AI Voice Clone Studio — Fish Speech Engine Implementation

Fish Speech S2 is the primary engine for Urdu (and 80+ languages).
Zero-shot voice cloning with native multilingual support.
"""

import time
from pathlib import Path
from datetime import datetime

from .base import TTSEngine, EngineInfo, GenerationResult
from ..config import settings
from ..utils.logger import setup_logger
from ..utils.exceptions import EngineLoadError, GenerationError

logger = setup_logger("voiceclone.engine.fish_speech")


class FishSpeechEngine(TTSEngine):
    """Fish Speech S2 voice cloning engine."""

    def __init__(self):
        self._model = None
        self._loaded = False
        self._device = "cpu"

    def get_info(self) -> EngineInfo:
        return EngineInfo(
            name="fish_speech",
            display_name="Fish Speech S2",
            version="2.0",
            description="Multilingual voice cloning (80+ languages). Best for Urdu support.",
            supported_languages=[
                "en", "ur", "hi", "zh", "ja", "ko", "ar", "fr", "de",
                "es", "pt", "ru", "tr", "it", "nl", "pl", "sv",
            ],
            requires_gpu=True,
            model_size_mb=4000,
            is_loaded=self._loaded,
        )

    async def load_model(self, device: str = "cpu") -> None:
        self._device = device
        try:
            logger.info(f"Loading Fish Speech S2 on {device}...")

            # fish-speech >= 1.5 exposes a high-level TTS class.
            # Install with: pip install fish-speech
            # GitHub: https://github.com/fishaudio/fish-speech
            try:
                from fish_speech.inference_engine import TTSInferenceEngine

                # Determine checkpoint path — allow override via env or default to HuggingFace cache
                checkpoint_dir = settings.models_dir / "fish-speech-1.5"
                if not checkpoint_dir.exists():
                    logger.warning(
                        f"Fish Speech checkpoint not found at {checkpoint_dir}. "
                        "Attempting to load from HuggingFace (openfishproject/fish-speech-1.5)..."
                    )
                    checkpoint_dir_str = "openfishproject/fish-speech-1.5"
                else:
                    checkpoint_dir_str = str(checkpoint_dir)

                self._model = TTSInferenceEngine(
                    checkpoint=checkpoint_dir_str,
                    device=device,
                    compile=False,  # Set True for production speed after first run
                )
                self._api_version = "v1.5"

            except ImportError:
                # Fallback: try the older fish-speech API style (pre-1.5)
                try:
                    from tools.api import decode_vq_tokens, encode_reference
                    self._model = {"encode": encode_reference, "decode": decode_vq_tokens}
                    self._api_version = "legacy"
                    logger.warning("Loaded Fish Speech with legacy API (pre-1.5)")
                except ImportError:
                    raise EngineLoadError(
                        "fish_speech",
                        "fish-speech package not installed. "
                        "Run: pip install fish-speech  "
                        "GitHub: https://github.com/fishaudio/fish-speech"
                    )

            self._loaded = True
            logger.info("✅ Fish Speech S2 model loaded")

        except EngineLoadError:
            raise
        except Exception as e:
            raise EngineLoadError("fish_speech", str(e))

    async def unload_model(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
            self._loaded = False
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
            logger.info("Fish Speech model unloaded")

    async def generate(
        self,
        text: str,
        reference_audio: Path,
        language: str = "ur",
        output_path: Path | None = None,
        reference_text: str | None = None,
    ) -> GenerationResult:
        if not self._loaded or self._model is None:
            raise GenerationError("Fish Speech model not loaded. Call load_model() first.")

        if not reference_audio.exists():
            raise GenerationError(f"Reference audio not found: {reference_audio}")

        start_time = time.time()
        logger.info(f"Generating with Fish Speech: '{text[:50]}...' [{language}]")

        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = settings.generated_dir / f"fish_{timestamp}.wav"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            api_version = getattr(self, "_api_version", "v1.5")

            if api_version == "v1.5" and hasattr(self._model, "tts"):
                # High-level fish-speech >= 1.5 TTSInferenceEngine API
                self._model.tts(
                    text=text,
                    reference_audio=str(reference_audio),
                    reference_text=reference_text or "",
                    output=str(output_path),
                    language=language,
                )
            else:
                # Fallback: fish-speech generate() pipeline (common pattern)
                import soundfile as sf

                result_audio = self._model.generate(
                    text=text,
                    prompt_tokens=str(reference_audio),
                    prompt_text=reference_text or "",
                    language=language,
                    top_p=0.7,
                    repetition_penalty=1.5,
                    temperature=0.7,
                )

                # result_audio may be (samples, sr) tuple or a numpy array
                if isinstance(result_audio, tuple):
                    audio_data, sample_rate = result_audio
                else:
                    audio_data = result_audio
                    sample_rate = 44100

                sf.write(str(output_path), audio_data, sample_rate)

            gen_time = time.time() - start_time

            # Measure output duration
            import wave as wave_module
            try:
                with wave_module.open(str(output_path), "r") as wf:
                    duration_sec = wf.getnframes() / wf.getframerate()
                    sample_rate_out = wf.getframerate()
            except Exception:
                duration_sec = 0.0
                sample_rate_out = 44100

            logger.info(f"✅ Fish Speech generated: {duration_sec:.1f}s in {gen_time:.2f}s")

            return GenerationResult(
                output_path=output_path,
                duration_sec=duration_sec,
                gen_time_sec=gen_time,
                sample_rate=sample_rate_out,
                engine="fish_speech",
            )

        except GenerationError:
            raise
        except Exception as e:
            raise GenerationError(f"Fish Speech generation failed: {e}")

    def get_supported_languages(self) -> list[str]:
        return [
            "en", "ur", "hi", "zh", "ja", "ko", "ar", "fr", "de",
            "es", "pt", "ru", "tr", "it", "nl", "pl", "sv",
        ]
