"""Run `python -m app.mcp` as a stdio MCP server for Codex or Claude."""

from __future__ import annotations

import asyncio
import time

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .client import StudioApiClient, StudioApiError

server = MCPServer(
    "AI Voice Clone Studio",
    instructions=(
        "Before generating, ask for missing script, language, reference voice and delivery. "
        "Use list_voices to confirm the reference profile ID; never invent an ID. "
        "For TTS, that reference determines the new output voice, not source-audio conversion. "
        "Use preview_speech_direction for supported delivery fields. Queue generate_speech once "
        "and report its job ID. Poll get_job or wait_for_job; claim audio is ready only when "
        "status is succeeded. Jobs, progress and completion appear in the open studio. "
        "Do not choose experimental models or spend outside the user's approved setup."
    ),
)
studio = StudioApiClient()


async def _request(method: str, path: str, *, body: dict | None = None) -> dict:
    try:
        async with asyncio.timeout(1):
            await studio.request("POST", "/api/agents/activity", body={"tool": path.split("?")[0]})
    except (StudioApiError, TimeoutError):
        pass  # Older web deployments have no desktop connection screen.
    try:
        return await studio.request(method, path, body=body)
    except StudioApiError as exc:
        raise ToolError(str(exc)) from exc


@server.tool()
async def studio_health() -> dict:
    """Check whether the local Voice Clone Studio is running."""
    return await _request("GET", "/api/health")


@server.tool()
async def list_voices() -> dict:
    """List enrolled voice profiles, including their IDs and languages."""
    return await _request("GET", "/api/voices")


@server.tool()
async def list_models() -> dict:
    """List available synthesis models and their current load states."""
    return await _request("GET", "/api/models")


@server.tool()
async def generate_speech(
    text: str,
    profile_id: int,
    language: str,
    model_id: str | None = None,
    title: str | None = None,
    speed: float = 1.0,
    apply_direction: bool = False,
    direction_plan: dict | None = None,
) -> dict:
    """Queue the user's script with a confirmed reference voice profile; returns a durable job ID.

    Ask for missing script, language, voice and delivery first. For delivery, preview this exact
    text and selected model with preview_speech_direction; it reports supported and ignored
    controls. Set apply_direction true to apply its supported plan. direction_plan can override
    segment index/emotion/intensity/energy/rate/pause_after_ms, never segment text.
    A reference profile shapes new generated output; this tool does not convert source recordings.
    """
    body = {"text": text, "profile_id": profile_id, "language": language}
    if model_id is not None:
        body["model_id"] = model_id
    if title is not None:
        body["title"] = title
    if speed != 1.0:
        body["speed"] = speed
    if apply_direction:
        body["apply_direction"] = True
    if direction_plan is not None:
        if not apply_direction:
            raise ToolError("Set apply_direction true when supplying a direction plan")
        body["direction_plan"] = direction_plan
    return await _request("POST", "/api/generate", body=body)


@server.tool()
async def preview_speech_direction(text: str, language: str, model_id: str | None = None) -> dict:
    """Preview delivery segments and actual model support for this exact script; does not generate.

    Use its plan to discuss the user's intended delivery. Do not promise ignored controls.
    """
    body = {"text": text, "language": language}
    if model_id is not None:
        body["model_id"] = model_id
    return await _request("POST", "/api/direction/analyze", body=body)


@server.tool()
async def wait_for_job(job_id: int, timeout_seconds: float = 20) -> dict:
    """Wait up to 25 seconds for a queued job; never enqueue a duplicate after a timeout.

    A timeout leaves the durable job running. Return its current progress and call again or
    get_job later. Succeeded results include generated audio; failed/cancelled are not success.
    """
    if not 0 <= timeout_seconds <= 25:
        raise ToolError("Choose a wait timeout between 0 and 25 seconds")
    deadline = time.monotonic() + timeout_seconds
    job = None
    try:
        async with asyncio.timeout(max(1, timeout_seconds)):
            while True:
                job = await _request("GET", f"/api/jobs/{job_id}")
                settled = job.get("status") in {"succeeded", "failed", "cancelled"}
                if settled or time.monotonic() >= deadline:
                    return {"job": job, "settled": settled, "timed_out": not settled}
                await asyncio.sleep(min(2, max(0, deadline - time.monotonic())))
    except TimeoutError:
        return {
            "job": job,
            "job_id": job_id,
            "settled": False,
            "timed_out": True,
            "next_action": "Check get_job later; the existing job was not cancelled or duplicated.",
        }


@server.tool()
async def get_job(job_id: int) -> dict:
    """Get a queued, running, completed, or failed studio job."""
    return await _request("GET", f"/api/jobs/{job_id}")


@server.tool()
async def cancel_job(job_id: int) -> dict:
    """Cancel a queued or running studio job."""
    return await _request("DELETE", f"/api/jobs/{job_id}")


@server.tool()
async def list_recent_jobs(page: int = 1, page_size: int = 20) -> dict:
    """List recent studio jobs and their statuses."""
    return await _request("GET", f"/api/jobs?page={page}&page_size={page_size}")


@server.tool()
async def list_history(page: int = 1, page_size: int = 20) -> dict:
    """List completed speech generations and their local media URLs."""
    return await _request("GET", f"/api/history?page={page}&page_size={page_size}")


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
