"""
AI Voice Clone Studio — Production XTTS v2 Engine Implementation

XTTS v2 is a high-quality zero-shot multilingual voice cloning engine.
Provides support for English, Hindi, Urdu, and 15+ languages with automatic
model download, GPU memory management, CPU fallback, and health checks.
"""

import gc
import shutil
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from .base import TTSEngine, EngineInfo, GenerationResult
from .registry import register_engine
from ..config import settings
from ..utils.logger import setup_logger
from ..utils.exceptions import (
    EngineLoadError,
    GenerationError,
    ModelNotDownloadedError,
    VRAMExhaustedError,
)

logger = setup_logger("voiceclone.engine.xtts_v2")

# HF Repo for Coqui XTTS v2
XTTS_HF_REPO = "coqui/XTTS-v2"
REQUIRED_CHECKPOINT_FILES = ["config.json", "vocab.json"]


@register_engine("xtts_v2")
class XTTSv2Engine(TTSEngine):
    """Production-grade XTTS v2 voice cloning engine."""

    def __init__(self):
        self._tts = None
        self._loaded: bool = False
        self._device: str = "cpu"
        self._model_dir: Path = settings.models_dir / "xtts_v2"

    def get_info(self) -> EngineInfo:
        """Get XTTS v2 engine metadata."""
        return EngineInfo(
            name="xtts_v2",
            display_name="XTTS v2",
            version="2.0.2",
            description="Mature multilingual zero-shot voice cloning engine with Urdu, Hindi, and English support.",
            supported_languages=[
                "en", "ur", "hi", "zh", "ja", "ko", "ar", "fr", "de",
                "es", "pt", "ru", "tr", "it", "nl", "pl", "cs", "hu",
            ],
            requires_gpu=True,
            model_size_mb=4500,
            is_loaded=self._loaded,
        )

    def _verify_checkpoint_integrity(self) -> bool:
        """Check if local model directory exists and contains valid checkpoint files."""
        if not self._model_dir.exists():
            return False

        # Must contain config.json and vocab.json
        for fname in REQUIRED_CHECKPOINT_FILES:
            if not (self._model_dir / fname).exists():
                logger.warning(f"XTTS v2 checkpoint file missing: {fname}")
                return False

        # Must contain at least one model weights file (.pth or .bin)
        weights_found = any(self._model_dir.glob("*.pth")) or any(self._model_dir.glob("*.bin"))
        if not weights_found:
            logger.warning("XTTS v2 model weights file (.pth / .bin) missing")
            return False

        return True

    def _ensure_model_downloaded(self) -> Path:
        """Automatically download XTTS v2 model checkpoint if missing or corrupted."""
        self._model_dir.mkdir(parents=True, exist_ok=True)

        if self._verify_checkpoint_integrity():
            logger.info(f"XTTS v2 model checkpoint verified at {self._model_dir}")
            return self._model_dir

        logger.info(f"Downloading/repairing XTTS v2 weights from HuggingFace ({XTTS_HF_REPO})...")

        try:
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id=XTTS_HF_REPO,
                local_dir=str(self._model_dir),
                local_dir_use_symlinks=False,
                resume_download=True,
            )

            if not self._verify_checkpoint_integrity():
                raise EngineLoadError("xtts_v2", "Downloaded XTTS v2 checkpoint failed integrity verification.")

            logger.info("✅ XTTS v2 model downloaded and verified successfully")
            return self._model_dir

        except Exception as e:
            logger.error(f"Failed to download XTTS v2 weights via huggingface_hub: {e}")

            # Fallback strategy: use coqui-tts built-in model download manager if available
            try:
                from TTS.api import TTS
                logger.info("Attempting fallback download via coqui-tts internal model manager...")
                tmp_tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False)
                del tmp_tts
                gc.collect()
                return self._model_dir
            except Exception as fallback_err:
                # If corrupted files remain, clean up directory for future attempts
                if self._model_dir.exists() and not self._verify_checkpoint_integrity():
                    logger.warning("Cleaning corrupted checkpoint directory...")
                    shutil.rmtree(self._model_dir, ignore_errors=True)

                raise ModelNotDownloadedError(f"XTTS v2 download failed: {e}. Fallback error: {fallback_err}")

    async def load_model(self, device: str = "cpu") -> None:
        """Lazily load XTTS v2 model with automatic GPU to CPU fallback."""
        if self._loaded and self._tts is not None and self._device == device:
            logger.info(f"XTTS v2 already loaded on device '{device}'")
            return

        # Ensure model weights exist
        model_path = self._ensure_model_downloaded()

        # Resolve requested device
        target_device = device.lower()
        if "cuda" in target_device:
            try:
                import torch
                if not torch.cuda.is_available():
                    logger.warning("CUDA requested for XTTS v2 but CUDA is not available. Falling back to CPU.")
                    target_device = "cpu"
            except ImportError:
                target_device = "cpu"

        self._device = target_device
        logger.info(f"Loading XTTS v2 engine on device '{target_device}'...")

        try:
            try:
                from TTS.api import TTS
            except ImportError as imp_err:
                raise EngineLoadError("xtts_v2", f"Failed to import TTS: {imp_err}. Run: pip install coqui-tts")

            # Attempt model load from local checkpoint directory or model name

            if self._verify_checkpoint_integrity():
                try:
                    from TTS.tts.configs.xtts_config import XttsConfig
                    from TTS.tts.models.xtts import Xtts

                    config = XttsConfig()
                    config.load_json(str(model_path / "config.json"))
                    model = Xtts.init_from_config(config)
                    model.load_checkpoint(config, checkpoint_dir=str(model_path), use_deepspeed=False)

                    if target_device.startswith("cuda"):
                        model.to(target_device)

                    self._tts = model
                    self._loaded_custom = True
                except Exception as custom_load_err:
                    logger.warning(f"Local Xtts direct load notice ({custom_load_err}), using TTS API interface...")
                    self._tts = TTS(model_path=str(model_path), config_path=str(model_path / "config.json")).to(target_device)
                    self._loaded_custom = False
            else:
                self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(target_device)
                self._loaded_custom = False

            self._loaded = True
            logger.info(f"✅ XTTS v2 model loaded successfully on '{target_device}'")

        except Exception as err:
            err_msg = str(err)
            logger.error(f"Failed to load XTTS v2 on '{target_device}': {err_msg}")

            # Handle CUDA Out Of Memory or initialization errors by falling back to CPU
            if target_device != "cpu" and any(k in err_msg.lower() for k in ["cuda", "out of memory", "oom", "nvml"]):
                logger.warning("⚠️ CUDA error/OOM detected during XTTS v2 load. Falling back to CPU...")
                await self.unload_model()
                self._device = "cpu"
                try:
                    from TTS.api import TTS
                    self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
                    self._loaded = True
                    logger.info("✅ XTTS v2 successfully recovered and loaded on CPU")
                    return
                except Exception as cpu_err:
                    raise EngineLoadError("xtts_v2", f"CPU fallback load failed: {cpu_err}")

            raise EngineLoadError("xtts_v2", f"Import or runtime error: {err_msg}")

    async def unload_model(self) -> None:
        """Unload model from RAM/VRAM and release hardware resources."""
        if self._tts is not None:
            del self._tts
            self._tts = None

        self._loaded = False
        gc.collect()

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()
        except ImportError:
            pass

        logger.info("XTTS v2 model unloaded and memory cleared")

    def _normalize_language_code(self, lang: str) -> str:
        """Map language codes for XTTS v2 compatibility (e.g. Urdu -> Hindi phonetizer)."""
        lang = lang.lower().strip()
        # XTTS v2 natively supports 17 languages: en, es, fr, de, it, pt, pl, tr, ru, nl, cs, ar, zh, ja, ko, hu, hi
        # For Urdu (ur), map to Hindi (hi) phonetizer compatible script mapping
        mapping = {
            "ur": "hi",
            "urdu": "hi",
            "hindi": "hi",
            "english": "en",
        }
        return mapping.get(lang, lang)

    async def generate(
        self,
        text: str,
        reference_audio: Path,
        language: str = "en",
        output_path: Optional[Path] = None,
        reference_text: Optional[str] = None,
        emotion: str = "neutral",
        style: Optional[str] = None,
        **kwargs,
    ) -> GenerationResult:
        """Generate cloned speech from text using reference audio."""
        if not self._loaded or self._tts is None:
            raise GenerationError("XTTS v2 model is not loaded. Call load_model() first.")

        if not reference_audio.exists():
            raise GenerationError(f"Reference audio file not found: {reference_audio}")

        start_time = time.time()
        normalized_lang = self._normalize_language_code(language)
        logger.info(f"Generating with XTTS v2: '{text[:40]}...' [Language: {language} -> {normalized_lang}]")

        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = settings.generated_dir / f"xtts_{timestamp}.wav"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            if getattr(self, "_loaded_custom", False) and hasattr(self._tts, "synthesize"):
                # Direct Xtts model synthesis call
                import torch
                gpt_cond_latent, speaker_embedding = self._tts.get_conditioning_latents(
                    audio_path=[str(reference_audio)]
                )
                out = self._tts.inference(
                    text=text,
                    language=normalized_lang,
                    gpt_cond_latent=gpt_cond_latent,
                    speaker_embedding=speaker_embedding,
                    temperature=0.7,
                )
                import torchaudio
                torchaudio.save(str(output_path), torch.tensor(out["wav"]).unsqueeze(0), 24000)
                sample_rate_out = 24000
            else:
                # TTS API synthesis call
                self._tts.tts_to_file(
                    text=text,
                    speaker_wav=str(reference_audio),
                    language=normalized_lang,
                    file_path=str(output_path),
                )
                sample_rate_out = 22050

            gen_time = time.time() - start_time

            # Compute generated audio duration
            duration_sec = 0.0
            try:
                with wave.open(str(output_path), "r") as wf:
                    duration_sec = wf.getnframes() / wf.getframerate()
                    sample_rate_out = wf.getframerate()
            except Exception:
                duration_sec = max(1.0, len(text.split()) * 0.3)

            logger.info(
                f"✅ XTTS v2 generated speech: {duration_sec:.1f}s audio in {gen_time:.2f}s"
            )

            return GenerationResult(
                output_path=output_path,
                duration_sec=duration_sec,
                gen_time_sec=gen_time,
                sample_rate=sample_rate_out,
                engine="xtts_v2",
                metadata={"original_language": language, "normalized_language": normalized_lang},
            )

        except Exception as e:
            err_msg = str(e)
            logger.error(f"XTTS v2 generation failed: {err_msg}")

            # Check for CUDA OOM during inference
            if "out of memory" in err_msg.lower() or "cuda" in err_msg.lower():
                raise VRAMExhaustedError("xtts_v2", 4500)

            raise GenerationError(f"XTTS v2 generation failed: {err_msg}")

    def get_supported_languages(self) -> List[str]:
        """Get supported language codes."""
        return [
            "en", "ur", "hi", "zh", "ja", "ko", "ar", "fr", "de",
            "es", "pt", "ru", "tr", "it", "nl", "pl", "cs", "hu",
        ]

    def health_check(self) -> Dict[str, Any]:
        """Perform comprehensive health check on XTTS v2 engine."""
        base_health = super().health_check()

        gpu_info = {"cuda_available": False, "gpu_name": None}
        try:
            import torch
            gpu_info["cuda_available"] = torch.cuda.is_available()
            if torch.cuda.is_available():
                gpu_info["gpu_name"] = torch.cuda.get_device_name(0)
                gpu_info["vram_allocated_mb"] = int(torch.cuda.memory_allocated() / (1024 * 1024))
        except ImportError:
            pass

        deps_ok = True
        try:
            import TTS
        except ImportError:
            deps_ok = False

        base_health.update({
            "device": self._device,
            "checkpoint_verified": self._verify_checkpoint_integrity(),
            "model_dir": str(self._model_dir),
            "dependencies_installed": deps_ok,
            "gpu_info": gpu_info,
        })
        return base_health
