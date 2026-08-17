"""
System status: GPU (via NVML), residency, capacity.

`free_mb` comes from NVML, never `total - allocated` — with workers in separate
processes the latter reports the card empty while they hold most of it. NVML is
read here in the API process, which is fine: it needs the driver, not a CUDA
context, so it does not pull in torch.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ...config import Settings
from ...inference.protocol import SchedulerProtocol
from ...inference.spec import ModelState
from ..deps import get_scheduler, get_settings
from ..schemas.system import FeatureAvailability, GPUInfo, SystemStatus

router = APIRouter(tags=["system"])


def _gpu_info() -> GPUInfo:
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode()
            major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
            driver = pynvml.nvmlSystemGetDriverVersion()
            if isinstance(driver, bytes):
                driver = driver.decode()
            temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            return GPUInfo(
                available=True, name=name,
                total_mb=mem.total // 1024 // 1024, free_mb=mem.free // 1024 // 1024,
                compute_capability=f"{major}.{minor}", driver_version=driver,
                temperature_c=temp,
            )
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        # No GPU / no driver (every dev laptop and CI box). Not an error.
        return GPUInfo(available=False)


@router.get("/system", response_model=SystemStatus)
async def system_status(
    request: Request,
    scheduler: Annotated[SchedulerProtocol, Depends(get_scheduler)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SystemStatus:
    statuses = await scheduler.status()
    resident = [s.spec.id for s in statuses if s.state is ModelState.RESIDENT]
    live_runtimes = {
        s.spec.runtime for s in statuses
        if s.state in (ModelState.RESIDENT, ModelState.WARM)
    }
    return SystemStatus(
        version=settings.version,
        gpu=_gpu_info(),
        resident_models=resident,
        workers_alive=len(live_runtimes),
        fake_runtime_enabled=settings.allow_fake_runtime,
        # Read off app.state rather than recomputed: the lifespan already
        # decided, and deciding twice is how the API and the thing it
        # describes drift apart.
        script_conversion=FeatureAvailability(
            available=getattr(request.app.state, "transliterator", None) is not None,
            reason=getattr(request.app.state, "transliterator_reason", None),
        ),
    )
