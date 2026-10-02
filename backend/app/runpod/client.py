"""Runpod REST v2 client. Tokens never enter browser storage or log output."""

from __future__ import annotations

import math
import re
from typing import Any

import httpx

API_BASE = "https://api.runpod.io/v2"


class RunpodApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None,
                 *, request_rejected: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_rejected = request_rejected


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
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, params=params, json=body)
        except httpx.HTTPError as exc:
            raise RunpodApiError("Cannot reach Runpod") from exc
        if response.is_error:
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
            if (codes and codes <= {"GRAPHQL_VALIDATION_FAILED", "GRAPHQL_PARSE_FAILED"}
                    and not data.get("data")):
                raise RunpodApiError(
                    "Runpod rejected the app's setup request before starting a machine. "
                    "Check for an app update.",
                    400, request_rejected=True)
            if codes & {"UNAUTHENTICATED", "FORBIDDEN"}:
                raise RunpodApiError(
                    "Runpod denied this request. Check that your API key allows managing Pods, "
                    "then reconnect.")
            raise RunpodApiError(
                "Runpod could not complete the cloud request. Check Runpod for service "
                "or availability issues, then retry setup.")
        if not isinstance(data.get("data"), dict):
            raise RunpodApiError("Runpod returned an incomplete cloud response; "
                                 "check the pending machine before retrying")
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
                float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None
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
    ) -> dict:
        """GraphQL creation atomically includes the provider termination deadline.

        REST v2's current creation schema has no expiry field. Never create an
        unguarded Pod and depend on this PC being online to stop its billing.
        """
        if not re.fullmatch(r"[a-zA-Z0-9./_-]+@sha256:[0-9a-f]{64}", image):
            raise ValueError("Worker image must have an immutable digest")
        body = {
            "name": name,
            "imageName": image,
            "cloudType": "SECURE",
            "computeType": "GPU" if gpu_id else "CPU",
            "gpuCount": 1 if gpu_id else 0,
            "containerDiskInGb": 30 if gpu_id else 10,
            "networkVolumeId": volume_id,
            "volumeMountPath": "/workspace",
            "dataCenterId": data_center,
            "ports": "8000/http",
            "terminateAfter": terminate_at,
            "startSsh": False,
            "startJupyter": False,
            "env": [
                {"key": "POD_WORKER_TOKEN", "value": worker_token},
                {"key": "HF_HOME", "value": "/workspace/hf-cache"},
                {"key": "VCS_DATA_DIR", "value": "/tmp/vcs-worker"},  # noqa: S108 -- ephemeral container data
            ],
        }
        if gpu_id:
            body.update(gpuTypeId=gpu_id, minCudaVersion="12.8")
        else:
            body.update(minVcpuCount=2, minMemoryInGb=4)
        data = await self.graphql(
            "mutation($input: PodFindAndDeployOnDemandInput!) { "
            "podFindAndDeployOnDemand(input: $input) { id costPerHr } }",
            {"input": body},
        )
        pod = data.get("podFindAndDeployOnDemand")
        if not isinstance(pod, dict) or not pod.get("id"):
            raise RunpodApiError("Runpod did not return a Pod ID; reconcile before retrying")
        return pod

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
        if size_gb < 150:
            raise ValueError("All-model volume must be at least 150 GB")
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
        return await self._call(
            "POST",
            "/pods",
            body={
                "name": name,
                "image": image,
                "gpu": {"id": gpu_id, "count": 1},
                "cloud": "SECURE",
                "dataCenterIds": [data_center],
                "disk": 30,
                "mounts": {"network": [{"volumeId": volume_id, "path": "/workspace"}]},
                "ports": ["8000/http"],
                "env": {
                    "POD_WORKER_TOKEN": worker_token,
                    "HF_HOME": "/workspace/hf-cache",
                    "VCS_DATA_DIR": "/tmp/vcs-worker",  # noqa: S108 -- ephemeral container data
                },
            },
        )

    async def pod_action(self, pod_id: str, action: str) -> dict[str, Any]:
        if action not in {"start", "stop", "restart"}:
            raise ValueError("Unsupported Pod action")
        return await self._call("POST", f"/pods/{pod_id}/action", body={"action": action})
