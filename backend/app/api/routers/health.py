"""
Liveness. Must not touch the GPU, the scheduler, or the database — a health
check that can block behind an inference is not a health check.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ...config import Settings
from ..deps import get_settings
from ..schemas.system import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(status="ok", version=settings.version)
