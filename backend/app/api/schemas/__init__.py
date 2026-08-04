"""
AI Voice Clone Studio — API schemas.

CONTRACT PACKAGE. Wave 0. B2 owns the routers; these types are frozen first so
the frontend can be generated against them in parallel.

Two rules that keep the wire honest:
  1. EVERY route declares `response_model=`. The predecessor had exactly one,
     and 12 of its 15 schemas were dead code that drifted from reality.
  2. `frontend/src/types/api.ts` is GENERATED from the resulting OpenAPI. It is
     never hand-edited, so the wire types cannot silently diverge.
"""

from .history import HistoryItem, HistoryList, HistoryUpdate
from .models import (
    LanguageInfo,
    LanguageListResponse,
    LanguageSupportInfo,
    ModelListResponse,
    ModelSummary,
    WarmRequest,
)
from .system import GPUInfo, HealthResponse, SystemStatus
from .tts import (
    RouteInfo,
    ScriptDetectRequest,
    ScriptDetectResponse,
    TTSGenerateRequest,
    TTSGenerateResponse,
)
from .voice import (
    VoiceProfileCreate,
    VoiceProfileList,
    VoiceProfileResponse,
    VoiceProfileUpdate,
)

__all__ = [
    "RouteInfo",
    "TTSGenerateRequest",
    "TTSGenerateResponse",
    "ScriptDetectRequest",
    "ScriptDetectResponse",
    "VoiceProfileCreate",
    "VoiceProfileUpdate",
    "VoiceProfileResponse",
    "VoiceProfileList",
    "LanguageSupportInfo",
    "ModelSummary",
    "ModelListResponse",
    "LanguageInfo",
    "LanguageListResponse",
    "WarmRequest",
    "HistoryItem",
    "HistoryList",
    "HistoryUpdate",
    "GPUInfo",
    "SystemStatus",
    "HealthResponse",
]
