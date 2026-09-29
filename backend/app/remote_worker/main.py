"""Pod-side HTTP boundary. It accepts resolved work, never chooses a model."""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.background import BackgroundTask

from ..config import Settings
from ..inference.catalog import CATALOG
from ..inference.protocol import SchedulerProtocol, SynthRequest
from ..main import _build_scheduler
from .model_install import ModelInstaller

PROTOCOL_VERSION = 1
MAX_REFERENCE_BYTES = 50 * 1024 * 1024


class RemoteSynthRequest(BaseModel):
    model_id: str
    text: str = Field(min_length=1)
    reference_text: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    sample_rate: int = Field(default=44_100, ge=8000, le=192_000)


def create_worker_app(
    *,
    scheduler: SchedulerProtocol | None = None,
    settings: Settings | None = None,
    token: str | None = None,
) -> FastAPI:
    settings = settings or Settings()
    token = token if token is not None else os.environ.get("POD_WORKER_TOKEN", "")
    if not token:
        raise ValueError("POD_WORKER_TOKEN is required")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_dirs()
        app.state.scheduler = scheduler or _build_scheduler(settings)
        hf_home = Path(os.environ.get("HF_HOME", "/workspace/hf-cache"))
        app.state.installer = ModelInstaller(
            Path(os.environ.get("HF_HUB_CACHE", str(hf_home / "hub")))
        )
        try:
            yield
        finally:
            await app.state.installer.shutdown()
            if scheduler is None:
                await app.state.scheduler.shutdown()

    app = FastAPI(title="Voice Clone Pod Worker", lifespan=lifespan)

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="Worker authentication required")

    @app.get("/v1/health", dependencies=[Depends(authenticate)])
    async def health() -> dict[str, int | str]:
        return {"status": "ok", "protocol_version": PROTOCOL_VERSION}

    @app.get("/v1/models", dependencies=[Depends(authenticate)])
    async def models() -> dict[str, object]:
        statuses = await app.state.scheduler.status()
        return {
            "protocol_version": PROTOCOL_VERSION,
            "models": [
                {"id": status.spec.id, "state": status.state.value,
                 "revision": status.spec.hf_revision,
                 "install": app.state.installer.status(status.spec.id)}
                for status in statuses
            ],
        }

    @app.post("/v1/models/{model_id}/install", status_code=202,
              dependencies=[Depends(authenticate)])
    async def install_model(model_id: str) -> dict[str, str]:
        try:
            return app.state.installer.start(model_id)
        except KeyError as exc:
            raise HTTPException(404, "Unknown model ID") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/models/{model_id}/install", dependencies=[Depends(authenticate)])
    async def install_status(model_id: str) -> dict[str, str]:
        try:
            return app.state.installer.status(model_id)
        except KeyError as exc:
            raise HTTPException(404, "Unknown model ID") from exc

    @app.post("/v1/models/{model_id}/warm", dependencies=[Depends(authenticate)])
    async def warm_model(model_id: str) -> dict[str, str]:
        if CATALOG.get(model_id) is None:
            raise HTTPException(status_code=404, detail="Unknown model ID")
        await app.state.scheduler.warm(model_id)
        return {"status": "warm", "model_id": model_id}

    @app.post("/v1/synthesize", dependencies=[Depends(authenticate)])
    async def synthesize(
        request: Annotated[str, Form()],
        reference_audio: Annotated[UploadFile, File()],
    ) -> FileResponse:
        try:
            payload = RemoteSynthRequest.model_validate(json.loads(request))
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail="Invalid synthesis request") from exc
        if CATALOG.get(payload.model_id) is None:
            raise HTTPException(status_code=404, detail="Unknown model ID")

        work = Path(tempfile.mkdtemp(prefix="vcs-worker-", dir=settings.data_dir))
        reference = work / "reference.wav"
        output = work / "output.wav"
        try:
            count = 0
            with reference.open("wb") as stream:
                while chunk := await reference_audio.read(1024 * 1024):
                    count += len(chunk)
                    if count > MAX_REFERENCE_BYTES:
                        raise HTTPException(status_code=413, detail="Reference audio exceeds 50 MB")
                    stream.write(chunk)
            result = await app.state.scheduler.synthesize(
                SynthRequest(
                    model_id=payload.model_id,
                    text=payload.text,
                    reference_audio=reference,
                    output_path=output,
                    reference_text=payload.reference_text,
                    params=payload.params,
                    sample_rate=payload.sample_rate,
                )
            )
            if result.model_id != payload.model_id or not output.is_file():
                raise RuntimeError("Worker returned no audio or changed the chosen model")
            return FileResponse(
                output,
                media_type="audio/wav",
                filename="generated.wav",
                headers={
                    "X-Model-Id": result.model_id,
                    "X-Generation-Time-Sec": str(result.gen_time_sec),
                    "X-Audio-Duration-Sec": str(result.duration_sec),
                    "X-Load-Time-Sec": str(result.load_time_sec),
                },
                background=BackgroundTask(_cleanup, work),
            )
        except BaseException:
            _cleanup(work)
            raise
        finally:
            await reference_audio.close()

    return app


def _cleanup(work: Path) -> None:
    for child in work.iterdir():
        child.unlink(missing_ok=True)
    work.rmdir()


if os.environ.get("POD_WORKER_TOKEN"):
    app = create_worker_app()
