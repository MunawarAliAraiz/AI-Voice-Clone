"""Narrow, testable HTTP client behind the MCP tools."""

from __future__ import annotations

import os
from typing import Any

import httpx


class StudioApiError(RuntimeError):
    pass


class StudioApiClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        base_url = base_url or os.environ.get("VCS_MCP_API_BASE", "http://127.0.0.1:8000")
        if not base_url.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("MCP may connect only to the local studio API")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key if api_key is not None else os.environ.get("VCS_MCP_API_KEY", "")
        self._transport = transport

    async def request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = {"X-API-Key": self._api_key} if self._api_key else {}
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=30.0,
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            try:
                response = await client.request(method, path, json=body)
            except httpx.HTTPError as exc:
                raise StudioApiError("The local studio API is unavailable") from exc
        if response.status_code >= 400:
            try:
                problem = response.json()
            except ValueError:
                problem = {}
            detail = problem.get("detail", response.reason_phrase)
            code = problem.get("code", "HTTP_ERROR")
            raise StudioApiError(f"{code}: {detail}")
        if response.status_code == 204:
            return {"status": "ok"}
        value = response.json()
        if not isinstance(value, dict):
            raise StudioApiError("Studio returned an invalid response")
        return value
