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
            vram_total = getattr(torch.cuda.get_device_properties(device_idx), "total_memory", getattr(torch.cuda.get_device_properties(device_idx), "total_mem", 0))
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
            logger.info(f"GPU detected: {name} ({info.vram_total_mb} MB VRAM)")
            return info
        else:
            logger.warning("PyTorch found but CUDA not available -- using CPU")
            return GPUInfo(available=False, device="cpu")

    except ImportError:
        logger.warning("PyTorch not installed -- GPU detection unavailable")
        return GPUInfo(available=False, device="cpu")
    except Exception as e:
        logger.error(f"GPU detection error: {e}")
        return GPUInfo(available=False, device="cpu")



from .gpu_manager import get_gpu_manager, GPUManager, GPUMode, VRAMMetrics


def get_gpu_info() -> GPUInfo:
    """Get GPU info dynamically from GPUManager."""
    mgr = get_gpu_manager()
    m = mgr.get_vram_metrics()
    return GPUInfo(
        available=m.available,
        name=m.gpu_name,
        vram_total_mb=m.vram_total_mb,
        vram_free_mb=m.vram_free_mb,
        cuda_version="CUDA",
        device=m.device,
    )

