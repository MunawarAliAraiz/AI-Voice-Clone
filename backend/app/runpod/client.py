"""Runpod REST v2 client. Tokens never enter browser storage or log output."""

from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Any, NoReturn

import httpx

API_BASE = "https://api.runpod.io/v2"


class RunpodApiError(RuntimeError):
    def __init__(
        self, message: str, status_code: int | None = None, *, request_rejected: bool = False
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_rejected = request_rejected


POD_START_BLOCKED_REASON = (
    "Cloud generation and model downloads are paused because automatic spending "
    "protection is unavailable. A Runpod machine could keep charging while this "
    "PC is offline. This needs an app fix; retrying or adding funds will not help."
)


def _reject_unprotected_pod_creation() -> NoReturn:
    raise RunpodApiError(POD_START_BLOCKED_REASON, request_rejected=True)


class RunpodClient:
    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not api_key:
            raise ValueError("Runpod API key is required")
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
            transport=transport,
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        allow_graphql_errors: bool = False,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, params=params, json=body)
        except httpx.HTTPError as exc:
            raise RunpodApiError("Cannot reach Runpod") from exc
        if response.is_error or response.is_redirect:
            # Apollo commonly sends parse/schema failures as HTTP 400. Inspect
            # only structured GraphQL errors through the same secret-safe code
            # classifier as HTTP 200; never infer rejection from HTTP alone.
            if allow_graphql_errors and response.status_code == 400:
                try:
                    rejected = response.json()
                except ValueError:
                    rejected = None
                if isinstance(rejected, dict) and rejected.get("errors"):
                    return rejected
            # Runpod responses may include account details. Keep the public
            # error stable and do not echo arbitrary provider bodies or keys.
            raise RunpodApiError(
                f"Runpod returned HTTP {response.status_code}", response.status_code
            )
        if response.status_code == 204:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise RunpodApiError("Runpod returned an invalid response") from exc
        if not isinstance(data, dict):
            raise RunpodApiError("Runpod returned an invalid response")
        return data

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict:
        data = await self._call(
            "POST",
            "https://api.runpod.io/graphql",
            body={"query": query, "variables": variables or {}},
            allow_graphql_errors=True,
        )
        if data.get("errors"):
            errors = data["errors"]
            codes = set()
            if isinstance(errors, list):
                for item in errors:
                    if isinstance(item, dict) and isinstance(item.get("extensions"), dict):
                        code = item["extensions"].get("code")
                        if isinstance(code, str):
                            codes.add(code)
            if (
                codes
                and codes <= {"GRAPHQL_VALIDATION_FAILED", "GRAPHQL_PARSE_FAILED"}
                and not data.get("data")
            ):
                raise RunpodApiError(
                    "Runpod rejected the app's setup request before starting a machine. "
                    "Check for an app update.",
                    400,
                    request_rejected=True,
                )
            if codes & {"UNAUTHENTICATED", "FORBIDDEN"}:
                raise RunpodApiError(
                    "Runpod denied this request. Check that your API key allows managing Pods, "
                    "then reconnect."
                )
            raise RunpodApiError(
                "Runpod could not complete the cloud request. Check Runpod for service "
                "or availability issues, then retry setup."
            )
        if not isinstance(data.get("data"), dict):
            raise RunpodApiError(
                "Runpod returned an incomplete cloud response; "
                "check the pending machine before retrying"
            )
        return data["data"]

    async def balance(self) -> dict[str, float | None]:
        try:
            account = (
                await self.graphql(
                    "query { myself { clientBalance minBalance currentSpendPerHr } }"
                )
            ).get("myself") or {}
        except RunpodApiError:
            account = {}
        result = {}
        for key, field in (
            ("balance_usd", "clientBalance"),
            ("minimum_balance_usd", "minBalance"),
            ("account_hourly_spend_usd", "currentSpendPerHr"),
        ):
            value = account.get(field)
            result[key] = (
                float(value)
                if isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                else None
            )
        return result

    async def list_data_centers(self) -> list[dict]:
        data = await self._call(
            "GET",
            "/catalog/datacenters",
            params={
                "include": "GPU_AVAILABILITY,CPU_AVAILABILITY",
                "networkVolumeTypes": "STANDARD",
            },
        )
        return data.get("dataCenters", [])

    async def list_cpu_types(self) -> list[dict]:
        return (
            await self._call(
                "GET",
                "/catalog/cpus",
                params={
                    "include": "AVAILABILITY",
                    "product": "POD",
                },
            )
        ).get("cpus", [])

    async def create_guarded_pod(
        self,
        *,
        name: str,
        image: str,
        data_center: str,
        volume_id: str,
        worker_token: str,
        terminate_at: str,
        gpu_id: str | None = None,
        cpu_instance_id: str | None = None,
        cache_home: str = "/workspace/hf-cache",
    ) -> dict:
        """Reject creation until independent automatic spending protection exists.

        Runpod accepts terminateAfter but does not enforce it or expose its
        value for readback. An accepted timestamp cannot guard a rental when
        this PC sleeps or goes offline. Keep this rejection before any HTTP
        creation request, for both GPU generation and CPU model installation.
        """
        if not re.fullmatch(r"[a-zA-Z0-9./_-]+@sha256:[0-9a-f]{64}", image):
            raise ValueError("Worker image must have an immutable digest")
        if cache_home not in {"/workspace/hf-cache", "/workspace/voice-clone/hf-cache"}:
            raise ValueError("Invalid model storage path")
        if not gpu_id and (
            not cpu_instance_id or not re.fullmatch(r"[A-Za-z0-9_-]+", cpu_instance_id)
        ):
            raise ValueError("CPU installer requires a quoted instance configuration")
        _reject_unprotected_pod_creation()

    async def terminate_pod(self, pod_id: str) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{6,40}", pod_id):
            raise ValueError("Invalid Pod ID")
        try:
            await self._call("DELETE", f"/pods/{pod_id}")
        except RunpodApiError as exc:
            if exc.status_code != 404:
                raise

    async def list_gpu_types(self) -> list[dict[str, Any]]:
        data = await self._call(
            "GET",
            "/catalog/gpus",
            params={
                "include": "AVAILABILITY",
                "product": "POD",
                "count": 1,
                "cloud": "SECURE",
            },
        )
        return data.get("gpus", [])

    async def list_pods(self) -> list[dict[str, Any]]:
        return (await self._call("GET", "/pods")).get("pods", [])

    async def list_volumes(self) -> list[dict[str, Any]]:
        return (await self._call("GET", "/network-volumes")).get("networkVolumes", [])

    async def get_pod(self, pod_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/pods/{pod_id}")

    async def account_billing(self, period: str) -> dict[str, Any]:
        """Read aggregate account billing, including terminated resources."""
        options = {"24h": ("hour", 24), "7d": ("day", 7), "30d": ("day", 30)}
        if period not in options:
            raise ValueError("Choose 24 hours, 7 days or 30 days.")
        bucket, count = options[period]
        try:
            async with asyncio.timeout(45):
                async with self._client.stream(
                    "GET", "/billing", params={"bucketSize": bucket, "lastN": count}
                ) as response:
                    if response.status_code != 200:
                        raise RunpodApiError(
                            f"Runpod returned HTTP {response.status_code}", response.status_code
                        )
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > 1024 * 1024:
                            raise RunpodApiError("Runpod returned an invalid billing response")
                        raw.extend(chunk)
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise ValueError
                    return data
        except (httpx.HTTPError, TimeoutError):
            raise RunpodApiError("Cannot reach Runpod billing") from None
        except (ValueError, UnicodeError):
            raise RunpodApiError("Runpod returned an invalid billing response") from None

    async def pod_billing(self, pod_id: str) -> dict[str, Any]:
        return await self._call(
            "GET",
            "/billing/pods",
            params={
                "podId": pod_id,
                "bucketSize": "hour",
                "lastN": 24,
            },
        )

    async def volume_billing(self, volume_id: str) -> dict[str, Any]:
        return await self._call(
            "GET",
            "/billing/network-volumes",
            params={
                "networkVolumeId": volume_id,
                "bucketSize": "hour",
                "lastN": 24,
            },
        )

    async def create_volume(self, *, name: str, data_center: str, size_gb: int) -> dict[str, Any]:
        if isinstance(size_gb, bool) or not isinstance(size_gb, int) or not 1 <= size_gb <= 4000:
            raise ValueError("Storage size must be between 1 and 4000 GB")
        return await self._call(
            "POST",
            "/network-volumes",
            body={
                "name": name,
                "dataCenter": data_center,
                "size": size_gb,
                "type": "STANDARD",
            },
        )

    async def create_pod(
        self,
        *,
        name: str,
        image: str,
        gpu_id: str,
        data_center: str,
        volume_id: str,
        worker_token: str,
    ) -> dict[str, Any]:
        if not image or "@sha256:" not in image:
            raise ValueError("Pod image must be pinned by digest")
        if not worker_token:
            raise ValueError("Worker token is required")
        _reject_unprotected_pod_creation()

    async def pod_action(self, pod_id: str, action: str) -> dict[str, Any]:
        if action not in {"start", "stop", "restart"}:
            raise ValueError("Unsupported Pod action")
        if action in {"start", "restart"}:
            _reject_unprotected_pod_creation()
        return await self._call("POST", f"/pods/{pod_id}/action", body={"action": action})
