"""
AI Voice Clone Studio — GPU Detection & Management
"""

from dataclasses import dataclass
from .logger import setup_logger

logger = setup_logger("voiceclone.gpu")


@dataclass
class GPUInfo:
    """GPU hardware information."""
    available: bool
    name: str | None = None
    vram_total_mb: int | None = None
    vram_free_mb: int | None = None
    cuda_version: str | None = None
    device: str = "cpu"


def detect_gpu() -> GPUInfo:
    """Detect NVIDIA GPU and CUDA availability."""
    try:
        import torch

        if torch.cuda.is_available():
            device_idx = 0
            name = torch.cuda.get_device_name(device_idx)
            vram_total = torch.cuda.get_device_properties(device_idx).total_mem
            vram_free = vram_total - torch.cuda.memory_allocated(device_idx)
            cuda_ver = torch.version.cuda

            info = GPUInfo(
                available=True,
                name=name,
                vram_total_mb=int(vram_total / 1024 / 1024),
                vram_free_mb=int(vram_free / 1024 / 1024),
                cuda_version=cuda_ver,
                device=f"cuda:{device_idx}",
            )
            logger.info(f"✅ GPU detected: {name} ({info.vram_total_mb} MB VRAM)")
            return info
        else:
            logger.warning("⚠️ PyTorch found but CUDA not available — using CPU")
            return GPUInfo(available=False, device="cpu")

    except ImportError:
        logger.warning("⚠️ PyTorch not installed — GPU detection unavailable")
        return GPUInfo(available=False, device="cpu")
    except Exception as e:
        logger.error(f"❌ GPU detection error: {e}")
        return GPUInfo(available=False, device="cpu")


# Cached GPU info
_gpu_info: GPUInfo | None = None


def get_gpu_info() -> GPUInfo:
    """Get cached GPU info (detect once)."""
    global _gpu_info
    if _gpu_info is None:
        _gpu_info = detect_gpu()
    return _gpu_info
