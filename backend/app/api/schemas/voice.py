"""
AI Voice Clone Studio — Voice profile schemas.

CONTRACT MODULE. Wave 0.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

__all__ = [
    "VoiceProfileCreate",
    "VoiceProfileResponse",
    "VoiceProfileList",
    "VoiceProfileUpdate",
]


class VoiceProfileCreate(BaseModel):
    """
    Metadata accompanying a reference-audio upload.

    Sent as multipart alongside the file. The upload itself is streamed to disk
    in bounded chunks and never read whole into memory.
    """

    name: str = Field(..., min_length=1, max_length=100, examples=["My voice — Urdu"])
    language: str = Field(..., examples=["ur", "hi", "en"])
    transcript: str | None = Field(
        None,
        max_length=2000,
        examples=["السلام علیکم، میں اپنی آواز ریکارڈ کر رہا ہوں۔"],
        description=(
            "Transcript of the reference audio. Required by F5-family models "
            "(spec.needs_reference_text); omitting it forces an ASR pass, which "
            "is slower and can mis-transcribe."
        ),
    )


class VoiceProfileUpdate(BaseModel):
    """Partial update. Omitted fields are left unchanged."""

    name: str | None = Field(None, min_length=1, max_length=100)
    transcript: str | None = Field(None, max_length=2000)
    is_active: bool | None = None


class VoiceProfileResponse(BaseModel):
    """A stored reference voice."""

    id: int
    name: str
    language: str
    transcript: str | None
    duration_sec: float | None
    sample_rate: int
    is_active: bool
    #: Signed, expiring URL for previewing the reference itself.
    audio_url: str
    #: Peak sample as dBFS. Surfaced because reference quality dominates clone
    #: quality more than any model parameter — a clipped reference cannot be
    #: rescued downstream.
    peak_dbfs: float | None = None
    is_clipped: bool = False
    created_at: datetime
    updated_at: datetime


class VoiceProfileList(BaseModel):
    profiles: list[VoiceProfileResponse]
    total: int
