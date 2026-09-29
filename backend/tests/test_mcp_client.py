"""The MCP bridge carries local auth and returns meaningful API failures."""

from __future__ import annotations

import httpx
import pytest

from app.mcp.client import StudioApiClient, StudioApiError


@pytest.mark.asyncio
async def test_local_client_sends_key_and_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        assert request.headers["X-API-Key"] == "session-key"
        assert request.method == "POST"
        assert b'"profile_id":7' in request.content
        return httpx.Response(202, json={"id": 42, "status": "queued"})

    studio = StudioApiClient(base_url="http://127.0.0.1:8901", api_key="session-key",
                             transport=httpx.MockTransport(handler))
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


def test_remote_api_is_refused() -> None:
    with pytest.raises(ValueError, match="local studio"):
        StudioApiClient(base_url="https://example.com", api_key="should-not-leak")
