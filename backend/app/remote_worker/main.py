"""Pod-side HTTP boundary. It accepts resolved work, never chooses a model."""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.background import BackgroundTask

from ..config import Settings
from ..domain.transliterate import MAX_BATCH_CHUNKS, SUPPORTED_PAIRS
from ..inference.analyzer_scheduler import QWEN_ANALYZER_MODEL_ID
from ..inference.catalog import CATALOG
from ..inference.protocol import SchedulerProtocol, SynthRequest
from ..inference.transliterator_scheduler import GEMMA_TRANSLITERATOR_MODEL_ID
from ..main import _build_analyzer, _build_scheduler, _build_transliterator
from .model_install import REQUIRED_MODEL_IDS, ModelInstaller, release_manifest_id
from .model_pins import AUXILIARY_PINS

PROTOCOL_VERSION = 1
MAX_REFERENCE_BYTES = 50 * 1024 * 1024


class RemoteSynthRequest(BaseModel):
    model_id: str
    text: str = Field(min_length=1)
    reference_text: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    sample_rate: int = Field(default=44_100, ge=8000, le=192_000)


class RemoteAnalyzeRequest(BaseModel):
    language: str = Field(min_length=2, max_length=16)
    sentences: list[str] = Field(min_length=1, max_length=512)


class RemoteTransliterateRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=MAX_BATCH_CHUNKS)
    instruction: str = Field(default="", max_length=2000)
    source_script: str
    target_script: str
    source_language: Literal["en", "hi", "ur"] | None = None


def create_worker_app(
    *,
    scheduler: SchedulerProtocol | None = None,
    settings: Settings | None = None,
    token: str | None = None,
    analyzer: Any = None,
    transliterator: Any = None,
) -> FastAPI:
    settings = settings or Settings()
    token = token if token is not None else os.environ.get("POD_WORKER_TOKEN", "")
    if not token:
        raise ValueError("POD_WORKER_TOKEN is required")
    if settings.desktop_static_dir is not None or settings.remote_worker_url:
        raise ValueError("Pod worker requires local GPU runtimes, not a remote scheduler")
    require_installed = scheduler is None

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_dirs()
        app.state.scheduler = scheduler or _build_scheduler(settings)
        hf_home = Path(os.environ.get("HF_HOME", "/workspace/hf-cache"))
        app.state.installer = ModelInstaller(
            Path(os.environ.get("HF_HUB_CACHE", str(hf_home / "hub")))
        )
        app.state.analyzer = analyzer or _build_analyzer(settings)
        if transliterator is not None:
            app.state.transliterator, app.state.transliterator_reason = transliterator, None
        else:
            app.state.transliterator, app.state.transliterator_reason = _build_transliterator(
                settings, app.state.scheduler
            )
        try:
            yield
        finally:
            await app.state.installer.shutdown()
            if scheduler is None:
                await app.state.scheduler.shutdown()
            if analyzer is None:
                await app.state.analyzer.shutdown()
            if transliterator is None and app.state.transliterator is not None:
                await app.state.transliterator.shutdown()

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
                {
                    "id": status.spec.id,
                    "state": (
                        status.state.value
                        if app.state.installer.status(status.spec.id)["state"] == "installed"
                        else "not_downloaded"
                    ),
                    "revision": status.spec.hf_revision,
                    "install": app.state.installer.status(status.spec.id),
                }
                for status in statuses
            ]
            + [
                {
                    "id": model_id,
                    "revision": pin[1],
                    "state": "available"
                    if app.state.installer.status(model_id)["state"] == "installed"
                    else "not_downloaded",
                    "install": app.state.installer.status(model_id),
                }
                for model_id, pin in AUXILIARY_PINS.items()
            ],
        }

    @app.get("/v1/setup", dependencies=[Depends(authenticate)])
    async def setup_status() -> dict[str, object]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "manifest_id": release_manifest_id(),
            "required_model_ids": list(REQUIRED_MODEL_IDS),
            **app.state.installer.setup_status(),
        }

    @app.post("/v1/setup", dependencies=[Depends(authenticate)], status_code=202)
    async def start_setup() -> dict[str, object]:
        for model_id in REQUIRED_MODEL_IDS:
            app.state.installer.start(model_id)
        return await setup_status()

    def require_helper(model_id: str) -> None:
        if require_installed and app.state.installer.status(model_id)["state"] != "installed":
            raise HTTPException(409, "Download this helper model first")

    @app.post("/v1/analyze", dependencies=[Depends(authenticate)])
    async def analyze(body: RemoteAnalyzeRequest) -> dict[str, Any]:
        require_helper(QWEN_ANALYZER_MODEL_ID)
        if sum(map(len, body.sentences)) > 5000 or any(not s.strip() for s in body.sentences):
            raise HTTPException(
                422, "Analysis requires nonempty sentences totaling at most 5000 characters"
            )
        result = await app.state.analyzer.classify(
            language=body.language, sentences=tuple(body.sentences)
        )
        return {
            "protocol_version": PROTOCOL_VERSION,
            "model_id": QWEN_ANALYZER_MODEL_ID,
            "revision": AUXILIARY_PINS[QWEN_ANALYZER_MODEL_ID][1],
            **asdict(result),
        }

    @app.post("/v1/transliterate", dependencies=[Depends(authenticate)])
    async def transliterate(body: RemoteTransliterateRequest) -> dict[str, Any]:
        require_helper(GEMMA_TRANSLITERATOR_MODEL_ID)
        if (
            body.source_language not in {"en", "hi"}
            and (body.source_script, body.target_script) not in SUPPORTED_PAIRS
        ):
            raise HTTPException(422, "Unsupported script conversion pair")
        if body.target_script not in {"roman", "perso_arabic"}:
            raise HTTPException(422, "Unsupported translation target")
        if any(not text.strip() or len(text) > 6000 for text in body.texts):
            raise HTTPException(422, "Conversion passages must contain 1 to 6000 characters")
        if app.state.transliterator is None:
            raise HTTPException(
                409, app.state.transliterator_reason or "Script conversion unavailable"
            )
        results = await app.state.transliterator.convert_many(
            texts=body.texts,
            instruction=body.instruction,
            source_script=body.source_script,
            target_script=body.target_script,
            **(
                {"source_language": body.source_language}
                if body.source_language in {"en", "hi"}
                else {}
            ),
        )
        return {
            "protocol_version": PROTOCOL_VERSION,
            "model_id": GEMMA_TRANSLITERATOR_MODEL_ID,
            "revision": AUXILIARY_PINS[GEMMA_TRANSLITERATOR_MODEL_ID][1],
            "results": [asdict(result) for result in results],
            "source_language": body.source_language,
        }

    @app.post(
        "/v1/models/{model_id}/install", status_code=202, dependencies=[Depends(authenticate)]
    )
    async def install_model(model_id: str) -> dict[str, Any]:
        try:
            return app.state.installer.start(model_id)
        except KeyError as exc:
            raise HTTPException(404, "Unknown model ID") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/models/{model_id}/install", dependencies=[Depends(authenticate)])
    async def install_status(model_id: str) -> dict[str, Any]:
        try:
            return app.state.installer.status(model_id)
        except KeyError as exc:
            raise HTTPException(404, "Unknown model ID") from exc

    @app.post("/v1/models/{model_id}/warm", dependencies=[Depends(authenticate)])
    async def warm_model(model_id: str) -> dict[str, str]:
        if CATALOG.get(model_id) is None:
            raise HTTPException(status_code=404, detail="Unknown model ID")
        if require_installed and app.state.installer.status(model_id)["state"] != "installed":
            raise HTTPException(status_code=409, detail="Download this model first")
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
        if (
            require_installed
            and app.state.installer.status(payload.model_id)["state"] != "installed"
        ):
            raise HTTPException(status_code=409, detail="Download this model first")

        # Reference clips and rendered audio are ephemeral on the Pod. The
        # network volume is reserved for model weights and runtime caches.
        work = Path(tempfile.mkdtemp(prefix="vcs-worker-"))
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
