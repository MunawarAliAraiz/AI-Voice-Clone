"""Short visible model tests use the normal durable queue and spending policy."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ...config import Settings
from ...db import Database
from ...exceptions import GenerationError, ModelNotFoundError
from ...inference.catalog import ModelCatalog
from ...inference.protocol import SchedulerProtocol
from ...jobs import JobKind, JobRunner
from ...model_test_voice import reference_profile
from ...remote_worker.model_install import REQUIRED_MODEL_IDS
from ...runpod.controller import CloudSetupError, controller
from ..deps import (
    get_catalog,
    get_db,
    get_job_runner,
    get_lexicon,
    get_scheduler,
    get_settings,
    require_cloud_ready,
)
from ..schemas.jobs import JobStatusResponse
from ..schemas.tts import TTSGenerateRequest
from .jobs import build_job_status_response
from .tts import generate

router = APIRouter(prefix="/model-tests", tags=["model-tests"])
_records_lock = asyncio.Lock()


def _records(settings: Settings) -> dict:
    try:
        value = json.loads((settings.data_dir / "model-tests.json").read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


async def _record(settings: Settings, model_id: str, job_id: int) -> None:
    async with _records_lock:
        def write():
            records = _records(settings)
            records[model_id] = job_id
            path = settings.data_dir / "model-tests.json"
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(records), encoding="utf-8")
            os.replace(temp, path)
        await asyncio.to_thread(write)


@router.get("/status")
async def test_status(settings: Annotated[Settings, Depends(get_settings)],
                      db: Annotated[Database, Depends(get_db)]) -> dict:
    cloud = controller(settings)
    files_ready = cloud.is_ready(cloud.read()) if settings.desktop_static_dir else False
    compute = cloud.read().get("compute") if settings.desktop_static_dir else None
    loaded = set()
    readable = not compute
    if compute and compute.get("kind") == "generation":
        from ...runpod.worker_pair import WorkerPairStore
        pair = WorkerPairStore(settings.data_dir).get()
        if pair and pair.pod_id == compute.get("pod_id"):
            try:
                value = await asyncio.wait_for(cloud._worker_call(pair, "GET", "/v1/activity"), 5)
                ids = value.get("loaded_model_ids")
                if not isinstance(ids, list) or len(ids) > 64:
                    raise ValueError("Invalid residency response")
                loaded = {item for item in ids if isinstance(item, str)}
                readable = True
            except (GenerationError, CloudSetupError, OSError, ValueError, TimeoutError):
                # A failed read is unknown residency, never proof of unloading.
                readable = False
    records = await asyncio.to_thread(_records, settings)
    models = []
    for model_id in REQUIRED_MODEL_IDS:
        job_id = records.get(model_id)
        row = await db.get_job(job_id) if isinstance(job_id, int) else None
        last = ({"job_id": row["id"], "status": row["status"],
                 "finished_at": row["finished_at"], "error_code": row["error_code"],
                 "error_detail": row["error_detail"]} if row else None)
        models.append({"id": model_id, "files_checked": files_ready,
                       "loaded": model_id in loaded if readable else None, "last_test": last})
    return {"models": models, "session_active": bool(compute), "worker_status_available": readable}


class TestInput(BaseModel):
    profile_id: int | None = Field(default=None, ge=1)


@router.post("/{model_id}", status_code=202, response_model=JobStatusResponse,
             dependencies=[Depends(require_cloud_ready)])
async def test_model(model_id: str, body: TestInput, request: Request, response: Response,
                     db: Annotated[Database, Depends(get_db)],
                     settings: Annotated[Settings, Depends(get_settings)],
                     scheduler: Annotated[SchedulerProtocol, Depends(get_scheduler)],
                     catalog: Annotated[ModelCatalog, Depends(get_catalog)],
                     runner: Annotated[JobRunner, Depends(get_job_runner)],
                     lexicon: Annotated[dict[str, str], Depends(get_lexicon)]) -> JobStatusResponse:
    if model_id not in REQUIRED_MODEL_IDS:
        raise ModelNotFoundError(model_id, available=tuple(REQUIRED_MODEL_IDS))
    if catalog.get(model_id):
        profile = await reference_profile(db, settings.data_dir, body.profile_id)
        urdu = model_id == "omnivoice_urdu"
        result = await generate(
            TTSGenerateRequest(profile_id=profile["id"], model_id=model_id, allow_experimental=True,
                               title=f"Model test · {catalog.get(model_id).display_name}",
                               text=("السلام علیکم۔ یہ آواز کا ایک مختصر تجربہ ہے۔" if urdu
                                     else "Hello. This is a short voice generation test."),
                               language="ur" if urdu else "en", output_format="wav",
                               apply_direction=False),
            response, db, scheduler, catalog, settings, runner, lexicon)
        await _record(settings, model_id, result.id)
        return result
    if model_id == "qwen2.5-3b-instruct-analyzer":
        job = await runner.enqueue(JobKind.ANALYZE_LLM,
            params={"language": "en", "sentences": ["Hello, this is a short test."],
                    "model_test_id": model_id}, route=None, profile_id=None)
    else:
        if getattr(request.app.state, "transliterator", None) is None:
            from ...exceptions import TransliteratorUnavailableError
            raise TransliteratorUnavailableError(
                "Text conversion is not available in this session."
            )
        job = await runner.enqueue(JobKind.TRANSLITERATE,
            params={"texts": ["Assalam alaikum. Yeh aik chhota test hai."], "instruction": "",
                    "target": "perso_arabic", "source_language": None, "model_test_id": model_id},
            route=None, profile_id=None)
    await _record(settings, model_id, job.id)
    return await build_job_status_response(job, db, settings, scheduler, response)
