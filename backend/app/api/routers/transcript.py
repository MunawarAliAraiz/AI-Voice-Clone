"""
Prepare pasted text for the Convert tab.

WHY THIS IS SYNCHRONOUS AND NOT A JOB
--------------------------------------
Same argument as `routers/text.py`'s title endpoint: the `jobs` table is the GPU
QUEUE (golden rule 8), and this never touches the GPU. It splits text and detects
a script — pure CPU work — so queueing it behind a 60-second synthesis, and
reporting a VRAM-derived ETA for it, would both be wrong.

WHAT THIS DOES NOT DO
---------------------
It does not synthesize, and it does not convert scripts. The chunks land in an
EDITABLE list, because the whole premise is that the user reviews and converts
before generating. The conversion itself is `POST /api/text/transliterate`;
this only gets the pasted text into review-sized units.

Public caption import was restored in 2026-10 through a bounded local caption
client. It downloads no audio/video and provides no sign-in or bot-check bypass.
Pasted scripts remain available when captions cannot be retrieved. The caller
declares English/Hindi/Urdu; script detection never guesses a Latin language.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from ...config import Settings
from ...domain.language import Script, profile_text
from ...domain.text import chunk_for_synthesis
from ...inference.catalog import ModelCatalog
from ..deps import get_catalog, get_settings
from ..schemas.transcript import (
    PreparedTextResponse,
    PrepareTextRequest,
    TranscriptChunk,
    YoutubeTranscriptRequest,
)

router = APIRouter(prefix="/transcript", tags=["convert"])

#: Split on one or more blank lines. A paragraph break the user typed is a real
#: pause (`direction_analyze` reads a newline as the longest pause there is), so
#: paragraphs are chunked SEPARATELY and rejoined with the break preserved —
#: `chunk_for_synthesis` calls `normalize_whitespace`, which would otherwise
#: flatten every newline the user meant as ~380 ms of silence.
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


@router.post("/prepare", response_model=PreparedTextResponse)
async def prepare_text(
    body: PrepareTextRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
) -> PreparedTextResponse:
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT.split(body.text.strip()) if p.strip()]
    if not paragraphs:
        paragraphs = [body.text.strip()]

    # Detect ONCE, from the whole text, and hand the same script to every
    # per-paragraph chunking call. Detecting per paragraph would let a
    # Latin-heavy paragraph pick a different sentence-terminator set than the
    # Devanagari one beside it — the same mistake the transliterate handler and
    # the old per-chapter path both refuse.
    profile = profile_text(body.text, body.source_language or "ur")
    script = profile.script
    needs_transliteration = script is Script.DEVANAGARI and not catalog.candidates(
        "hi", Script.DEVANAGARI
    )

    # Chunk each paragraph, then re-number GLOBALLY. `chunk_for_synthesis`
    # numbers from 0 per call, and leaving those in place would give several
    # parts the same index — which the UI keys conversion results off, so part
    # 8's Urdu would land on part 1 and look entirely plausible there.
    api_chunks: list[TranscriptChunk] = []
    for paragraph in paragraphs:
        for chunk in chunk_for_synthesis(
            paragraph,
            script,
            max_chars=settings.transcript_chunk_chars,
            min_chars=settings.transcript_chunk_min_chars,
        ):
            api_chunks.append(
                TranscriptChunk(
                    index=len(api_chunks),
                    text=chunk.text,
                    ends_on_sentence=chunk.ends_on_sentence,
                )
            )

    return PreparedTextResponse(
        text="\n\n".join(paragraphs),
        script=script.value,
        needs_transliteration=needs_transliteration,
        chunks=api_chunks,
        source_language=body.source_language,
    )


@router.post("/youtube", response_model=PreparedTextResponse)
async def import_youtube(
    body: YoutubeTranscriptRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
) -> PreparedTextResponse:
    try:
        from ...youtube_captions import fetch_captions, video_id_from_url
    except ImportError as exc:
        raise HTTPException(
            503, "Caption import is not installed in this build. Paste the script instead."
        ) from exc
    video_id = video_id_from_url(body.url)
    captions = await run_in_threadpool(
        fetch_captions,
        video_id,
        body.source_language,
        max_chars=min(settings.transcript_max_chars, 200_000),
    )
    prepared = await prepare_text(
        PrepareTextRequest(text=captions.text, source_language=captions.language),
        settings,
        catalog,
    )
    return prepared.model_copy(
        update={
            "video_id": captions.video_id,
            "caption_language_code": captions.language_code,
            "captions_generated": captions.is_generated,
        }
    )
