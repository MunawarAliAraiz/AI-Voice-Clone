"""
AI Voice Clone Studio — System status schemas.

CONTRACT MODULE. Wave 0.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["GPUInfo", "SystemStatus", "HealthResponse"]


class GPUInfo(BaseModel):
    """
    GPU state, read via NVML.

    `free_mb` MUST come from NVML / `torch.cuda.mem_get_info()`, never from
    `total_memory - memory_allocated()`. The latter sees only the current
    process's tensors, so with workers in separate processes it cheerfully
    reports 24 GB free while they hold 20 GB — and every admission decision
    built on it is wrong.
    """

    available: bool
    name: str | None = None
    total_mb: int | None = None
    free_mb: int | None = None
    #: Held by inference workers specifically, as the scheduler accounts for it.
    used_by_workers_mb: int | None = None
    compute_capability: str | None = Field(None, examples=["8.6"])
    driver_version: str | None = None
    temperature_c: int | None = None


class SystemStatus(BaseModel):
    """Health and capacity."""

    version: str
    gpu: GPUInfo
    #: Model ids currently loaded in live workers.
    resident_models: list[str] = Field(default_factory=list)
    workers_alive: int = 0
    #: In-flight plus queued synthesis requests.
    queue_depth: int = 0
    queue_limit: int = 0
    profiles_count: int = 0
    generations_count: int = 0
    #: True when the fake runtime is enabled. Surfaced so the UI can show a loud
    #: banner: audio produced in this mode is NOT a clone.
    fake_runtime_enabled: bool = False


class HealthResponse(BaseModel):
    """
    Liveness only. Must not touch the GPU, the scheduler, or the database —
    a health check that can block behind an inference is not a health check.
    """

    status: str = Field("ok", examples=["ok"])
    version: str
