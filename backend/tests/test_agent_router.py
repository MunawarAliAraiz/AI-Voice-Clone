"""Agent configuration endpoints keep desktop/session boundaries."""

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.agents.configure import AgentConnections
from app.api.deps import ApiKeyMiddleware
from app.api.routers import agents
from app.config import Settings


@pytest.mark.asyncio
async def test_agents_require_session_auth_and_configure_only_requested_client(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    executable = tmp_path / "mcp.exe"
    executable.write_bytes(b"isolated-test-placeholder")
    service = AgentConnections(
        tmp_path / "data", home=home, appdata=tmp_path / "roaming", mcp_executable=executable
    )
    monkeypatch.setattr(agents, "_connections", lambda _: service)
    app = FastAPI()
    app.state.settings = Settings(data_dir=tmp_path / "data", desktop_static_dir=tmp_path)
    app.state.desktop_mutation_lock = asyncio.Lock()
    app.state.desktop_updating = False
    app.include_router(agents.router, prefix="/api")
    app.add_middleware(ApiKeyMiddleware, api_key="test-session")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        assert (await client.get("/api/agents")).status_code == 401
        headers = {"X-API-Key": "test-session"}
        initial = await client.get("/api/agents", headers=headers)
        assert initial.status_code == 200 and initial.json()["activity"] is None
        configured = await client.post(
            "/api/agents/configure/claude_code", json={}, headers=headers
        )
        assert configured.status_code == 200 and configured.json()["configured"]
        assert service.paths["claude_code"].is_file()
        assert not service.paths["codex"].exists() and not service.paths["claude_desktop"].exists()
        assert (
            await client.post(
                "/api/agents/activity", json={"tool": "/api/generate"}, headers=headers
            )
        ).status_code == 204
        assert (await client.get("/api/agents", headers=headers)).json()["activity"][
            "tool"
        ] == "/api/generate"


@pytest.mark.asyncio
async def test_agents_unavailable_in_web_mode(tmp_path: Path) -> None:
    app = FastAPI()
    app.state.settings = Settings(data_dir=tmp_path)
    app.include_router(agents.router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        assert (await client.get("/api/agents")).status_code == 404
