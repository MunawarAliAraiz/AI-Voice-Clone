"""Atomically quiesce desktop mutations before the signed native updater exits."""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from ...jobs.types import JobKind
from ...runpod.controller import controller

router = APIRouter(prefix="/desktop/updates", tags=["Desktop updates"])


@router.post("/prepare")
async def prepare_update(request: Request) -> dict:
    settings = request.app.state.settings
    if settings.desktop_static_dir is None:
        raise HTTPException(404, "Desktop updates are unavailable in web mode")
    # A lost response must not strand the updater: the admission fence is
    # already established, so preparing the same idle session is idempotent.
    if request.app.state.desktop_updating:
        return {"prepared": True}
    pending = sum(
        await asyncio.gather(*(request.app.state.db.count_pending(kind.value) for kind in JobKind))
    )
    if pending:
        raise HTTPException(409, "Finish or cancel queued and running jobs before restarting.")
    cloud = controller(settings)
    if (
        (await cloud.snapshot())["compute"]
        or cloud.active
        or any(
            task is not None and not task.done() for task in (cloud.setup_task, cloud.release_task)
        )
    ):
        raise HTTPException(409, "Wait for the current cloud session to finish before restarting.")
    # ApiKeyMiddleware holds the shared mutation lock until this flag is set.
    request.app.state.desktop_updating = True
    return {"prepared": True}


@router.post("/cancel")
async def cancel_update(request: Request) -> dict:
    if request.app.state.settings.desktop_static_dir is None:
        raise HTTPException(404, "Desktop updates are unavailable in web mode")
    request.app.state.desktop_updating = False
    return {"prepared": False}
