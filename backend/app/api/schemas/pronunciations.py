"""
AI Voice Clone Studio — pronunciation dictionary schemas.

CONTRACT MODULE. Mirrored by hand in `frontend/src/types/api.ts` — there is no
generator, and hand-editing that file is correct (see CLAUDE.md's conventions).

WHAT A DICTIONARY ENTRY IS
--------------------------
A word OmniVoice says wrong, and the respelling to synthesize instead. The
maintainer ships a handful verified by ear (`domain/urdu_text.py`); these are
the user's own, for words only they can judge.

The key may be in EITHER script: Latin for an English loanword sitting inside
Urdu (`database`), Perso-Arabic for a word that arrives already converted
(`میٹنگ`, read as "mating"). The second case is why a Latin-only table was not
enough.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "PronunciationCreate",
    "PronunciationItem",
    "PronunciationList",
    "PronunciationUpdate",
]

#: Long enough for a multi-word key like `pull request`; short enough that the
#: regex built from every entry stays sane. A key is a word, not a sentence.
_MAX_KEY = 100
#: A replacement can be several words (`URL` -> `یو آر ایل` is three), so it
#: gets more room than the key.
_MAX_REPLACEMENT = 200
_MAX_NOTES = 500


def _one_line(value: str) -> str:
    """
    Collapse whitespace and strip.

    A key with a newline in it could never match anyway — the matcher works on
    word boundaries within a line — so accepting one would create an entry that
    silently does nothing. Same for a leading space.
    """
    return " ".join(value.split())


class PronunciationItem(BaseModel):
    """One entry, as stored."""

    id: int
    key_text: str
    replacement: str
    language: str
    is_enabled: bool
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class PronunciationList(BaseModel):
    items: list[PronunciationItem]
    total: int


class PronunciationCreate(BaseModel):
    key_text: str = Field(min_length=1, max_length=_MAX_KEY)
    replacement: str = Field(min_length=1, max_length=_MAX_REPLACEMENT)
    #: Only `ur` is used today. Present because the column is, and because an
    #: English dictionary is the obvious next ask.
    language: str = Field("ur", min_length=2, max_length=8)
    is_enabled: bool = True
    notes: str | None = Field(None, max_length=_MAX_NOTES)

    @field_validator("key_text", "replacement")
    @classmethod
    def _collapse(cls, value: str) -> str:
        collapsed = _one_line(value)
        if not collapsed:
            raise ValueError("must contain more than whitespace")
        return collapsed


class PronunciationUpdate(BaseModel):
    """
    Partial update. `None` means "not supplied", so `notes` is cleared by
    sending `""` rather than `null` — the same convention the database layer
    uses, kept identical here so the two cannot drift.
    """

    key_text: str | None = Field(None, min_length=1, max_length=_MAX_KEY)
    replacement: str | None = Field(None, min_length=1, max_length=_MAX_REPLACEMENT)
    language: str | None = Field(None, min_length=2, max_length=8)
    is_enabled: bool | None = None
    notes: str | None = Field(None, max_length=_MAX_NOTES)

    @field_validator("key_text", "replacement")
    @classmethod
    def _collapse(cls, value: str | None) -> str | None:
        if value is None:
            return None
        collapsed = _one_line(value)
        if not collapsed:
            raise ValueError("must contain more than whitespace")
        return collapsed
