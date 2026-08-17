"""
AI Voice Clone Studio — text-assistance schemas.

CONTRACT MODULE. Mirrored by hand in `frontend/src/types/api.ts`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ...domain.transliterate import MAX_BATCH_CHUNKS, MAX_INPUT_CHARS

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

    #: One passage. Sugar for `texts=[text]` — most callers have a single
    #: thing to convert and should not have to wrap it.
    text: str | None = Field(None, min_length=1, max_length=MAX_INPUT_CHARS)
    #: Several passages, converted against ONE model residency.
    #:
    #: This is the shape a transcript needs. An imported 90,000-character
    #: video is ~45 chunks, and from a cold start the per-chunk endpoint would
    #: have loaded 19 GB of weights forty-five times. Here it loads at most
    #: once. Order is preserved and every input gets exactly one result.
    texts: list[str] | None = Field(None, max_length=MAX_BATCH_CHUNKS)
    #: Appended to the gate-passing system prompt as a preference, after the
    #: rules rather than in place of them. Editable because a user who knows
    #: their own dialect can improve it — which is exactly why the server-side
    #: validator (`domain/transliterate.py`) is not optional and cannot be
    #: turned off from here.
    instruction: str = Field("", max_length=2000)
    #: Where to convert TO — `roman` or `perso_arabic`. `None` means "whatever
    #: this source usually goes to" (`domain/transliterate.DEFAULT_TARGETS`).
    #:
    #: The TARGET is the caller's, the SOURCE is not. That asymmetry is the
    #: point: the source is a fact about the text and is detected server-side,
    #: while "readable" versus "speakable" is a preference about what the
    #: caller is about to do, and only they know it. A Devanagari transcript
    #: legitimately goes either way.
    target: Literal["roman", "perso_arabic"] | None = None

    @model_validator(mode="after")
    def _exactly_one_input(self) -> TransliterateRequest:
        """
        `text` or `texts`, never both and never neither.

        Rejected rather than reconciled: a request carrying both is a caller
        that does not know which one it meant, and silently picking would send
        one of them to a GPU while discarding the other.
        """
        if (self.text is None) == (self.texts is None):
            raise ValueError("provide exactly one of `text` or `texts`")
        if self.texts is not None:
            if not self.texts:
                raise ValueError("`texts` must not be empty")
            for i, chunk in enumerate(self.texts):
                if not chunk.strip():
                    raise ValueError(f"`texts[{i}]` is empty")
                if len(chunk) > MAX_INPUT_CHARS:
                    raise ValueError(
                        f"`texts[{i}]` is {len(chunk)} characters; the limit is "
                        f"{MAX_INPUT_CHARS} per chunk"
                    )
        return self

    def as_list(self) -> list[str]:
        """The normalized input. One shape downstream, no branching."""
        return self.texts if self.texts is not None else [self.text or ""]
