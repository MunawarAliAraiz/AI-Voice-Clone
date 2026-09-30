"""Runpod REST v2 client. Tokens never enter browser storage or log output."""

from __future__ import annotations

from typing import Any

import httpx

API_BASE = "https://api.runpod.io/v2"


class RunpodApiError(RuntimeError):
    pass


class RunpodClient:
    def __init__(
        self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
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
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, params=params, json=body)
        except httpx.HTTPError as exc:
            raise RunpodApiError("Cannot reach Runpod") from exc
        if response.is_error:
            # Runpod responses may include account details. Keep the public
            # error stable and do not echo arbitrary provider bodies or keys.
            raise RunpodApiError(f"Runpod returned HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise RunpodApiError("Runpod returned an invalid response")
        return data

    async def list_gpu_types(self) -> list[dict[str, Any]]:
        data = await self._call("GET", "/catalog/gpus", params={
            "include": "AVAILABILITY", "product": "POD", "count": 1,
            "cloud": "SECURE",
        })
        return data.get("gpus", [])

    async def list_pods(self) -> list[dict[str, Any]]:
        return (await self._call("GET", "/pods")).get("pods", [])

    async def list_volumes(self) -> list[dict[str, Any]]:
        return (await self._call("GET", "/network-volumes")).get("networkVolumes", [])

    async def get_pod(self, pod_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/pods/{pod_id}")

    async def pod_billing(self, pod_id: str) -> dict[str, Any]:
        return await self._call("GET", "/billing/pods", params={
            "podId": pod_id, "bucketSize": "hour", "lastN": 24,
        })

    async def volume_billing(self, volume_id: str) -> dict[str, Any]:
        return await self._call("GET", "/billing/network-volumes", params={
            "networkVolumeId": volume_id, "bucketSize": "hour", "lastN": 24,
        })

    async def create_volume(
        self, *, name: str, data_center: str, size_gb: int
    ) -> dict[str, Any]:
        if size_gb < 150:
            raise ValueError("All-model volume must be at least 150 GB")
        return await self._call("POST", "/network-volumes", body={
            "name": name, "dataCenter": data_center, "size": size_gb,
            "type": "STANDARD",
        })

    async def create_pod(
        self, *, name: str, image: str, gpu_id: str,
        data_center: str, volume_id: str, worker_token: str,
    ) -> dict[str, Any]:
        if not image or "@sha256:" not in image:
            raise ValueError("Pod image must be pinned by digest")
        if not worker_token:
            raise ValueError("Worker token is required")
        return await self._call("POST", "/pods", body={
            "name": name,
            "image": image,
            "gpu": {"id": gpu_id, "count": 1},
            "cloud": "SECURE",
            "dataCenterIds": [data_center],
            "disk": 30,
            "mounts": {"network": [{"volumeId": volume_id, "path": "/workspace"}]},
            "ports": ["8000/http"],
            "env": {"POD_WORKER_TOKEN": worker_token, "HF_HOME": "/workspace/hf-cache",
                    "VCS_DATA_DIR": "/tmp/vcs-worker"},  # noqa: S108 - ephemeral Pod data, no weights
        })

    async def pod_action(self, pod_id: str, action: str) -> dict[str, Any]:
        if action not in {"start", "stop", "restart"}:
            raise ValueError("Unsupported Pod action")
        return await self._call("POST", f"/pods/{pod_id}/action", body={"action": action})
