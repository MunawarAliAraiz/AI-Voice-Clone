"""
AI Voice Clone Studio — Production GPU Manager

Provides real-time VRAM monitoring, GPU temperature monitoring, memory cleanup,
automatic model unloading, CUDA Out-Of-Memory (OOM) prevention, and operating mode control
('one_active_model' vs 'multi_model').

Never leaks GPU memory.
"""

import gc
import subprocess
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional

from .logger import setup_logger
from .exceptions import VRAMExhaustedError

logger = setup_logger("voiceclone.gpu.manager")


class GPUMode(str, Enum):
    ONE_ACTIVE_MODEL = "one_active_model"
    MULTI_MODEL = "multi_model"


@dataclass
class VRAMMetrics:
    """Detailed VRAM memory status."""
    available: bool
    device: str
    gpu_name: Optional[str]
    vram_total_mb: int
    vram_used_mb: int
    vram_free_mb: int
    vram_allocated_mb: int
    vram_reserved_mb: int
    usage_pct: float
    temperature_celsius: Optional[int] = None


class GPUManager:
    """Production GPU Hardware & VRAM Manager."""

    _instance: Optional["GPUManager"] = None

    def __new__(cls) -> "GPUManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._mode: GPUMode = GPUMode.ONE_ACTIVE_MODEL
            cls._instance._active_engine_names: List[str] = []
        return cls._instance

    @property
    def mode(self) -> GPUMode:
        return self._mode

    def set_mode(self, mode: GPUMode | str) -> None:
        """Set GPU operating mode ('one_active_model' or 'multi_model')."""
        if isinstance(mode, str):
            mode = GPUMode(mode.lower().strip())
        self._mode = mode
        logger.info(f"GPUManager operating mode set to: '{self._mode.value}'")

    @staticmethod
    def get_gpu_temperature() -> Optional[int]:
        """Query GPU temperature in Celsius via nvidia-smi or pynvml."""
        # 1. Try pynvml if available
        try:
            import pynvml
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            pynvml.nvmlShutdown()
            return int(temp)
        except Exception:
            pass

        # 2. Try nvidia-smi CLI fallback
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                timeout=3,
            )
            if res.returncode == 0:
                output = res.stdout.decode("utf-8").strip()
                if output.isdigit():
                    return int(output)
        except Exception:
            pass

        return None

    def get_vram_metrics(self) -> VRAMMetrics:
        """Query real-time VRAM memory metrics and GPU temperature."""
        try:
            import torch
            if torch.cuda.is_available():
                idx = 0
                props = torch.cuda.get_device_properties(idx)
                total = int(getattr(props, "total_memory", getattr(props, "total_mem", 0)) / 1024 / 1024)
                allocated = int(torch.cuda.memory_allocated(idx) / 1024 / 1024)
                reserved = int(torch.cuda.memory_reserved(idx) / 1024 / 1024)
                free = total - allocated
                used = total - free
                pct = round((used / total) * 100.0, 1) if total > 0 else 0.0

                temp = self.get_gpu_temperature()

                return VRAMMetrics(
                    available=True,
                    device=f"cuda:{idx}",
                    gpu_name=torch.cuda.get_device_name(idx),
                    vram_total_mb=total,
                    vram_used_mb=used,
                    vram_free_mb=free,
                    vram_allocated_mb=allocated,
                    vram_reserved_mb=reserved,
                    usage_pct=pct,
                    temperature_celsius=temp,
                )
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"Error querying VRAM metrics: {e}")

        return VRAMMetrics(
            available=False,
            device="cpu",
            gpu_name=None,
            vram_total_mb=0,
            vram_used_mb=0,
            vram_free_mb=0,
            vram_allocated_mb=0,
            vram_reserved_mb=0,
            usage_pct=0.0,
            temperature_celsius=None,
        )

    def cleanup_memory(self) -> Dict[str, Any]:
        """Force Python GC and flush PyTorch CUDA VRAM cache to prevent leaks."""
        logger.info("Executing deep VRAM memory cleanup pass...")
        collected = gc.collect()
        cleaned_cuda = False

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()
                cleaned_cuda = True
        except ImportError:
            pass

        metrics = self.get_vram_metrics()
        logger.info(f"Memory cleanup pass complete (GC items={collected}, CUDA cleared={cleaned_cuda}, Free VRAM={metrics.vram_free_mb}MB)")
        return {

            "status": "ok",
            "gc_objects_collected": collected,
            "cuda_cleared": cleaned_cuda,
            "vram_free_mb": metrics.vram_free_mb,
            "usage_pct": metrics.usage_pct,
        }

    async def prepare_for_model_load(self, target_engine_name: str, required_vram_mb: int = 1500) -> None:
        """Ensure adequate VRAM is available before loading a model.

        Unloads dormant models or enforces 'one_active_model' rule if necessary.
        """
        from ..engines import EngineRegistry

        # 1. Enforce ONE_ACTIVE_MODEL Mode
        if self._mode == GPUMode.ONE_ACTIVE_MODEL:
            logger.info(f"Enforcing 'ONE_ACTIVE_MODEL' mode before loading '{target_engine_name}'...")
            for loaded_name in EngineRegistry.get_loaded_engine_names():
                if loaded_name != target_engine_name:
                    logger.info(f"Offloading active model '{loaded_name}' from VRAM...")
                    try:
                        eng = EngineRegistry.get_instance(loaded_name)
                        await eng.unload_model()
                    except Exception as err:
                        logger.warning(f"Failed to unload '{loaded_name}': {err}")

            self.cleanup_memory()

        # 2. Enforce OOM Prevention for MULTI_MODEL Mode
        else:
            metrics = self.get_vram_metrics()
            if metrics.available and metrics.vram_free_mb < required_vram_mb:
                logger.warning(
                    f"Low free VRAM detected ({metrics.vram_free_mb}MB free < {required_vram_mb}MB required). "
                    "Offloading dormant models..."
                )
                for loaded_name in EngineRegistry.get_loaded_engine_names():
                    if loaded_name != target_engine_name:
                        eng = EngineRegistry.get_instance(loaded_name)
                        await eng.unload_model()
                        self.cleanup_memory()
                        metrics = self.get_vram_metrics()
                        if metrics.vram_free_mb >= required_vram_mb:
                            break



                if metrics.vram_free_mb < (required_vram_mb // 2):
                    raise VRAMExhaustedError(
                        required_vram_mb=required_vram_mb,
                        available_vram_mb=metrics.vram_free_mb,
                    )

        # Track active target engine
        if target_engine_name not in self._active_engine_names:
            self._active_engine_names.append(target_engine_name)


# Singleton getter
def get_gpu_manager() -> GPUManager:
    return GPUManager()
