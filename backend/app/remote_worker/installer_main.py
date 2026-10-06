"""CPU-only authenticated model installer; never imports GPU schedulers."""

from __future__ import annotations

import contextlib
import hmac
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException

from ..inference.catalog import CATALOG
from .model_install import REQUIRED_MODEL_IDS, ModelInstaller
from .model_manifest import atomic_json, bound_path
from .model_pins import AUXILIARY_PINS


def create_installer_app(
    *,
    token: str | None = None,
    cache_dir: Path | None = None,
    volume_root: Path | None = None,
    installer: ModelInstaller | None = None,
) -> FastAPI:
    token = token if token is not None else os.environ.get("POD_WORKER_TOKEN", "")
    if not token:
        raise ValueError("POD_WORKER_TOKEN is required")
    root = (volume_root or Path(os.environ.get("VCS_MODEL_VOLUME_ROOT", "/workspace"))).resolve()
    cache = (cache_dir or Path(os.environ.get("HF_HUB_CACHE", "/workspace/hf-cache/hub"))).resolve()
    if (
        not root.is_dir()
        or not cache.is_relative_to(root)
        or (volume_root is None and not root.is_mount())
    ):
        raise ValueError("Mounted persistent model volume is required")
    manager = installer or ModelInstaller(cache)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.installer = manager
        try:
            yield
        finally:
            await manager.shutdown()

    app = FastAPI(title="Voice Clone Model Installer", lifespan=lifespan)

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        if not authorization or not hmac.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "Worker authentication required")

    @app.get("/v1/health", dependencies=[Depends(authenticate)])
    async def health() -> dict:
        return {"status": "ok", "protocol_version": 1, "role": "model_installer"}

    @app.get("/v1/models", dependencies=[Depends(authenticate)])
    async def models() -> dict:
        ids = [spec.id for spec in CATALOG.specs] + list(AUXILIARY_PINS)
        return {
            "protocol_version": 1,
            "models": [
                {
                    "id": model_id,
                    "state": manager.status(model_id)["state"],
                    "install": manager.status(model_id),
                }
                for model_id in ids
            ],
        }

    @app.get("/v1/setup", dependencies=[Depends(authenticate)])
    async def setup_status() -> dict:
        return {"protocol_version": 1, **manager.setup_status()}

    @app.post("/v1/setup", status_code=202, dependencies=[Depends(authenticate)])
    async def setup() -> dict:
        if manager.capacity_status().get("sufficient") is not True:
            raise HTTPException(409, "Check model storage before downloading")
        for model_id in REQUIRED_MODEL_IDS:
            manager.start(model_id)
        return {"protocol_version": 1, **manager.setup_status()}

    @app.post("/v1/capacity", status_code=202, dependencies=[Depends(authenticate)])
    async def start_capacity() -> dict:
        marker = bound_path(cache, ".vcs-volume.json")
        if marker.exists():
            if marker.is_symlink():
                raise HTTPException(409, "Storage identity could not be checked")
            try:
                identity = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise HTTPException(409, "Storage identity could not be checked") from None
            if (
                not isinstance(identity, dict)
                or identity.get("app_id") != "studio.voiceclone.desktop"
            ):
                raise HTTPException(409, "This storage folder belongs to another app")
        return {"protocol_version": 1, **manager.start_capacity()}

    @app.get("/v1/capacity", dependencies=[Depends(authenticate)])
    async def capacity_status() -> dict:
        result = manager.capacity_status()
        if result.get("sufficient"):
            marker = bound_path(cache, ".vcs-volume.json")
            cache.mkdir(parents=True, exist_ok=True)
            atomic_json(
                marker,
                {
                    "app_id": "studio.voiceclone.desktop",
                    "version": 1,
                    "volume_id": os.environ.get("VCS_VOLUME_ID"),
                },
            )
        return {"protocol_version": 1, "app_owned": bool(result.get("sufficient")), **result}

    @app.get("/v1/models/{model_id}/install", dependencies=[Depends(authenticate)])
    async def model_status(model_id: str) -> dict:
        try:
            return manager.status(model_id)
        except KeyError:
            raise HTTPException(404, "Unknown model") from None

    @app.post(
        "/v1/models/{model_id}/install", status_code=202, dependencies=[Depends(authenticate)]
    )
    async def model_install(model_id: str) -> dict:
        try:
            return manager.start(model_id)
        except KeyError:
            raise HTTPException(404, "Unknown model") from None

    return app
