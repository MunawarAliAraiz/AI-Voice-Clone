"""
AI Voice Clone Studio — F5-TTS Engine Implementation

F5-TTS is the primary engine for English and Hindi voice cloning.
Uses zero-shot voice cloning with a short reference audio clip.
"""

import time
from pathlib import Path
from datetime import datetime

from .base import TTSEngine, EngineInfo, GenerationResult
from .registry import register_engine
from ..config import settings
from ..utils.logger import setup_logger
from ..utils.exceptions import EngineLoadError, GenerationError, ModelNotDownloadedError

logger = setup_logger("voiceclone.engine.f5tts")


@register_engine("f5_tts")
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

    def _prepare_reference_audio(self, reference_audio: Path, max_sec: float = 7.0) -> Path:
        """Trim reference audio to optimal duration (5-7 seconds max) to fit F5-TTS 8192 frame sequence limit."""
        try:
            import soundfile as sf
            data, sr = sf.read(str(reference_audio))
            max_samples = int(sr * max_sec)
            if len(data) > max_samples:
                logger.info(f"Trimming reference audio for F5-TTS from {len(data)/sr:.1f}s to {max_sec}s max to prevent sequence length overflow")
                trimmed_data = data[:max_samples]
                temp_path = settings.generated_dir / f"temp_f5_ref_{time.time_ns()}.wav"
                temp_path.parent.mkdir(parents=True, exist_ok=True)
                sf.write(str(temp_path), trimmed_data, sr)
                return temp_path
        except Exception as err:
            logger.warning(f"F5-TTS reference trimming skipped ({err})")
        return reference_audio

    def _split_text_chunks(self, text: str, max_chars: int = 140) -> list[str]:
        """Split long text into sentence chunks to stay under F5-TTS frame limit."""
        import re
        sentences = [s.strip() for s in re.split(r'(?<=[.!?۔;\n])\s+', text) if s.strip()]
        chunks = []
        curr = ""
        for s in sentences:
            if len(curr) + len(s) + 1 <= max_chars:
                curr = f"{curr} {s}".strip()
            else:
                if curr:
                    chunks.append(curr)
                if len(s) > max_chars:
                    # Hard split very long sentences
                    sub = [s[i:i+max_chars] for i in range(0, len(s), max_chars)]
                    chunks.extend(sub[:-1])
                    curr = sub[-1]
                else:
                    curr = s
        if curr:
            chunks.append(curr)
        return chunks if chunks else [text]

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

        prepared_ref = self._prepare_reference_audio(reference_audio, max_sec=6.0)

        try:
            chunks = self._split_text_chunks(text, max_chars=130)
            logger.info(f"F5-TTS text chunked into {len(chunks)} batch segment(s)")

            import numpy as np
            import soundfile as sf

            all_wavs = []
            sr_out = 24000

            for idx, chunk in enumerate(chunks):
                chunk_file = output_path.with_stem(f"{output_path.stem}_chk_{idx}")
                wav_chunk, sr_chunk, _ = self._model.infer(
                    ref_file=str(prepared_ref),
                    ref_text=reference_text or "",
                    gen_text=chunk,
                    file_wave=str(chunk_file),
                )
                if chunk_file.exists():
                    try:
                        chunk_file.unlink()
                    except Exception:
                        pass
                if wav_chunk is not None and len(wav_chunk) > 0:
                    all_wavs.append(wav_chunk)
                    sr_out = sr_chunk

            if all_wavs:
                final_wav = np.concatenate(all_wavs)
                sf.write(str(output_path), final_wav, sr_out)
                wav = final_wav
                sr = sr_out
            else:
                raise GenerationError("F5-TTS generated no audio output.")

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
            logger.error(f"F5-TTS generation error: {e}")
            raise GenerationError(f"F5-TTS generation failed: {e}")
        finally:
            if prepared_ref != reference_audio and prepared_ref.exists():
                try:
                    prepared_ref.unlink()
                except Exception:
                    pass



    def get_supported_languages(self) -> list[str]:
        return ["en", "hi", "zh", "ja", "ko", "fr", "de", "es"]
