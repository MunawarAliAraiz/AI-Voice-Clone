"""Authenticated setup status for the desktop's publisher-downloaded audio tool."""

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/audio-tools", tags=["desktop"])


def _controller(request: Request):
    instance = getattr(request.app.state, "audio_tools", None)
    if instance is None:
        raise HTTPException(404, "Audio tools setup is available in the Windows desktop app.")
    return instance


@router.get("/status")
async def status(request: Request):
    return _controller(request).snapshot()


@router.post("/start", status_code=202)
async def start(request: Request):
    return await _controller(request).start()
