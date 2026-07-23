"""
AI Voice Clone Studio — Engine Registry

Central registry for all TTS engines. Handles engine discovery,
selection, and lifecycle management.
"""

from .base import TTSEngine, EngineInfo, GenerationResult
from .mock_engine import MockTTSEngine
from .f5_tts import F5TTSEngine
from .fish_speech import FishSpeechEngine
from .xtts_v2 import XTTSv2Engine
from .registry import EngineRegistry, register_engine

from ..utils.logger import setup_logger
from ..utils.exceptions import EngineNotFoundError

logger = setup_logger("voiceclone.engines")

# Core engines automatically register themselves via @register_engine decorator on import

# Maintain backward compatibility aliases
_ENGINE_CLASSES = EngineRegistry._engine_classes
_engine_instances = EngineRegistry._engine_instances


def get_available_engines() -> list[EngineInfo]:
    """Get info about all registered engines."""
    return EngineRegistry.get_available_engines_info()


def get_engine(name: str) -> TTSEngine:
    """Get or create a TTS engine instance by name.

    Args:
        name: Engine name ('mock', 'f5_tts', 'fish_speech', 'xtts_v2')

    Returns:
        TTSEngine instance

    Raises:
        EngineNotFoundError: If engine name is not registered
    """
    return EngineRegistry.get_instance(name)


def select_engine_for_language(language: str) -> str:
    """Auto-select the best engine for a given language.

    Priority:
    - Urdu → Fish Speech (native support)
    - Hindi → F5-TTS (best quality)
    - English → F5-TTS (best quality)
    - Other → Fish Speech (80+ languages)

    Falls back to mock engine if preferred engine isn't loaded.
    """
    language_priority: dict[str, list[str]] = {
        "ur": ["fish_speech", "xtts_v2", "mock"],
        "hi": ["f5_tts", "xtts_v2", "fish_speech", "mock"],
        "en": ["f5_tts", "fish_speech", "xtts_v2", "mock"],
    }

    priorities = language_priority.get(language, ["fish_speech", "f5_tts", "mock"])

    for engine_name in priorities:
        try:
            engine = get_engine(engine_name)
            info = engine.get_info()
            if info.is_loaded and engine.is_language_supported(language):
                logger.info(f"Auto-selected engine '{engine_name}' for language '{language}'")
                return engine_name
        except EngineNotFoundError:
            continue

    # Fallback: return mock if nothing is loaded
    logger.warning(f"No loaded engine supports '{language}', falling back to mock")
    return "mock"


__all__ = [
    "TTSEngine",
    "EngineInfo",
    "GenerationResult",
    "MockTTSEngine",
    "F5TTSEngine",
    "FishSpeechEngine",
    "XTTSv2Engine",
    "EngineRegistry",
    "register_engine",
    "get_engine",
    "get_available_engines",
    "select_engine_for_language",
]


