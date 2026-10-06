"""Optional per-user Cloudflare connection; validation reads only, no guard deployment.

References: Cloudflare API accounts/tokens/verify, user/tokens/verify and
workers/scripts/list. Connected credentials do not establish protection.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..runpod.secrets import _crypt

INVALID_INPUT = "Enter a valid Cloudflare account ID and API token."
ACCESS_DENIED = (
    "Check the Cloudflare account ID and token permissions. "
    "Workers Scripts read access is required."
)
INACTIVE_DETAIL = "The Cloudflare token is not active. Create an active token and try again."
UNEXPECTED_RESPONSE = "Cloudflare returned an unexpected response. Try again."
UNREACHABLE = "Cannot reach Cloudflare. Check your connection and try again."
RETRY_LATER = "Cloudflare is busy. Try again later."
SAVED_UNREADABLE = "Saved Cloudflare access could not be read. Connect it again."
CONNECTED_DETAIL = "Connected. Extra protection has not been set up."
MAX_RESPONSE = 512 * 1024
BASE_URL = "https://api.cloudflare.com/client/v4"


class CloudflareConnectionError(RuntimeError):
    """Fixed messages never include token values or provider bodies."""


@dataclass(frozen=True)
class CloudflareCredentials:
    account_id: str = field(repr=False)
    api_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.account_id, str)
            or not re.fullmatch(r"[A-Fa-f0-9]{32}", self.account_id)
            or not isinstance(self.api_token, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{20,512}", self.api_token)
        ):
            raise ValueError(INVALID_INPUT)
        object.__setattr__(self, "account_id", self.account_id.lower())


class CloudflareClient:
    def __init__(self, credentials: CloudflareCredentials, *, transport=None) -> None:
        self.credentials = credentials
        self._client = httpx.AsyncClient(
            timeout=15, follow_redirects=False, trust_env=False, transport=transport
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, *, allow_fallback: bool = False) -> dict | None:
        # Callers supply fixed paths built from an exactly validated account ID.
        allowed = {
            f"/accounts/{self.credentials.account_id}/tokens/verify",
            "/user/tokens/verify",
            f"/accounts/{self.credentials.account_id}/workers/scripts",
        }
        if path not in allowed:
            raise ValueError("Unsupported Cloudflare connection check.")
        async with self._client.stream(
            "GET",
            BASE_URL + path,
            headers={"Authorization": "Bearer " + self.credentials.api_token},
        ) as response:
            if response.status_code in {401, 403, 404}:
                if allow_fallback:
                    return None
                raise CloudflareConnectionError(ACCESS_DENIED)
            if response.status_code == 429 or response.status_code >= 500:
                raise CloudflareConnectionError(RETRY_LATER)
            if response.status_code != 200:
                raise CloudflareConnectionError(UNEXPECTED_RESPONSE)
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                if len(raw) + len(chunk) > MAX_RESPONSE:
                    raise CloudflareConnectionError(UNEXPECTED_RESPONSE)
                raw.extend(chunk)
            try:
                body = json.loads(raw)
                if not isinstance(body, dict) or body.get("success") is not True:
                    raise ValueError
            except (ValueError, UnicodeError):
                raise CloudflareConnectionError(UNEXPECTED_RESPONSE) from None
            return body

    async def validate(self) -> None:
        try:
            async with asyncio.timeout(40):
                verified = await self._get(
                    f"/accounts/{self.credentials.account_id}/tokens/verify", allow_fallback=True
                )
                if verified is None:
                    verified = await self._get("/user/tokens/verify")
                result = verified.get("result")
                if not isinstance(result, dict) or "status" not in result:
                    raise CloudflareConnectionError(UNEXPECTED_RESPONSE)
                if result["status"] != "active":
                    raise CloudflareConnectionError(INACTIVE_DETAIL)
                scripts = await self._get(
                    f"/accounts/{self.credentials.account_id}/workers/scripts"
                )
                if not isinstance(scripts.get("result"), list):
                    raise CloudflareConnectionError(UNEXPECTED_RESPONSE)
        except (httpx.HTTPError, TimeoutError):
            raise CloudflareConnectionError(UNREACHABLE) from None


class CloudflareConnectionStore:
    """Only this desktop profile's DPAPI pair; no environment fallback."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "secrets" / "cloudflare-connection.dpapi"

    def get(self) -> CloudflareCredentials | None:
        if not self.path.exists():
            return None
        try:
            if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size > 8192:
                raise ValueError
            record = json.loads(_crypt(self.path.read_bytes(), protect=False))
            if (
                not isinstance(record, dict)
                or set(record) != {"version", "account_id", "api_token"}
                or record["version"] != 1
            ):
                raise ValueError
            return CloudflareCredentials(record["account_id"], record["api_token"])
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise CloudflareConnectionError(SAVED_UNREADABLE) from None

    def set(self, credentials: CloudflareCredentials) -> None:
        encrypted = _crypt(
            json.dumps(
                {
                    "version": 1,
                    "account_id": credentials.account_id,
                    "api_token": credentials.api_token,
                }
            ).encode(),
            protect=True,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent, prefix=".cloudflare-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def status(self) -> dict:
        credentials = self.get()
        return {
            "connected": credentials is not None,
            "account_id_masked": "****" + credentials.account_id[-4:] if credentials else None,
            "protection_ready": False,
            "detail": CONNECTED_DETAIL if credentials else None,
        }
