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

import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ...apify_captions import caption_service
from ...config import Settings
from ...domain.language import Script, profile_text
from ...domain.text import chunk_for_synthesis
from ...inference.catalog import ModelCatalog
from ...youtube_captions import video_id_from_url
from ..deps import get_catalog, get_settings
from ..schemas.transcript import (
    CaptionImportStatus,
    CaptionTrackResponse,
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


def _require_caption_access(settings: Settings) -> None:
    if settings.desktop_static_dir is None:
        return
    try:
        key = caption_service(settings.data_dir).keys.get()
    except (OSError, RuntimeError, UnicodeError):
        raise HTTPException(
            503, "Could not read Apify settings. Reconnect Apify in Settings."
        ) from None
    if not key:
        raise HTTPException(
            409,
            "Connect Apify in Settings to fetch YouTube captions. "
            "You can still paste a script.",
        )


@router.post("/youtube", response_model=PreparedTextResponse)
async def import_youtube(
    body: YoutubeTranscriptRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
) -> PreparedTextResponse:
    _require_caption_access(settings)
    if settings.desktop_static_dir is not None:
        raise HTTPException(409, "Use YouTube import in Convert to fetch captions with Apify.")
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
            "available_caption_tracks": [
                CaptionTrackResponse.model_validate(vars(t)) for t in captions.available_tracks
            ],
            "caption_provider": captions.provider,
        }
    )


def _desktop(settings: Settings):
    if settings.desktop_static_dir is None:
        raise HTTPException(404, "Apify credentials are available in the desktop app")
    return caption_service(settings.data_dir)


@router.get("/apify")
async def apify_status(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    service = _desktop(settings)
    account = service._read("connection.json") or {}
    return {
        **{k: account[k] for k in ["free_plan", "included_credit_remaining_usd"] if k in account},
        "connected": service.keys.path.is_file(),
        "maximum_import_usd": 0.03,
    }


@router.put("/apify")
async def apify_connect(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> dict:
    service = _desktop(settings)
    # Never let FastAPI reflect invalid credential values in a 422 response.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 2048:
            raise HTTPException(400, "Enter a valid Apify API token")
    try:
        body = json.loads(raw)
        if (
            not isinstance(body, dict)
            or set(body) != {"api_key"}
            or not isinstance(body["api_key"], str)
        ):
            raise ValueError
        key = body["api_key"].strip()
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Enter a valid Apify API token") from None
    if not 8 <= len(key) <= 512 or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in key
    ):
        raise HTTPException(400, "Enter a valid Apify API token")
    try:
        account = await service.account(key)
        service.keys.set(key)
        service._write("connection.json", account)
        return account
    except (OSError, RuntimeError, UnicodeError) as exc:
        raise HTTPException(503, "Cannot save the Apify key securely on this PC") from exc


@router.delete("/apify", status_code=204)
async def apify_disconnect(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    service = _desktop(settings)
    if any(not task.done() for task in service.tasks.values()):
        raise HTTPException(409, "Finish or cancel caption import before disconnecting Apify")
    try:
        service.keys.clear()
        (service.root / "connection.json").unlink(missing_ok=True)
    except OSError as exc:
        raise HTTPException(503, "Cannot remove the saved Apify key") from exc


async def _import_response(value, settings, catalog):
    captions = value.pop("captions", None)
    result = None
    if value["phase"] == "ready" and captions:
        result = await prepare_text(
            PrepareTextRequest(text=captions["text"], source_language=captions["language"]),
            settings,
            catalog,
        )
        result = result.model_copy(
            update={
                "video_id": captions["video_id"],
                "caption_language_code": captions["language_code"],
                "captions_generated": captions["is_generated"],
                "available_caption_tracks": [
                    CaptionTrackResponse.model_validate(t) for t in captions["available_tracks"]
                ],
                "caption_provider": captions["provider"],
            }
        )
    return CaptionImportStatus(**value, result=result)


@router.post("/youtube/imports", response_model=CaptionImportStatus, status_code=202)
async def start_caption_import(
    body: YoutubeTranscriptRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
) -> CaptionImportStatus:
    _require_caption_access(settings)
    try:
        status = await caption_service(settings.data_dir).start(
            video_id_from_url(body.url),
            body.source_language,
            max_chars=min(settings.transcript_max_chars, 200_000),
            refresh=body.refresh,
        )
        return await _import_response(status, settings, catalog)
    except (OSError, RuntimeError, UnicodeError) as exc:
        raise HTTPException(503, "Cannot unlock caption settings on this PC") from exc


def _import_id(value: str):
    if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise HTTPException(404, "Caption import not found")


@router.get("/youtube/imports", response_model=CaptionImportStatus | None)
async def active_caption_import(
    settings: Annotated[Settings, Depends(get_settings)],
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
) -> CaptionImportStatus | None:
    status = caption_service(settings.data_dir).active()
    return await _import_response(status, settings, catalog) if status else None


@router.get("/youtube/imports/{import_id}", response_model=CaptionImportStatus)
async def caption_import_status(
    import_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
) -> CaptionImportStatus:
    _import_id(import_id)
    status = await caption_service(settings.data_dir).status(import_id)
    return await _import_response(status, settings, catalog)


@router.post("/youtube/imports/{import_id}/cancel", response_model=CaptionImportStatus)
async def cancel_caption_import(
    import_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
) -> CaptionImportStatus:
    _import_id(import_id)
    status = await caption_service(settings.data_dir).cancel(import_id)
    return await _import_response(status, settings, catalog)
