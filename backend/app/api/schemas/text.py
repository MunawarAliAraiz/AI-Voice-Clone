"""
AI Voice Clone Studio — text-assistance schemas.

CONTRACT MODULE. Mirrored by hand in `frontend/src/types/api.ts`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ...domain.transliterate import MAX_INPUT_CHARS

__all__ = ["TitleRequest", "TitleResponse", "TransliterateRequest"]


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


class TransliterateRequest(BaseModel):
    """
    Body for `POST /api/text/transliterate`, which ENQUEUES a job and returns
    202 — see that endpoint's docstring for why this one cannot be synchronous
    like `/text/title` is.
    """

    text: str = Field(..., min_length=1, max_length=MAX_INPUT_CHARS)
    #: Appended to the gate-passing system prompt as a preference, after the
    #: rules rather than in place of them. Editable because a user who knows
    #: their own dialect can improve it — which is exactly why the server-side
    #: validator (`domain/transliterate.py`) is not optional and cannot be
    #: turned off from here.
    instruction: str = Field("", max_length=2000)
