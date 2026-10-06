"""Pod-free, read-only Runpod storage authentication. No model transfers yet.

The management key cannot authorize S3. A separate user access key and S3
secret are encrypted with Windows DPAPI and bound to the selected account/volume.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from .secrets import _crypt

REGION_ENDPOINTS = MappingProxyType(
    {
        region: f"https://s3api-{region.lower()}.runpod.io"
        for region in (
            "EU-CZ-1",
            "EU-RO-1",
            "EUR-IS-1",
            "EUR-NO-1",
            "US-CA-2",
            "US-GA-2",
            "US-IL-1",
            "US-KS-2",
            "US-MD-1",
            "US-MO-1",
            "US-MO-2",
            "US-NC-1",
            "US-NC-2",
            "US-NE-1",
            "US-WA-1",
        )
    }
)
PREFIXES = frozenset({"hf-cache/hub/", "voice-clone/hf-cache/hub/"})
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class StorageAccessError(RuntimeError):
    """Stable user-facing errors never include credentials or provider bodies."""


@dataclass(frozen=True)
class StorageCredentials:
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"user_[A-Za-z0-9_-]{4,160}", self.access_key):
            raise ValueError("Enter the storage access key shown by Runpod.")
        if not re.fullmatch(r"rps_[A-Za-z0-9_-]{8,240}", self.secret_key):
            raise ValueError("Enter the storage secret shown by Runpod.")


@dataclass(frozen=True)
class StorageBinding:
    account: str = field(repr=False)
    volume_id: str
    region: str
    prefix: str = "hf-cache/hub/"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", self.account):
            raise ValueError("Connect your Runpod account first.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.volume_id):
            raise ValueError("Select a valid model storage volume.")
        if self.region not in REGION_ENDPOINTS:
            raise ValueError("This storage region does not support direct downloads.")
        if self.prefix not in PREFIXES:
            raise ValueError("The selected model storage folder is not supported.")


def canonical_query(parameters: tuple[tuple[str, str], ...]) -> str:
    """AWS percent encoding, sorted after encoding; preserve duplicate values."""
    return "&".join(
        f"{key}={value}"
        for key, value in sorted(
            (quote(key, safe="-_.~"), quote(value, safe="-_.~")) for key, value in parameters
        )
    )


def signature_headers(
    *,
    method: str,
    host: str,
    path: str,
    parameters: tuple[tuple[str, str], ...],
    access_key: str,
    secret_key: str,
    region: str,
    now: datetime,
    extra_headers: dict[str, str] | None = None,
    payload_sha256: str = EMPTY_SHA256,
) -> dict[str, str]:
    """Pure AWS SigV4 signer; the network client fixes endpoint and operations.

    Reference: AWS S3 developer guide sig-v4-header-based-auth.html.
    No URL normalization: an S3 object's path is its exact encoded key.
    """
    if now.tzinfo is None:
        raise ValueError("Signing time must include a timezone")
    if not re.fullmatch(r"[a-f0-9]{64}", payload_sha256):
        raise ValueError("Invalid signed payload digest")
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    date = stamp[:8]
    headers = {"host": host, "x-amz-date": stamp, "x-amz-content-sha256": payload_sha256}
    if extra_headers:
        if any(
            key.lower() in headers
            or not re.fullmatch(r"[A-Za-z0-9-]+", key)
            or "\r" in value
            or "\n" in value
            for key, value in extra_headers.items()
        ):
            raise ValueError("Invalid extra signing header")
        headers.update(
            {key.lower(): " ".join(value.split()) for key, value in extra_headers.items()}
        )
    signed = ";".join(sorted(headers))
    canonical_headers = "".join(f"{key}:{headers[key]}\n" for key in sorted(headers))
    encoded_path = quote(path, safe="/-_.~")
    request = "\n".join(
        (
            method,
            encoded_path,
            canonical_query(parameters),
            canonical_headers,
            signed,
            payload_sha256,
        )
    )
    scope = f"{date}/{region}/s3/aws4_request"
    to_sign = "\n".join(
        ("AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(request.encode()).hexdigest())
    )
    key = ("AWS4" + secret_key).encode()
    for value in (date, region, "s3", "aws4_request"):
        key = hmac.new(key, value.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope},"
        f"SignedHeaders={signed},Signature={signature}"
    )
    return headers


class StorageAccessClient:
    """Validate access with HEAD and one bounded listing; never creates objects."""

    def __init__(
        self,
        credentials: StorageCredentials,
        binding: StorageBinding,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.credentials = credentials
        self.binding = binding
        self._client = httpx.AsyncClient(
            timeout=20, follow_redirects=False, trust_env=False, transport=transport
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def validate(self) -> None:
        try:
            async with asyncio.timeout(45):
                await self._validate()
        except TimeoutError:
            raise StorageAccessError("Storage check took too long. Try again.") from None

    async def _validate(self) -> None:
        endpoint = REGION_ENDPOINTS[self.binding.region]
        host = httpx.URL(endpoint).host
        path = "/" + self.binding.volume_id
        for method, parameters in (
            ("HEAD", ()),
            ("GET", (("list-type", "2"), ("max-keys", "1"), ("prefix", self.binding.prefix))),
        ):
            headers = signature_headers(
                method=method,
                host=host,
                path=path,
                parameters=parameters,
                access_key=self.credentials.access_key,
                secret_key=self.credentials.secret_key,
                region=self.binding.region,
                now=datetime.now(UTC),
            )
            url = endpoint + quote(path, safe="/-_.~")
            if parameters:
                url += "?" + canonical_query(parameters)
            try:
                async with self._client.stream(method, url, headers=headers) as response:
                    if response.status_code in {401, 403}:
                        raise StorageAccessError(
                            "Storage access was denied. Check both storage keys."
                        )
                    if response.status_code == 404:
                        raise StorageAccessError("The selected storage volume was not found.")
                    if response.status_code != 200:
                        raise StorageAccessError(
                            "Runpod could not check storage access. Try again."
                        )
                    # Do not parse, retain or expose object names returned by the check.
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 64 * 1024:
                            raise StorageAccessError(
                                "Runpod returned an unexpected storage response."
                            )
                    if method == "GET":
                        try:
                            text = body.decode("utf-8")
                            if (
                                "\0" in text
                                or "<!DOCTYPE" in text.upper()
                                or "<!ENTITY" in text.upper()
                            ):
                                raise ValueError
                            # Bounded UTF-8 only; reject DTD/entity declarations before parsing.
                            root = ElementTree.fromstring(text)  # noqa: S314

                            def local(tag: str) -> str:
                                return tag.rsplit("}", 1)[-1]

                            name = next(
                                (item.text for item in root if local(item.tag) == "Name"), None
                            )
                            if (
                                local(root.tag) != "ListBucketResult"
                                or name != self.binding.volume_id
                            ):
                                raise ValueError
                        except (ElementTree.ParseError, ValueError, UnicodeError):
                            raise StorageAccessError(
                                "Runpod returned an unexpected storage response."
                            ) from None
            except httpx.HTTPError:
                raise StorageAccessError(
                    "Cannot reach storage. Check your connection and try again."
                ) from None


class StorageAccessStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "secrets" / "runpod-storage-access.dpapi"

    def get(self, binding: StorageBinding) -> StorageCredentials | None:
        if not self.path.is_file():
            return None
        try:
            if self.path.stat().st_size > 8192:
                raise ValueError
            record = json.loads(_crypt(self.path.read_bytes(), protect=False))
            if not isinstance(record, dict) or record.get("version") != 1:
                raise ValueError
            stored = record.get("binding")
            expected = {
                "account": binding.account,
                "volume_id": binding.volume_id,
                "region": binding.region,
                "prefix": binding.prefix,
            }
            if stored != expected:
                return None
            return StorageCredentials(record["access_key"], record["secret_key"])
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise StorageAccessError(
                "Saved storage access could not be read. Connect it again."
            ) from None

    def set(self, credentials: StorageCredentials, binding: StorageBinding) -> None:
        record = {
            "version": 1,
            "access_key": credentials.access_key,
            "secret_key": credentials.secret_key,
            "binding": {
                "account": binding.account,
                "volume_id": binding.volume_id,
                "region": binding.region,
                "prefix": binding.prefix,
            },
        }
        # Encrypt before creating a temporary file. No plaintext is written to disk.
        encrypted = _crypt(json.dumps(record).encode(), protect=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent, prefix=".storage-access-", delete=False
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

    def status(self, binding: StorageBinding) -> dict:
        credentials = self.get(binding)
        return {
            "connected": credentials is not None,
            "access_key_masked": "****" + credentials.access_key[-4:]
            if credentials
            else None,
            "region": binding.region,
            "download_supported": False,
        }
