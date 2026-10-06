"""Authenticated native Serverless transport; no retries of paid mutations.

REST v2 management and runtime v2 are deliberately separate. A transport claim
must already be durable before creation/submission. Unknown responses preserve
the ledger fence. This transport is not a provider-enforced spending cap.
"""

from __future__ import annotations

import httpx

from .flex_jobs import FlexJobError
from .native_admission import NativeAdmissionError, identity, uuid_text

MANAGEMENT = "https://api.runpod.io/v2/"
RUNTIME = "https://api.runpod.ai/v2/"


class NativeTransportError(NativeAdmissionError):
    pass


class NativeTransport:
    def __init__(self, key: str, *, transport=None):
        if not key:
            raise NativeTransportError("Connect the Runpod account first")
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
            follow_redirects=False,
            transport=transport,
        )

    async def _call(self, base, method, path, *, body=None, params=None, absent=False):
        try:
            response = await self.client.request(method, base + path, json=body, params=params)
        except httpx.HTTPError as exc:
            raise NativeTransportError(
                "Runpod request is unconfirmed; no work was replayed"
            ) from exc
        if response.status_code == 404 and absent:
            return None
        if response.status_code not in {200, 201, 202, 204}:
            raise NativeTransportError("Runpod request is unconfirmed; reconcile or stop")
        if response.status_code == 204:
            return {}
        try:
            value = response.json()
        except ValueError as exc:
            raise NativeTransportError("Runpod returned invalid evidence") from exc
        if not isinstance(value, dict):
            raise NativeTransportError("Runpod returned invalid evidence")
        return value

    async def account(self):
        value = await self._call(
            "https://api.runpod.io/",
            "POST",
            "graphql",
            body={"query": "query { myself { id } }", "variables": {}},
        )
        if value.get("errors"):
            raise NativeTransportError("Runpod account identity is unconfirmed")
        try:
            return identity(value["data"]["myself"]["id"])
        except (KeyError, TypeError) as exc:
            raise NativeTransportError("Runpod account identity is unconfirmed") from exc

    async def catalog(self):
        return await self._call(
            MANAGEMENT,
            "GET",
            "catalog/gpus",
            params={
                "product": "SERVERLESS",
                "include": "AVAILABILITY",
            },
        )

    async def create_claimed(self, ledger, session_id, *, now, cache_prefix):
        row = ledger.view(session_id)
        q = ledger._qualification(row)
        q.check(now)
        if cache_prefix not in {"hf-cache/hub/", "voice-clone/hf-cache/hub/"}:
            raise NativeTransportError("Model cache mount is unqualified")
        if not ledger.claim_create(session_id, now=now):
            raise NativeTransportError("Endpoint creation was already claimed; reconcile it")
        body = {
            "name": "vcs-native-" + uuid_text(session_id),
            "image": q.image,
            "type": "QUEUE",
            "disk": 20,
            "ports": [],
            "args": "",
            "env": {
                "VCS_NATIVE_SESSION_ID": session_id,
                "VCS_NATIVE_DEDICATED": "true",
                "HF_HOME": "/runpod-volume/" + cache_prefix.removesuffix("/hub/"),
                "HF_HUB_CACHE": "/runpod-volume/" + cache_prefix.rstrip("/"),
            },
            "gpu": {"pools": [q.pool], "count": 1, "allowedCudaVersions": []},
            "workers": {"min": 0, "max": 1, "idleTimeout": 60},
            "scaling": {"type": "QUEUE_DELAY", "queueDelay": 4},
            "networkVolumes": [q.volume_id],
            "dataCenterIds": [q.region],
            "timeout": q.execution_seconds * 1000,
            "flashboot": "OFF",
        }
        return await self._call(MANAGEMENT, "POST", "serverless", body=body)

    async def endpoint(self, endpoint_id):
        value = await self._call(
            MANAGEMENT, "GET", "serverless/" + identity(endpoint_id), absent=True
        )
        if value is not None and value.get("id") != endpoint_id:
            raise NativeTransportError("Endpoint readback belongs to another resource")
        return value

    async def owned_candidates(self, session_id):
        name = "vcs-native-" + uuid_text(session_id)
        matches, seen, cursor = [], set(), None
        for _ in range(100):
            params = {"limit": 1000}
            if cursor is not None:
                params["cursor"] = cursor
            value = await self._call(MANAGEMENT, "GET", "serverless", params=params)
            rows, page = value.get("endpoints"), value.get("pagination")
            if not isinstance(rows, list) or not isinstance(page, dict):
                raise NativeTransportError("Endpoint listing is incomplete")
            if any(not isinstance(row, dict) for row in rows):
                raise NativeTransportError("Endpoint listing is incomplete")
            matches.extend(row for row in rows if row.get("name") == name)
            if page.get("hasNextPage") is False:
                return matches
            cursor = page.get("nextCursor")
            if (
                page.get("hasNextPage") is not True
                or not isinstance(cursor, str)
                or not cursor
                or len(cursor) > 4096
                or cursor in seen
            ):
                raise NativeTransportError("Endpoint listing is incomplete")
            seen.add(cursor)
        raise NativeTransportError("Endpoint listing exceeded the recovery bound")

    async def workers(self, endpoint_id):
        # A workers 404 is unavailable evidence, never an empty worker list.
        return await self._call(
            MANAGEMENT, "GET", "serverless/" + identity(endpoint_id) + "/workers", absent=True
        )

    async def submit_claimed(self, ledger, operation_id, endpoint_id, body, *, now):
        operation = ledger.operation_view(operation_id)
        if ledger.view(operation["session"])["endpoint"] != identity(endpoint_id):
            raise NativeTransportError("Submission endpoint changed")
        from .flex_protocol import FlexInput
        from .native_admission import prepare_envelope

        row = ledger.view(operation["session"])
        checked, digest = prepare_envelope(
            FlexInput.model_validate(body.get("input")),
            now=now,
            deadline=row["deadline"],
            execution_seconds=operation["execution"],
        )
        if (
            checked != body
            or digest != operation["hash"]
            or str(checked["input"]["operation_id"]) != operation_id
        ):
            raise NativeTransportError("Submission differs from its reservation")
        if not ledger.claim_submit(operation_id, now=now):
            raise NativeTransportError("Submission was already claimed; no duplicate was sent")
        value = await self._call(RUNTIME, "POST", identity(endpoint_id) + "/run", body=body)
        job_id = identity(value.get("id"))
        ledger.record_job(operation_id, job_id)
        return job_id

    async def status(self, endpoint_id, job_id):
        value = await self._call(
            RUNTIME, "GET", identity(endpoint_id) + "/status/" + identity(job_id), absent=True
        )
        if value is None:
            raise FlexJobError("Cloud job result is unavailable. It may have expired.")
        return value

    async def cancel(self, endpoint_id, job_id):
        return await self._call(
            RUNTIME, "POST", identity(endpoint_id) + "/cancel/" + identity(job_id)
        )

    async def delete_owned(self, ledger, session_id, *, now):
        from .native_admission import parse_owned_cleanup

        row = ledger.view(session_id)
        endpoint_id = row["endpoint"]
        if not endpoint_id:
            raise NativeTransportError("Unknown endpoint needs owned reconciliation")
        raw = await self.endpoint(endpoint_id)
        if raw is not None:
            q = ledger._qualification(row)
            proof = parse_owned_cleanup(
                raw,
                session_id=session_id,
                expected_image=q.image,
                expected_volume=q.volume_id,
                observed_at=now,
                now=now,
            )
            ledger.record_cleanup_target(session_id, proof, now=now)
        ledger.request_stop(session_id)
        return await self._call(
            MANAGEMENT, "DELETE", "serverless/" + identity(endpoint_id), absent=True
        )

    async def billing(self, endpoint_id, start, end):
        value = await self._call(
            MANAGEMENT,
            "GET",
            "billing/serverless",
            params={
                "serverlessId": identity(endpoint_id),
                "bucketSize": "hour",
                "startTime": start,
                "endTime": end,
            },
        )
        records = value.get("records")
        query = (value.get("metadata") or {}).get("query")
        if (
            not isinstance(records, list)
            or not isinstance(query, dict)
            or query.get("serverlessId") != endpoint_id
            or query.get("bucketSize") != "hour"
            or query.get("startTime") != start
            or query.get("endTime") != end
        ):
            raise NativeTransportError("Billing scope is unconfirmed")
        return records

    async def close(self):
        await self.client.aclose()
