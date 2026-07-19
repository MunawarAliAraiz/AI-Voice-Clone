"""
AI Voice Clone Studio — Engine Registry

Central registry for all TTS engines. Handles engine discovery,
selection, and lifecycle management.
"""

from .base import TTSEngine, EngineInfo
from .mock_engine import MockTTSEngine
from .f5_tts import F5TTSEngine
from .fish_speech import FishSpeechEngine
from .xtts_v2 import XTTSv2Engine
from ..utils.logger import setup_logger
from ..utils.exceptions import EngineNotFoundError

logger = setup_logger("voiceclone.engines")

# Engine registry — maps engine name to its class
_ENGINE_CLASSES: dict[str, type[TTSEngine]] = {
    "mock": MockTTSEngine,
    "f5_tts": F5TTSEngine,
    "fish_speech": FishSpeechEngine,
    "xtts_v2": XTTSv2Engine,
}

# Singleton engine instances
_engine_instances: dict[str, TTSEngine] = {}


def get_available_engines() -> list[EngineInfo]:
    """Get info about all registered engines."""
    result = []
    for name, cls in _ENGINE_CLASSES.items():
        engine = cls()
        result.append(engine.get_info())
    return result


def get_engine(name: str) -> TTSEngine:
    """Get or create a TTS engine instance by name.

    Args:
        name: Engine name ('mock', 'f5_tts', 'fish_speech', 'xtts_v2')

    Returns:
        TTSEngine instance

    Raises:
        EngineNotFoundError: If engine name is not registered
    """
    if name not in _ENGINE_CLASSES:
        raise EngineNotFoundError(name)

    if name not in _engine_instances:
        _engine_instances[name] = _ENGINE_CLASSES[name]()
        logger.info(f"Created engine instance: {name}")

    return _engine_instances[name]


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
        engine = get_engine(engine_name)
        info = engine.get_info()
        if info.is_loaded and engine.is_language_supported(language):
            logger.info(f"Auto-selected engine '{engine_name}' for language '{language}'")
            return engine_name

    # Fallback: return mock if nothing is loaded
    logger.warning(f"No loaded engine supports '{language}', falling back to mock")
    return "mock"


__all__ = [
    "TTSEngine",
    "EngineInfo",
    "MockTTSEngine",
    "F5TTSEngine",
    "FishSpeechEngine",
    "XTTSv2Engine",
    "get_engine",
    "get_available_engines",
    "select_engine_for_language",
]
