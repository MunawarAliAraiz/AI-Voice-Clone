"""
AI Voice Clone Studio — Independent NLLB-200 Translation Service

Provides neural machine translation between Urdu, Hindi, and English using Meta's NLLB-200 model.
Completely decoupled from TTS engines. Supports automatic language detection, double-layer caching
(memory + SQLite), lazy loading, GPU acceleration, and CPU fallback.
"""

import re
import gc
import asyncio
import time
from typing import Optional, Dict, Tuple, Any
from pathlib import Path

from ..config import settings
from ..database import get_db, close_db
from ..utils.logger import setup_logger
from ..utils.exceptions import VoiceCloneError

logger = setup_logger("voiceclone.service.translation")

# NLLB-200 FLORES-200 Language Codes
FLORES_MAP = {
    "en": "eng_Latn",
    "ur": "urd_Arab",
    "hi": "hin_Deva",
}

DEFAULT_NLLB_MODEL = "facebook/nllb-200-distilled-600M"


class TranslationError(VoiceCloneError):
    """Raised when translation fails."""
    def __init__(self, message: str):
        super().__init__(message, code="TRANSLATION_ERROR")


def detect_language(text: str) -> str:
    """Detect source language code ('ur', 'hi', or 'en') using Unicode script analysis."""
    text = text.strip()
    if not text:
        return "en"

    # Perso-Arabic script range (Urdu)
    arabic_chars = re.findall(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]", text)
    # Devanagari script range (Hindi)
    devanagari_chars = re.findall(r"[\u0900-\u097F]", text)
    # Latin characters (English)
    latin_chars = re.findall(r"[a-zA-Z]", text)

    count_ur = len(arabic_chars)
    count_hi = len(devanagari_chars)
    count_en = len(latin_chars)

    if count_ur > count_hi and count_ur > count_en:
        return "ur"
    elif count_hi > count_ur and count_hi > count_en:
        return "hi"
    else:
        return "en"


class TranslationService:
    """Independent Neural Translation Service using Meta NLLB-200."""

    _instance: Optional["TranslationService"] = None

    def __new__(cls) -> "TranslationService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._model = None
            cls._instance._tokenizer = None
            cls._instance._loaded = False
            cls._instance._device = "cpu"
            cls._instance._memory_cache: Dict[Tuple[str, str, str], str] = {}
        return cls._instance

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    async def load_model(self, device: str = "cpu") -> None:
        """Lazily load NLLB-200 tokenizer and model with GPU/CPU fallback."""
        if self._loaded and self._model is not None and self._device == device:
            return

        target_device = device.lower()
        if "cuda" in target_device:
            try:
                import torch
                if not torch.cuda.is_available():
                    logger.warning("CUDA requested for TranslationService but not available. Using CPU.")
                    target_device = "cpu"
            except ImportError:
                target_device = "cpu"

        self._device = target_device
        logger.info(f"Loading NLLB-200 Translation Model ({DEFAULT_NLLB_MODEL}) on device '{target_device}'...")

        try:
            from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
            import torch

            model_dir = settings.models_dir / "nllb-200-distilled-600M"
            model_path_str = str(model_dir) if model_dir.exists() else DEFAULT_NLLB_MODEL

            self._tokenizer = AutoTokenizer.from_pretrained(model_path_str)
            self._model = AutoModelForSeq2SeqLM.from_pretrained(model_path_str)

            if "cuda" in target_device:
                self._model.to(target_device)

            self._loaded = True
            logger.info(f"✅ NLLB-200 Translation Model loaded on '{target_device}'")

        except Exception as err:
            logger.error(f"Failed to load NLLB-200 on '{target_device}': {err}")

            if target_device != "cpu":
                logger.warning("Falling back NLLB-200 to CPU...")
                await self.unload_model()
                self._device = "cpu"
                try:
                    from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
                    self._tokenizer = AutoTokenizer.from_pretrained(DEFAULT_NLLB_MODEL)
                    self._model = AutoModelForSeq2SeqLM.from_pretrained(DEFAULT_NLLB_MODEL)
                    self._loaded = True
                    logger.info("✅ NLLB-200 loaded on CPU fallback")
                    return
                except Exception as cpu_err:
                    raise TranslationError(f"CPU fallback load failed: {cpu_err}")

            raise TranslationError(f"Model load failed: {err}")

    async def unload_model(self) -> None:
        """Unload model from RAM/VRAM."""
        if self._model is not None:
            del self._model
            self._model = None

        if self._tokenizer is not None:
            del self._tokenizer
            self._tokenizer = None

        self._loaded = False
        gc.collect()

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

        logger.info("NLLB-200 Translation model unloaded")

    async def _get_cached_translation(self, text: str, src_lang: str, tgt_lang: str) -> Optional[str]:
        """Check in-memory and SQLite cache for existing translation."""
        cache_key = (text.strip(), src_lang, tgt_lang)
        if cache_key in self._memory_cache:
            return self._memory_cache[cache_key]

        try:
            db = await get_db()
            cursor = await db.execute(
                """SELECT translated_text FROM translation_cache
                   WHERE source_text = ? AND source_lang = ? AND target_lang = ?""",
                (text.strip(), src_lang, tgt_lang),
            )
            row = await cursor.fetchone()
            await close_db(db)
            if row:
                res = row["translated_text"]
                self._memory_cache[cache_key] = res
                return res
        except Exception:
            pass

        return None

    async def _save_cached_translation(self, text: str, src_lang: str, tgt_lang: str, translated: str) -> None:
        """Save translation result into memory and SQLite database cache."""
        cache_key = (text.strip(), src_lang, tgt_lang)
        self._memory_cache[cache_key] = translated

        try:
            db = await get_db()
            await db.execute(
                """INSERT OR REPLACE INTO translation_cache (source_text, source_lang, target_lang, translated_text)
                   VALUES (?, ?, ?, ?)""",
                (text.strip(), src_lang, tgt_lang, translated),
            )
            await db.commit()
            await close_db(db)
        except Exception as e:
            logger.warning(f"Failed to persist translation cache: {e}")

    def _do_inference(self, text: str, src_flores: str, tgt_flores: str) -> str:
        """Synchronous NLLB-200 neural translation inference pass."""
        import torch

        # Update src_lang on tokenizer
        self._tokenizer.src_lang = src_flores
        inputs = self._tokenizer(text, return_tensors="pt")

        if "cuda" in self._device:
            inputs = {k: v.to(self._device) for k, v in inputs.items()}

        forced_bos_token_id = self._tokenizer.lang_code_to_id[tgt_flores]
        generated_tokens = self._model.generate(
            **inputs,
            forced_bos_token_id=forced_bos_token_id,
            max_length=512,
        )

        translated_text = self._tokenizer.batch_decode(generated_tokens, skip_special_tokens=True)[0]
        return translated_text.strip()

    async def translate(
        self,
        text: str,
        target_lang: str,
        source_lang: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Translate text between Urdu, Hindi, and English using NLLB-200.

        Args:
            text: Text to translate.
            target_lang: Target language code ('ur', 'hi', 'en').
            source_lang: Optional source language. Auto-detected if None.

        Returns:
            Dict containing translated_text, source_lang, target_lang, cached indicator.
        """
        text = text.strip()
        if not text:
            return {
                "translated_text": "",
                "source_lang": source_lang or "en",
                "target_lang": target_lang,
                "cached": False,
            }

        # Auto-detect source language if not provided
        if not source_lang or source_lang == "auto":
            source_lang = detect_language(text)

        source_lang = source_lang.lower().strip()
        target_lang = target_lang.lower().strip()

        # If source and target are identical, return as-is
        if source_lang == target_lang:
            return {
                "translated_text": text,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "cached": True,
            }

        # Check Cache
        cached_result = await self._get_cached_translation(text, source_lang, target_lang)
        if cached_result is not None:
            logger.info(f"Translation cache hit [{source_lang} -> {target_lang}]")
            return {
                "translated_text": cached_result,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "cached": True,
            }

        # Validate FLORES mapping
        if source_lang not in FLORES_MAP or target_lang not in FLORES_MAP:
            raise TranslationError(
                f"Unsupported translation pair: '{source_lang}' to '{target_lang}'. "
                f"Supported: Urdu (ur), Hindi (hi), English (en)."
            )

        src_flores = FLORES_MAP[source_lang]
        tgt_flores = FLORES_MAP[target_lang]

        # Ensure model is loaded
        if not self._loaded or self._model is None:
            from ..utils.gpu import get_gpu_info
            gpu = get_gpu_info()
            device = gpu.device if gpu.available else "cpu"
            await self.load_model(device=device)

        start_time = time.time()
        logger.info(f"Translating: '{text[:30]}...' [{source_lang} ({src_flores}) -> {target_lang} ({tgt_flores})]")

        try:
            # Offload heavy inference call off the main asyncio thread
            translated_text = await asyncio.to_thread(
                self._do_inference, text, src_flores, tgt_flores
            )
            elapsed = time.time() - start_time
            logger.info(f"✅ Translated in {elapsed:.2f}s: '{translated_text[:30]}...'")

            # Save to cache
            await self._save_cached_translation(text, source_lang, target_lang, translated_text)

            return {
                "translated_text": translated_text,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "cached": False,
                "elapsed_sec": elapsed,
            }

        except Exception as e:
            logger.error(f"NLLB-200 translation failed: {e}")
            raise TranslationError(f"Translation failed: {e}")


# Singleton instance getter
def get_translation_service() -> TranslationService:
    return TranslationService()
