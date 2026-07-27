"""
AI Voice Clone Studio — Dynamic Engine Registry & Memory Manager

Implements SOLID principles (Open/Closed, Dependency Inversion) to allow dynamic
registration of TTS engines (F5-TTS, Fish Speech, XTTS v2, and future models)
without modifying core registry code. Also manages VRAM offloading to prevent CUDA OOM.
"""

import gc
from typing import Dict, Type, Optional, List
from .base import TTSEngine, EngineInfo
from ..utils.logger import setup_logger
from ..utils.exceptions import EngineNotFoundError, EngineRegistrationError

logger = setup_logger("voiceclone.engines.registry")


class EngineRegistry:
    """Singleton registry for managing TTS engine classes and instances lifecycle."""

    _instance: Optional["EngineRegistry"] = None
    _engine_classes: Dict[str, Type[TTSEngine]] = {}
    _engine_instances: Dict[str, TTSEngine] = {}
    _active_gpu_engine: Optional[str] = None

    def __new__(cls) -> "EngineRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, name: str, engine_cls: Type[TTSEngine], overwrite: bool = False) -> None:
        """Register a new TTS engine class.

        Args:
            name: Unique name identifier for the engine.
            engine_cls: The TTSEngine subclass.
            overwrite: Whether to allow overwriting existing registrations.
        """
        if not issubclass(engine_cls, TTSEngine):
            raise EngineRegistrationError(
                name, f"Class {engine_cls.__name__} must inherit from TTSEngine"
            )

        if name in cls._engine_classes and not overwrite:
            logger.warning(f"Engine '{name}' already registered. Skipping.")
            return

        cls._engine_classes[name] = engine_cls
        logger.info(f"Registered TTS engine: '{name}' ({engine_cls.__name__})")

    @classmethod
    def get_registered_names(cls) -> List[str]:
        """Get names of all registered engines."""
        return list(cls._engine_classes.keys())

    @classmethod
    def get_engine_class(cls, name: str) -> Type[TTSEngine]:
        """Get engine class by name."""
        if name not in cls._engine_classes:
            raise EngineNotFoundError(name)
        return cls._engine_classes[name]

    @classmethod
    def get_instance(cls, name: str) -> TTSEngine:
        """Get or instantiate a singleton engine instance by name."""
        if name not in cls._engine_classes:
            raise EngineNotFoundError(name)

        if name not in cls._engine_instances:
            engine_cls = cls._engine_classes[name]
            cls._engine_instances[name] = engine_cls()
            logger.info(f"Instantiated engine: '{name}'")

        return cls._engine_instances[name]

    @classmethod
    def get_loaded_engine_names(cls) -> List[str]:
        """Get list of engine names that are currently loaded in memory."""
        loaded = []
        for name, instance in cls._engine_instances.items():
            if instance.get_info().is_loaded:
                loaded.append(name)
        return loaded

    @classmethod
    def list_engines(cls) -> List[TTSEngine]:
        """Get instances for all registered engines."""
        return [cls.get_instance(name) for name in cls._engine_classes]

    @classmethod
    def get_available_engines_info(cls) -> List[EngineInfo]:
        """Get metadata for all registered engines."""
        info_list = []
        for name in cls._engine_classes:
            engine = cls.get_instance(name)
            info_list.append(engine.get_info())
        return info_list


    @classmethod
    async def manage_vram_and_load(cls, target_name: str, device: str = "cpu") -> TTSEngine:
        """Manage VRAM offloading and load the requested engine model.

        Delegates to GPUManager for OOM prevention, VRAM threshold evaluation,
        and 'ONE_ACTIVE_MODEL' vs 'MULTI_MODEL' mode enforcement.
        """
        target_engine = cls.get_instance(target_name)
        target_info = target_engine.get_info()

        # If already loaded, return immediately
        if target_info.is_loaded:
            return target_engine

        # Prepare GPU VRAM before model load pass
        if "cuda" in device.lower() or target_info.requires_gpu:
            from ..utils.gpu_manager import get_gpu_manager
            gpu_mgr = get_gpu_manager()
            await gpu_mgr.prepare_for_model_load(
                target_engine_name=target_name,
                required_vram_mb=target_info.model_size_mb or 1500,
            )

        # Load target engine model
        logger.info(f"Loading engine model '{target_name}' on device '{device}'...")
        await target_engine.load_model(device=device)

        if target_info.requires_gpu or "cuda" in device.lower():
            cls._active_gpu_engine = target_name

        return target_engine


def register_engine(name: str):
    """Decorator to automatically register a TTSEngine subclass.

    Usage:
        @register_engine("my_new_engine")
        class MyNewEngine(TTSEngine):
            ...
    """
    def decorator(cls: Type[TTSEngine]):
        EngineRegistry.register(name, cls)
        return cls
    return decorator
