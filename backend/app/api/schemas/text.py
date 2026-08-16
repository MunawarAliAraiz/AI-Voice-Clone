"""
AI Voice Clone Studio — text-assistance schemas.

CONTRACT MODULE. Mirrored by hand in `frontend/src/types/api.ts`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = ["TitleRequest", "TitleResponse"]


class TitleRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    language: str = Field(..., examples=["ur", "en"])


class TitleResponse(BaseModel):
    title: str
    #: Which path produced it. `"text"` means the analyzer was unavailable or
    #: its output failed validation and this is the first few words instead —
    #: surfaced rather than hidden, so a persistently degraded analyzer is
    #: visible in the client and the logs instead of looking like a bad model.
    source: Literal["analyzer", "text"]
