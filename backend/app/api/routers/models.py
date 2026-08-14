"""
Model catalog + language endpoints.

Feeds the dynamic picker so the frontend keeps no hardcoded model list — the
predecessor's static dropdown is what advertised Urdu on an engine that could
not speak it. Only Phase-A-verified (language, script) pairs are returned,
with one narrow, explicit exception: a model with `experimental_listing=True`
(Chatterbox, and VoxCPM2's Urdu LoRA) also lists its unverified cells, each
still carrying `verified=False` and the model flagged `experimental=True`, so
the picker can show it as a clearly labeled opt-in rather than hiding it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ...config import Settings
from ...exceptions import ModelNotFoundError
from ...inference.catalog import ModelCatalog
from ...inference.protocol import ModelStatus, SchedulerProtocol
from ...inference.spec import ModelSpec
from ..deps import get_catalog, get_scheduler, get_settings
from ..schemas.models import (
    LanguageInfo,
    LanguageListResponse,
    LanguageSupportInfo,
    ModelListResponse,
    ModelSummary,
    WarmRequest,
)

router = APIRouter(prefix="/models", tags=["models"])

#: Display + native names for the languages the product can serve.
_LANG_NAMES: dict[str, tuple[str, str]] = {
    "ur": ("Urdu (اردو)", "اردو"),
    "hi": ("Hindi (हिन्दी)", "हिन्दी"),
    "en": ("English", "English"),
}


def _listed_langs(spec: ModelSpec) -> list[LanguageSupportInfo]:
    return [
        LanguageSupportInfo(
            language=ls.language, script=ls.script.value, verified=ls.verified,
            cer=ls.cer, speaker_cosine=ls.speaker_cosine,
        )
        for ls in spec.languages
        if ls.verified or spec.experimental_listing
    ]


def _summary(status: ModelStatus) -> ModelSummary:
    spec = status.spec
    return ModelSummary(
        id=spec.id, display_name=spec.display_name, runtime=spec.runtime.value,
        license=spec.license.value, languages=_listed_langs(spec),
        experimental=spec.experimental_listing,
        state=status.state.value, est_wait_sec=status.est_wait_sec,
        vram_mb=spec.vram_mb, est_rtf=spec.est_rtf, params=spec.params,
        needs_reference_text=spec.needs_reference_text,
        reference_max_sec=spec.reference_max_sec, notes=spec.notes,
    )


@router.get("", response_model=ModelListResponse)
async def list_models(
    scheduler: Annotated[SchedulerProtocol, Depends(get_scheduler)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ModelListResponse:
    statuses = await scheduler.status()
    return ModelListResponse(
        models=[_summary(s) for s in statuses],
        vram_budget_mb=settings.budget_mb,
        max_workers=settings.max_workers,
    )


@router.post("/warm", status_code=202)
async def warm_model(
    body: WarmRequest,
    scheduler: Annotated[SchedulerProtocol, Depends(get_scheduler)],
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
) -> dict[str, str]:
    if catalog.get(body.model_id) is None:
        raise ModelNotFoundError(
            body.model_id, available=tuple(s.id for s in catalog.specs)
        )
    await scheduler.warm(body.model_id)
    return {"status": "warming", "model_id": body.model_id}


languages_router = APIRouter(tags=["models"])


@languages_router.get("/languages", response_model=LanguageListResponse)
async def list_languages(
    catalog: Annotated[ModelCatalog, Depends(get_catalog)],
) -> LanguageListResponse:
    # Aggregate verified (language -> scripts, model_ids) across the catalog, so
    # a language with no verified model is simply absent rather than broken.
    by_lang: dict[str, dict[str, set[str]]] = {}
    for spec in catalog.specs:
        for ls in spec.languages:
            if not ls.verified:
                continue
            entry = by_lang.setdefault(ls.language, {"scripts": set(), "models": set()})
            entry["scripts"].add(ls.script.value)
            entry["models"].add(spec.id)

    langs = []
    for code, entry in by_lang.items():
        display, native = _LANG_NAMES.get(code, (code, code))
        # Every verified cell here is rendered with transform NONE (VoxCPM2 reads
        # romanized hi/ur directly), so nothing is served only-via-lossy-transform.
        langs.append(
            LanguageInfo(
                code=code, display_name=display, native_name=native,
                scripts=sorted(entry["scripts"]), model_ids=sorted(entry["models"]),
                requires_transform=False,
            )
        )
    langs.sort(key=lambda x: x.code)
    return LanguageListResponse(languages=langs)
