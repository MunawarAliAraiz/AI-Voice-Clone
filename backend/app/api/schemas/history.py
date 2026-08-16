"""
AI Voice Clone Studio — Generation history schemas.

CONTRACT MODULE. Wave 0.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .tts import RouteInfo

__all__ = ["HistoryItem", "HistoryList", "HistoryUpdate"]


class HistoryItem(BaseModel):
    """One past generation."""

    id: int
    profile_id: int
    profile_name: str | None = None
    #: None for every generation made before titles existed, and for any made
    #: without one — the UI falls back to the text.
    title: str | None = None
    input_text: str
    language: str
    #: Signed, expiring URL. Regenerated per response — never stored, never
    #: built client-side.
    audio_url: str
    output_format: str
    duration_sec: float | None
    gen_time_sec: float | None
    is_favorite: bool
    #: Pause-joined segments Speech Direction rendered this into. 0 means it
    #: was synthesized in one piece with no direction; None means the row
    #: predates the column, which is not the same claim as 0.
    direction_segments: int | None = None
    #: How this was produced. Persisted, so history stays auditable after the
    #: catalog changes: if a model is later removed, old rows still say what
    #: made them.
    route: RouteInfo
    created_at: datetime


class HistoryList(BaseModel):
    items: list[HistoryItem]
    total: int
    page: int
    page_size: int


class HistoryUpdate(BaseModel):
    """Partial update of a history row."""

    is_favorite: bool | None = Field(None)
