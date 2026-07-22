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

            # fish-speech exposes high-level inference engines
            try:
                import inspect
                from fish_speech.inference_engine import TTSInferenceEngine

                init_params = inspect.signature(TTSInferenceEngine.__init__).parameters
                checkpoint_dir = settings.models_dir / "fish-speech-1.5"

                if "checkpoint" in init_params:
                    # fish-speech <= 1.5 API: TTSInferenceEngine(checkpoint=..., device=..., compile=...)
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
                        compile=False,
                    )
                    self._api_version = "v1.5"
                else:
                    # fish-speech >= 2.0 API: TTSInferenceEngine(llama_queue=..., decoder_model=..., precision=..., compile=...)
                    import torch
                    from huggingface_hub import snapshot_download
                    from fish_speech.models.text2semantic.inference import (
                        launch_thread_safe_queue,
                    )

                    if not checkpoint_dir.exists():
                        logger.info(f"Downloading Fish Speech 1.5 weights to {checkpoint_dir}...")
                        checkpoint_dir.mkdir(parents=True, exist_ok=True)
                        snapshot_download(
                            repo_id="fishaudio/fish-speech-1.5",
                            local_dir=str(checkpoint_dir),
                        )

                    # Find codec model checkpoint (.pth)
                    codec_path = None
                    for pth in checkpoint_dir.glob("*.pth"):
                        if any(k in pth.name.lower() for k in ["firefly", "vq", "codec", "generator"]):
                            codec_path = pth
                            break
                    if codec_path is None:
                        pth_files = [f for f in checkpoint_dir.glob("*.pth") if f.name != "model.pth"]
                        if pth_files:
                            codec_path = pth_files[0]
                        else:
                            raise EngineLoadError("fish_speech", f"Codec checkpoint not found in {checkpoint_dir}")

                    precision = (
                        torch.bfloat16
                        if torch.cuda.is_available() and torch.cuda.is_bf16_supported()
                        else (torch.half if torch.cuda.is_available() else torch.float32)
                    )

                    decoder_model = load_custom_codec_model(
                        codec_checkpoint_path=str(codec_path),
                        device=device,
                        precision=precision,
                    )

                    # Ensure tokenizer_config.json exists so AutoTokenizer can load PreTrainedTokenizerFast
                    tokenizer_config = checkpoint_dir / "tokenizer_config.json"
                    if not tokenizer_config.exists():
                        import json
                        logger.info(f"Creating tokenizer_config.json in {checkpoint_dir}...")
                        tokenizer_config.write_text(json.dumps({
                            "tokenizer_class": "PreTrainedTokenizerFast"
                        }))

                    llama_queue = launch_thread_safe_queue(
                        checkpoint_path=str(checkpoint_dir),
                        device=device,
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

            except Exception as err:
                logger.error(f"Fish Speech error: {err}")
                raise EngineLoadError(
                    "fish_speech",
                    f"Fish Speech engine error: {err}"
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
