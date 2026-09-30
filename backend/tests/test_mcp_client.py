"""The MCP bridge carries local auth and returns meaningful API failures."""

from __future__ import annotations

import httpx
import pytest

from app.desktop_session import DesktopSession, DesktopSessionStore
from app.mcp.client import StudioApiClient, StudioApiError


@pytest.mark.asyncio
async def test_local_client_sends_key_and_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        assert request.headers["X-API-Key"] == "session-key"
        assert request.method == "POST"
        assert b'"profile_id":7' in request.content
        return httpx.Response(202, json={"id": 42, "status": "queued"})

    studio = StudioApiClient(
        base_url="http://127.0.0.1:8901",
        api_key="session-key",
        transport=httpx.MockTransport(handler),
    )
    result = await studio.request("POST", "/api/generate", body={"profile_id": 7})
    assert result == {"id": 42, "status": "queued"}


@pytest.mark.asyncio
async def test_problem_json_is_exposed_without_traceback() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(422, json={"code": "NO_ROUTE", "detail": "Choose Urdu"})
    )
    studio = StudioApiClient(base_url="http://localhost:8000", transport=transport)
    with pytest.raises(StudioApiError, match="NO_ROUTE: Choose Urdu"):
        await studio.request("GET", "/api/models")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://localhost:8000@evil.test",
        "http://127.0.0.1:8000/proxy",
        "http://localhost:8000?next=x",
        "http://localhost:99999",
        "http://localhost",
    ],
)
def test_remote_api_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match="local studio"):
        StudioApiClient(base_url=url, api_key="should-not-leak")


@pytest.mark.asyncio
async def test_session_discovery_refreshes_after_desktop_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("VCS_MCP_API_BASE", raising=False)
    monkeypatch.setenv("VCS_MCP_SESSION_FILE", str(tmp_path / "session.dpapi"))
    monkeypatch.setattr("app.desktop_session._crypt", lambda data, protect: data)
    seen = []

    def handler(request):
        seen.append((request.url.port, request.headers["X-API-Key"]))
        return httpx.Response(200, json={"status": "ok"})

    studio = StudioApiClient(transport=httpx.MockTransport(handler))
    with pytest.raises(StudioApiError, match="Open Voice Clone Studio"):
        await studio.request("GET", "/api/health")
    store = DesktopSessionStore(tmp_path / "session.dpapi")
    first = DesktopSession(8910, "a" * 64, 100)
    second = DesktopSession(8920, "b" * 64, 200)
    store.save(first)
    await studio.request("GET", "/api/health")
    store.save(second)
    store.clear_if_current(first)
    await studio.request("GET", "/api/health")
    assert seen == [(8910, "a" * 64), (8920, "b" * 64)]
    store.clear_if_current(second)
    assert not store.path.exists()


@pytest.mark.asyncio
async def test_tool_cannot_send_key_to_path_supplied_host() -> None:
    studio = StudioApiClient(base_url="http://localhost:8000", api_key="key")
    with pytest.raises(ValueError, match="API paths"):
        await studio.request("GET", "//evil.test/api/health")
