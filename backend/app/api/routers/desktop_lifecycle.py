"""Pause cloud work before the native window exits, independently of updates."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from ...runpod.controller import controller

router = APIRouter(prefix="/desktop/lifecycle", tags=["Desktop lifecycle"])


async def _prepare_exit(app) -> dict:
    # Stop queue consumers before pausing compute: a queued job must not start
    # another paid session after the close request has fenced HTTP mutations.
    if getattr(app.state, "owns_jobs", False):
        await app.state.jobs.stop(drain_timeout_sec=2)
        app.state.desktop_exit_jobs_stopped = True
    cloud = controller(app.state.settings)
    await cloud.pause_for_app_exit(
        queue_paused=bool(getattr(app.state, "desktop_exit_jobs_stopped", False)))
    snapshot = await cloud.snapshot()
    confirmed = not snapshot.get("compute") and not snapshot.get("cleanup_pending")
    if not confirmed:
        raise HTTPException(
            409, "Could not confirm the cloud machine stopped. Try again or return to the app.")
    return {"prepared": True, "stop_confirmed": True}


@router.post("/prepare-exit")
async def prepare_exit(request: Request) -> dict:
    app = request.app
    if app.state.settings.desktop_static_dir is None:
        raise HTTPException(404, "Desktop closing is unavailable in web mode")
    # Middleware holds mutation admission while this flag is set. Keep the
    # cleanup task alive even if the window/network disconnects mid-request.
    app.state.desktop_exiting = True
    task = getattr(app.state, "desktop_exit_task", None)
    if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
        task = asyncio.create_task(_prepare_exit(app))
        app.state.desktop_exit_task = task
        # Observe errors even if the requesting connection has already gone.
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    try:
        return await asyncio.shield(task)
    except HTTPException:
        raise
    except Exception as exc:
        # Provider exception text can contain diagnostics/identifiers. Do not
        # put it into a native message or expose credentials through errors.
        raise HTTPException(
            503, "Could not finish closing. Try again or return to the app.") from exc


@router.post("/cancel-exit")
async def cancel_exit(request: Request) -> dict:
    app = request.app
    if app.state.settings.desktop_static_dir is None:
        raise HTTPException(404, "Desktop closing is unavailable in web mode")
    task = getattr(app.state, "desktop_exit_task", None)
    if task is not None and not task.done():
        raise HTTPException(
            409, "The cloud machine is still stopping. Please wait, then try again.")
    if getattr(app.state, "desktop_exit_jobs_stopped", False):
        # Cancelled running rows are interrupted, not silently rerun. Queued
        # rows retain their normal durable queue semantics.
        await app.state.jobs.reap_stale()
        await app.state.jobs.start()
        app.state.desktop_exit_jobs_stopped = False
    app.state.desktop_exit_task = None
    app.state.desktop_exiting = False
    # Do not clear desktop_updating: it is an independent admission fence.
    return {"prepared": False}
