"""Narrow, testable HTTP client behind the MCP tools."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..desktop_session import DesktopSessionStore, default_session_path


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
        base_url = base_url or os.environ.get("VCS_MCP_API_BASE")
        self._base_url = self._validate_base(base_url) if base_url else None
        self._api_key = api_key if api_key is not None else os.environ.get("VCS_MCP_API_KEY", "")
        self._transport = transport

    @staticmethod
    def _validate_base(base_url: str) -> str:
        parsed = urlsplit(base_url)
        try:
            valid_port = parsed.port is not None and 1 <= parsed.port <= 65535
        except ValueError:
            valid_port = False
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not valid_port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("MCP may connect only to the local studio API")
        return base_url.rstrip("/")

    def _connection(self) -> tuple[str, str]:
        if self._base_url is not None:
            return self._base_url, self._api_key
        try:
            session = DesktopSessionStore(default_session_path()).load()
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            raise StudioApiError(
                "Open Voice Clone Studio first; its desktop session is unavailable"
            ) from exc
        return session.base_url, session.api_key

    async def request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not path.startswith("/api/") or "#" in path:
            raise ValueError("MCP may request only studio API paths")
        base_url, api_key = self._connection()
        headers = {"X-API-Key": api_key} if api_key else {}
        async with httpx.AsyncClient(
            base_url=base_url,
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
