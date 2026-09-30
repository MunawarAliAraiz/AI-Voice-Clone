"""Local dialogue assembly from successful, independently editable speech jobs."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from ...config import Settings
from ...db import Database
from ...jobs import JobKind, JobStatus, job_record_from_row
from ...speech.dialogue_assembly import assemble_dialogue
from ..deps import get_db, get_settings
from ..media_tokens import make_media_url

router = APIRouter(prefix="/dialogue", tags=["dialogue"])


class DraftInput(BaseModel):
    draft: dict[str, Any]

    @field_validator("draft")
    @classmethod
    def validate_draft(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("version") != 1 or not isinstance(value.get("lines"), list):
            raise ValueError("Unsupported dialogue draft")
        if not 1 <= len(value["lines"]) <= 50 or not isinstance(value.get("voices"), dict):
            raise ValueError("A dialogue draft needs voices and 1 to 50 lines")
        if len(json.dumps(value).encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("Dialogue draft exceeds 2 MB")
        return value


def _save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".draft-", suffix=".json", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@router.get("/draft")
async def load_draft(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    path = settings.data_dir / "projects" / "dialogue-draft.json"
    try:
        value = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
        return {"draft": DraftInput(draft=value).draft}
    except FileNotFoundError:
        return {"draft": None}
    except (ValueError, OSError) as exc:
        raise HTTPException(409, "Saved dialogue draft cannot be read") from exc


@router.put("/draft")
async def save_draft(
    body: DraftInput, settings: Annotated[Settings, Depends(get_settings)]
) -> dict:
    await asyncio.to_thread(
        _save_json, settings.data_dir / "projects" / "dialogue-draft.json", body.draft
    )
    return {"saved": True}


class AssemblyLine(BaseModel):
    job_id: int = Field(ge=1)
    pause_after_sec: float = Field(default=0.25, ge=0, le=60, allow_inf_nan=False)


class AssemblyInput(BaseModel):
    lines: list[AssemblyLine] = Field(min_length=1, max_length=500)


@router.post("/assemble", status_code=201)
async def assemble(
    body: AssemblyInput,
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    clips, profiles, records = [], set(), []
    for line in body.lines:
        row = await db.get_job(line.job_id)
        if row is None:
            raise HTTPException(404, f"Job {line.job_id} was not found")
        job = job_record_from_row(row)
        if job.kind is not JobKind.SYNTHESIZE or job.status is not JobStatus.SUCCEEDED:
            raise HTTPException(409, "Every dialogue line must be a completed speech job")
        if (job.result or {}).get("is_fake"):
            raise HTTPException(409, "Fake test audio cannot be assembled as dialogue")
        history = await db.get_generation(job.history_id)
        if history is None:
            raise HTTPException(409, "A dialogue clip has been deleted from history")
        path = await asyncio.to_thread(Path(history["output_path"]).resolve)
        generated = await asyncio.to_thread(settings.generated_dir.resolve)
        if not path.is_relative_to(generated) or not await asyncio.to_thread(path.is_file):
            raise HTTPException(409, "A dialogue clip is unavailable locally")
        profiles.add(job.profile_id)
        records.append(
            {
                "job_id": job.id,
                "profile_id": job.profile_id,
                "text": job.params.get("input_text", ""),
                "route": job.route,
                "pause_after_sec": line.pause_after_sec,
            }
        )
        clips.append((job.id, path, line.pause_after_sec))
    if len(profiles) > 3:
        raise HTTPException(422, "Assign at most three voices to a dialogue")
    item_id = uuid.uuid4().hex
    output = settings.generated_dir / "dialogue" / f"{item_id}.wav"
    try:
        result = await asyncio.to_thread(assemble_dialogue, clips, output)
        manifest = {"id": item_id, "lines": records, **result}
        await asyncio.to_thread(
            output.with_suffix(".json").write_text,
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        await asyncio.to_thread(output.unlink, missing_ok=True)
        raise HTTPException(422, "Dialogue could not be assembled; check clips and FFmpeg") from exc
    return {
        "id": item_id,
        **result,
        "audio_url": make_media_url(
            f"dialogue/{item_id}", settings.media_token_secret, settings.media_token_ttl_sec
        ),
    }
