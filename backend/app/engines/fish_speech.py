"""
AI Voice Clone Studio — Production Fish Speech Engine Implementation

Fish Speech S2 is a high-performance zero-shot multilingual voice cloning engine
with native support for Urdu (80+ languages), Hindi, and English.
Includes automatic Hugging Face checkpoint download, GPU memory management,
CPU fallback, and health checks.
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

logger = setup_logger("voiceclone.engine.fish_speech")

FISH_SPEECH_HF_REPO = "fishaudio/fish-speech-1.5"


def load_custom_codec_model(codec_checkpoint_path: str, device: str, precision):
    """Load DAC codec model with dynamic input_dim matching the checkpoint weights."""
    import fish_speech
    import torch
    from pathlib import Path
    from hydra.utils import instantiate
    from omegaconf import OmegaConf

    state_dict = torch.load(codec_checkpoint_path, map_location="cpu")
    if "state_dict" in state_dict:
        state_dict = state_dict["state_dict"]
    if any("generator" in k for k in state_dict):
        state_dict = {
            k.replace("generator.", ""): v
            for k, v in state_dict.items()
            if "generator." in k
        }

    config_path = Path(fish_speech.__file__).parent / "configs" / "modded_dac_vq.yaml"
    cfg = OmegaConf.load(str(config_path))

    # Auto-detect input_dim from state_dict weight shape if available
    downsample_key = "quantizer.downsample.0.0.conv.weight"
    if downsample_key in state_dict:
        dim = state_dict[downsample_key].shape[0]
        logger.info(f"Detected codec input_dim={dim} from checkpoint state_dict")
        cfg.quantizer.input_dim = dim
        if hasattr(cfg.quantizer, "post_module"):
            cfg.quantizer.post_module.input_dim = dim
        if hasattr(cfg.quantizer, "pre_module"):
            cfg.quantizer.pre_module.input_dim = dim

    codec = instantiate(cfg)
    codec.load_state_dict(state_dict, strict=False)
    codec.eval()
    codec.to(device=device, dtype=precision)
    return codec


@register_engine("fish_speech")
class FishSpeechEngine(TTSEngine):
    """Production-grade Fish Speech S2 voice cloning engine."""

    def __init__(self):
        self._model = None
        self._loaded: bool = False
        self._device: str = "cpu"
        self._api_version: str = "v1.5"
        self._checkpoint_dir: Path = settings.models_dir / "fish-speech-1.5"

    def get_info(self) -> EngineInfo:
        """Get Fish Speech engine metadata."""
        return EngineInfo(
            name="fish_speech",
            display_name="Fish Speech S2",
            version="2.0",
            description="Multilingual zero-shot voice cloning (80+ languages). Excellent native Urdu, Hindi, and English support.",
            supported_languages=[
                "en", "ur", "hi", "zh", "ja", "ko", "ar", "fr", "de",
                "es", "pt", "ru", "tr", "it", "nl", "pl", "sv",
            ],
            requires_gpu=True,
            model_size_mb=4000,
            is_loaded=self._loaded,
        )

    def _verify_checkpoint_integrity(self) -> bool:
        """Check if local checkpoint directory exists and contains model files."""
        if not self._checkpoint_dir.exists():
            return False

        # Look for model weights (.pth, .bin, or safetensors)
        weights_found = (
            any(self._checkpoint_dir.glob("*.pth"))
            or any(self._checkpoint_dir.glob("*.bin"))
            or any(self._checkpoint_dir.glob("*.safetensors"))
        )
        return weights_found

    def _ensure_model_downloaded(self) -> Path:
        """Automatically download Fish Speech weights if missing or corrupted."""
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

        if self._verify_checkpoint_integrity():
            logger.info(f"Fish Speech checkpoint verified at {self._checkpoint_dir}")
            return self._checkpoint_dir

        logger.info(f"Downloading Fish Speech 1.5 weights from HuggingFace ({FISH_SPEECH_HF_REPO})...")

        try:
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id=FISH_SPEECH_HF_REPO,
                local_dir=str(self._checkpoint_dir),
                local_dir_use_symlinks=False,
                resume_download=True,
            )

            if not self._verify_checkpoint_integrity():
                raise EngineLoadError("fish_speech", "Downloaded Fish Speech checkpoint failed integrity check.")

            logger.info("✅ Fish Speech model weights downloaded successfully")
            return self._checkpoint_dir

        except Exception as e:
            logger.error(f"Failed to download Fish Speech weights: {e}")
            if self._checkpoint_dir.exists() and not self._verify_checkpoint_integrity():
                shutil.rmtree(self._checkpoint_dir, ignore_errors=True)
            raise ModelNotDownloadedError(f"Fish Speech download failed: {e}")

    async def load_model(self, device: str = "cpu") -> None:
        """Lazily load Fish Speech model with automatic GPU to CPU fallback."""
        if self._loaded and self._model is not None and self._device == device:
            logger.info(f"Fish Speech already loaded on device '{device}'")
            return

        # Ensure model files are present
        checkpoint_dir = self._ensure_model_downloaded()

        # Resolve device
        target_device = device.lower()
        if "cuda" in target_device:
            try:
                import torch
                if not torch.cuda.is_available():
                    logger.warning("CUDA requested for Fish Speech but CUDA is not available. Falling back to CPU.")
                    target_device = "cpu"
            except ImportError:
                target_device = "cpu"

        self._device = target_device
        logger.info(f"Loading Fish Speech engine on device '{target_device}'...")

        try:
            import torch
            import inspect
            from fish_speech.inference_engine import TTSInferenceEngine

            init_params = inspect.signature(TTSInferenceEngine.__init__).parameters

            if "checkpoint" in init_params:
                # fish-speech <= 1.5 API
                self._model = TTSInferenceEngine(
                    checkpoint=str(checkpoint_dir),
                    device=target_device,
                    compile=False,
                )
                self._api_version = "v1.5"
            else:
                # fish-speech >= 2.0 API
                from fish_speech.models.text2semantic.inference import launch_thread_safe_queue

                codec_path = None
                for pth in checkpoint_dir.glob("*.pth"):
                    if any(k in pth.name.lower() for k in ["firefly", "vq", "codec", "generator"]):
                        codec_path = pth
                        break
                if codec_path is None:
                    pth_files = [f for f in checkpoint_dir.glob("*.pth") if f.name != "model.pth"]
                    codec_path = pth_files[0] if pth_files else (checkpoint_dir / "codec.pth")

                precision = (
                    torch.bfloat16
                    if "cuda" in target_device and torch.cuda.is_available() and torch.cuda.is_bf16_supported()
                    else (torch.half if "cuda" in target_device and torch.cuda.is_available() else torch.float32)
                )

                decoder_model = load_custom_codec_model(
                    codec_checkpoint_path=str(codec_path),
                    device=target_device,
                    precision=precision,
                )

                tokenizer_config = checkpoint_dir / "tokenizer_config.json"
                if not tokenizer_config.exists():
                    import json
                    tokenizer_config.write_text(json.dumps({"tokenizer_class": "PreTrainedTokenizerFast"}))

                llama_queue = launch_thread_safe_queue(
                    checkpoint_path=str(checkpoint_dir),
                    device=target_device,
                    precision=precision,
                    compile=False,
                )

                self._model = TTSInferenceEngine(
                    llama_queue=llama_queue,
                    decoder_model=decoder_model,
                    precision=precision,
                    compile=False,
                )
                self._api_version = "v2.0"

            self._loaded = True
            logger.info(f"✅ Fish Speech model loaded successfully on '{target_device}'")

        except Exception as err:
            err_msg = str(err)
            logger.error(f"Failed to load Fish Speech on '{target_device}': {err_msg}")

            # CUDA OOM or GPU driver failure fallback to CPU
            if target_device != "cpu" and any(k in err_msg.lower() for k in ["cuda", "out of memory", "oom", "nvml"]):
                logger.warning("⚠️ CUDA error/OOM detected during Fish Speech load. Falling back to CPU...")
                await self.unload_model()
                self._device = "cpu"
                try:
                    from fish_speech.inference_engine import TTSInferenceEngine
                    self._model = TTSInferenceEngine(
                        checkpoint=str(checkpoint_dir),
                        device="cpu",
                        compile=False,
                    )
                    self._api_version = "v1.5"
                    self._loaded = True
                    logger.info("✅ Fish Speech successfully recovered and loaded on CPU")
                    return
                except Exception as cpu_err:
                    raise EngineLoadError("fish_speech", f"CPU fallback load failed: {cpu_err}")

            raise EngineLoadError("fish_speech", err_msg)

    async def unload_model(self) -> None:
        """Unload model from RAM/VRAM and release hardware resources."""
        if self._model is not None:
            del self._model
            self._model = None

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

        logger.info("Fish Speech model unloaded and memory cleared")

    async def generate(
        self,
        text: str,
        reference_audio: Path,
        language: str = "ur",
        output_path: Optional[Path] = None,
        reference_text: Optional[str] = None,
    ) -> GenerationResult:
        """Generate cloned speech from text using reference audio."""
        if not self._loaded or self._model is None:
            raise GenerationError("Fish Speech model not loaded. Call load_model() first.")

        if not reference_audio.exists():
            raise GenerationError(f"Reference audio not found: {reference_audio}")

        start_time = time.time()
        logger.info(f"Generating with Fish Speech: '{text[:40]}...' [Language: {language}]")

        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = settings.generated_dir / f"fish_{timestamp}.wav"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            api_version = getattr(self, "_api_version", "v1.5")

            if api_version == "v2.0":
                from fish_speech.utils.schema import ServeTTSRequest, ServeReferenceAudio
                import soundfile as sf

                with open(reference_audio, "rb") as f:
                    ref_audio_bytes = f.read()

                ref_obj = ServeReferenceAudio(
                    audio=ref_audio_bytes,
                    text=reference_text or "",
                )

                req = ServeTTSRequest(
                    text=text,
                    references=[ref_obj],
                    reference_id=None,
                    max_new_tokens=0,
                    chunk_length=200,
                    top_p=0.7,
                    repetition_penalty=1.5,
                    temperature=0.7,
                    streaming=False,
                )

                results = list(self._model.inference(req))
                audio_result = None
                for res in results:
                    if res.code == "final":
                        audio_result = res
                        break
                    elif res.code == "error":
                        raise GenerationError(f"Fish Speech inference error: {res.error}")

                if audio_result is None or audio_result.audio is None:
                    raise GenerationError("Fish Speech generated no audio output.")

                sample_rate, audio_data = audio_result.audio
                sf.write(str(output_path), audio_data, sample_rate)

            elif api_version == "v1.5" and hasattr(self._model, "tts"):
                self._model.tts(
                    text=text,
                    reference_audio=str(reference_audio),
                    reference_text=reference_text or "",
                    output=str(output_path),
                    language=language,
                )
            else:
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

                if isinstance(result_audio, tuple):
                    audio_data, sample_rate = result_audio
                else:
                    audio_data = result_audio
                    sample_rate = 44100

                sf.write(str(output_path), audio_data, sample_rate)

            gen_time = time.time() - start_time

            # Compute audio duration
            duration_sec = 0.0
            sample_rate_out = 44100
            try:
                with wave.open(str(output_path), "r") as wf:
                    duration_sec = wf.getnframes() / wf.getframerate()
                    sample_rate_out = wf.getframerate()
            except Exception:
                duration_sec = max(1.0, len(text.split()) * 0.3)

            logger.info(f"✅ Fish Speech generated speech: {duration_sec:.1f}s in {gen_time:.2f}s")

            return GenerationResult(
                output_path=output_path,
                duration_sec=duration_sec,
                gen_time_sec=gen_time,
                sample_rate=sample_rate_out,
                engine="fish_speech",
                metadata={"language": language, "api_version": api_version},
            )

        except GenerationError:
            raise
        except Exception as e:
            err_msg = str(e)
            if "out of memory" in err_msg.lower() or "cuda" in err_msg.lower():
                raise VRAMExhaustedError("fish_speech", 4000)
            raise GenerationError(f"Fish Speech generation failed: {err_msg}")

    def get_supported_languages(self) -> List[str]:
        """Get supported language codes."""
        return [
            "en", "ur", "hi", "zh", "ja", "ko", "ar", "fr", "de",
            "es", "pt", "ru", "tr", "it", "nl", "pl", "sv",
        ]

    def health_check(self) -> Dict[str, Any]:
        """Perform comprehensive health check on Fish Speech engine."""
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
            import fish_speech
        except ImportError:
            deps_ok = False

        base_health.update({
            "device": self._device,
            "api_version": self._api_version,
            "checkpoint_verified": self._verify_checkpoint_integrity(),
            "checkpoint_dir": str(self._checkpoint_dir),
            "dependencies_installed": deps_ok,
            "gpu_info": gpu_info,
        })
        return base_health
