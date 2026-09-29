"""Run `python -m app.mcp` as a stdio MCP server for Codex or Claude."""

from __future__ import annotations

from mcp.server import MCPServer

from .client import StudioApiClient

server = MCPServer("AI Voice Clone Studio")
studio = StudioApiClient()


@server.tool()
async def studio_health() -> dict:
    """Check whether the local Voice Clone Studio is running."""
    return await studio.request("GET", "/api/health")


@server.tool()
async def list_voices() -> dict:
    """List enrolled voice profiles, including their IDs and languages."""
    return await studio.request("GET", "/api/voices")


@server.tool()
async def list_models() -> dict:
    """List available synthesis models and their current load states."""
    return await studio.request("GET", "/api/models")


@server.tool()
async def generate_speech(
    text: str,
    profile_id: int,
    language: str,
    model_id: str | None = None,
    title: str | None = None,
) -> dict:
    """Queue speech generation; returns a job ID to poll with get_job."""
    body = {"text": text, "profile_id": profile_id, "language": language}
    if model_id is not None:
        body["model_id"] = model_id
    if title is not None:
        body["title"] = title
    return await studio.request("POST", "/api/generate", body=body)


@server.tool()
async def get_job(job_id: int) -> dict:
    """Get a queued, running, completed, or failed studio job."""
    return await studio.request("GET", f"/api/jobs/{job_id}")


@server.tool()
async def cancel_job(job_id: int) -> dict:
    """Cancel a queued or running studio job."""
    return await studio.request("DELETE", f"/api/jobs/{job_id}")


@server.tool()
async def list_recent_jobs(page: int = 1, page_size: int = 20) -> dict:
    """List recent studio jobs and their statuses."""
    return await studio.request("GET", f"/api/jobs?page={page}&page_size={page_size}")


@server.tool()
async def list_history(page: int = 1, page_size: int = 20) -> dict:
    """List completed speech generations and their local media URLs."""
    return await studio.request("GET", f"/api/history?page={page}&page_size={page_size}")


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
