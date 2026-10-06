"""Native session orchestration shared by desktop synthesis and text helpers.

Production construction accepts only source-reviewed deployment/qualification.
No environment or profile flag grants admission; this release has no approved
deployment. Test doubles exercise orchestration without claiming live proof.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .flex_jobs import FlexJobError, FlexJobs
from .native_admission import (
    NativeAdmissionError,
    NativeQualification,
    parse_catalog,
    parse_endpoint,
    parse_owned_cleanup,
    parse_workers,
    prepare_envelope,
)
from .native_ledger import NativeLedger
from .native_qualification import NativeQualificationContext, native_qualification_status
from .native_transport import NativeTransport


@dataclass(frozen=True)
class NativeDeployment:
    context: NativeQualificationContext
    minimum_vram_gb: int
    duration_seconds: int
    disk_reserve_usd: str


# Source review after real image/lifecycle qualification is required to populate.
# User settings, adjacent files and environment variables cannot populate this.
_REVIEWED_DEPLOYMENTS: tuple[NativeDeployment, ...] = ()


def deployment_for(binding, manifest_sha256, *, now):
    for deployment in _REVIEWED_DEPLOYMENTS:
        context = deployment.context
        if (context.volume_id, context.region, context.manifest_sha256) != (
            binding.volume_id,
            binding.region,
            manifest_sha256,
        ):
            continue
        result = native_qualification_status(context, now=now)
        if result.available and result.qualification is not None:
            return deployment, result.qualification
    raise NativeAdmissionError("Native Serverless image and provider lifecycle are not qualified")


async def production_backend(cloud):
    """Check compiled trust before keys, filesystem state or provider requests."""
    from ..remote_worker.model_install import release_manifest_id

    binding = cloud.storage_binding()
    deployment, qualification = deployment_for(binding, release_manifest_id(), now=time.time())
    # One Windows-user/account ledger outside application profiles. Cross-machine
    # admission remains unqualified; this is not a global account spending cap.
    base = os.environ.get("LOCALAPPDATA")
    if os.name != "nt" or not base:
        raise NativeAdmissionError("A protected shared Windows account ledger is required")
    transport = NativeTransport(cloud.key())
    try:
        account = await transport.account()
        ledger = NativeLedger(
            Path(base) / "AI Voice Clone Studio" / "cloud-ledgers" / (account + ".sqlite"), account
        )
        # No approval import or implicit $0.50 renewal. An absent audited ledger
        # allowance causes open_session to fail before any paid provider mutation.
        return NativeBackend(
            transport,
            ledger,
            qualification,
            deployment,
            root=cloud.settings.data_dir / "native-jobs",
            cache_prefix=binding.prefix,
        )
    except BaseException:
        await transport.close()
        raise


class NativeBackend:
    def __init__(
        self,
        transport,
        ledger,
        qualification: NativeQualification,
        deployment,
        *,
        root,
        cache_prefix,
        clock=time.time,
    ):
        self.transport, self.ledger, self.qualification = transport, ledger, qualification
        self.deployment, self.cache_prefix, self.clock = deployment, cache_prefix, clock
        self.lock = asyncio.Lock()
        self.stop_lock = asyncio.Lock()
        self.cleanup_tasks = set()
        self.session_id = None
        self.catalog_evidence = None
        self.jobs = FlexJobs(root, self, native_submitter=self)
        self.sweep_task = None

    async def _catalog(self):
        observed = self.clock()
        evidence = parse_catalog(
            await self.transport.catalog(), observed_at=observed, product_context="SERVERLESS"
        )
        self.catalog_evidence = evidence
        if self.session_id:
            self.ledger.refresh_catalog(
                self.session_id,
                evidence,
                now=self.clock(),
                minimum_vram_gb=self.deployment.minimum_vram_gb,
            )
        return evidence

    async def _readback(self):
        row = self.ledger.view(self.session_id)
        raw = await self.transport.endpoint(row["endpoint"])
        if raw is None:
            raise NativeAdmissionError("Endpoint is absent; generation cannot be admitted")
        cache_home = "/runpod-volume/" + self.cache_prefix.removesuffix("/hub/")
        env = raw.get("env")
        if (
            raw.get("args") != ""
            or raw.get("ports") != []
            or type(raw.get("disk")) is not int
            or raw["disk"] != 20
            or raw.get("scaling") != {"type": "QUEUE_DELAY", "queueDelay": 4}
            or not isinstance(env, dict)
            or env.get("HF_HOME") != cache_home
            or env.get("HF_HUB_CACHE") != cache_home + "/hub"
        ):
            raise NativeAdmissionError("Worker command, cache mount or disk configuration changed")
        workers = await self.transport.workers(row["endpoint"])
        if not isinstance(workers, dict):
            raise NativeAdmissionError("Worker evidence is unavailable")
        # GET endpoint need not return a version. Use the actual worker response
        # version, requiring identical endpoint configuration around that read.
        later = await self.transport.endpoint(row["endpoint"])
        if later != raw or (
            raw.get("version") is not None and raw["version"] != workers.get("endpointVersion")
        ):
            raise NativeAdmissionError("Endpoint changed during worker inspection")
        normalized = {**raw, "version": workers.get("endpointVersion")}
        now = self.clock()
        endpoint = parse_endpoint(
            normalized,
            session_id=self.session_id,
            qualification=self.qualification,
            observed_at=now,
            now=now,
        )
        evidence = parse_workers(
            workers,
            endpoint=endpoint,
            catalog=self.catalog_evidence,
            qualification=self.qualification,
            observed_at=now,
            now=now,
        )
        self.ledger.observe_workers(self.session_id, evidence, now=now)
        return endpoint

    async def start(self):
        async with self.lock:
            self.qualification.check(self.clock())
            if self.session_id is not None:
                if self.ledger.view(self.session_id)["state"] != "active":
                    self._ensure_sweep()
                    raise NativeAdmissionError("Native session requires cleanup and billing review")
                if self.session_id in self.ledger.due_sessions(now=self.clock()):
                    await self.stop()
                    raise NativeAdmissionError(
                        "The warm session ended; cleanup and billing review are required"
                    )
                return self.ledger.view(self.session_id)["endpoint"]
            prior = self.ledger.unresolved_sessions()
            if prior:
                # Restarts recover only exact saved resources. They never create
                # a replacement or silently re-admit unfinished inference.
                self.session_id = prior[0]["id"]
                self._ensure_sweep()
                if len(prior) != 1 or prior[0]["state"] != "active":
                    raise NativeAdmissionError("Previous native session needs owned cleanup")
                if self.ledger._qualification(prior[0]) != self.qualification:
                    raise NativeAdmissionError("Previous session qualification changed")
                await self._catalog()
                await self._readback()
            else:
                catalog = await self._catalog()
                sid = str(uuid4())
                self.ledger.open_session(
                    sid,
                    qualification=self.qualification,
                    catalog=catalog,
                    now=self.clock(),
                    duration_seconds=self.deployment.duration_seconds,
                    minimum_vram_gb=self.deployment.minimum_vram_gb,
                    disk_reserve_usd=self.deployment.disk_reserve_usd,
                )
                self.session_id = sid
                try:
                    raw = await self.transport.create_claimed(
                        self.ledger, sid, now=self.clock(), cache_prefix=self.cache_prefix
                    )
                    now = self.clock()
                    proof = parse_owned_cleanup(
                        raw,
                        session_id=sid,
                        expected_image=self.qualification.image,
                        expected_volume=self.qualification.volume_id,
                        observed_at=now,
                        now=now,
                    )
                    self.ledger.record_creation(sid, proof, now=now)
                    endpoint = await self._readback()
                    self.ledger.bind_endpoint(sid, endpoint, now=self.clock())
                except BaseException:
                    self.ledger.request_stop(sid)
                    # Never hide the durable fence if cleanup fails.
                    with contextlib.suppress(NativeAdmissionError, OSError):
                        await self.stop()
                    raise
            self._ensure_sweep()
            return self.ledger.view(self.session_id)["endpoint"]

    def _ensure_sweep(self):
        if self.sweep_task is None or self.sweep_task.done():
            self.sweep_task = asyncio.create_task(self._sweep())

    async def recover_submitted(self, endpoint_id, value):
        """Repair the ledger-to-file crash window without a provider mutation."""
        try:
            operation = self.ledger.operation_view(str(value.operation_id))
        except NativeAdmissionError:
            return None
        row = self.ledger.view(operation["session"])
        digest = hashlib.sha256(
            json.dumps(
                value.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        if row["endpoint"] != endpoint_id or digest != operation["hash"]:
            raise FlexJobError("Saved operation differs from its native reservation")
        return operation["job"]

    async def recover_operation(self, value):
        path = self.jobs._path(value.operation_id)
        if not path.exists():
            return None
        saved = self.jobs._read(value.operation_id)
        endpoint_id = saved["endpoint_id"]
        await self.jobs.submit_once(endpoint_id, value)  # existing intent only; cannot POST again
        operation = self.ledger.operation_view(str(value.operation_id))
        if self.session_id not in {None, operation["session"]}:
            raise NativeAdmissionError("Recovered operation belongs to another native session")
        self.session_id = operation["session"]
        if self.ledger.view(self.session_id)["state"] != "stopped":
            self._ensure_sweep()
        return endpoint_id

    def request_cancel(self, operation_id):
        task = asyncio.create_task(self.cancel_operation(operation_id))
        self.cleanup_tasks.add(task)

        # Retrieve errors to prevent unobserved-task logs; the persistent fence
        # is the recovery source, never a discarded coroutine exception.
        def finished(done):
            self.cleanup_tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        return task

    async def submit_once(self, endpoint_id, value):
        async with self.lock:
            row = self.ledger.view(self.session_id)
            if row["endpoint"] != endpoint_id:
                raise NativeAdmissionError("Operation endpoint changed")
            try:
                await self._catalog()
                await self._readback()
                now = self.clock()
                seconds = self.qualification.execution_seconds
                body, digest = prepare_envelope(
                    value, now=now, deadline=row["deadline"], execution_seconds=seconds
                )
                self.ledger.reserve_intent(
                    self.session_id,
                    str(value.operation_id),
                    digest,
                    execution_seconds=seconds,
                    queue_seconds=0,
                    now=now,
                )
                return await self.transport.submit_claimed(
                    self.ledger, str(value.operation_id), endpoint_id, body, now=now
                )
            except BaseException:
                self.ledger.request_stop(self.session_id)
                with contextlib.suppress(NativeAdmissionError, OSError):
                    await self.stop()
                raise

    async def status(self, endpoint_id, job_id):
        # FlexJobs verifies result/identity; polling cannot start a new worker.
        return await self.transport.status(endpoint_id, job_id)

    async def cancel(self, endpoint_id, job_id):
        return await self.transport.cancel(endpoint_id, job_id)

    async def poll_once(self, operation_id):
        view = await self.jobs.poll_once(operation_id)
        self.ledger.observe_job(
            str(operation_id), job_id=view.provider_job_id, status=view.status, now=self.clock()
        )
        # Refresh real counts during paid startup/inference; excessive epochs
        # request owned cleanup rather than silently renting replacements.
        if view.status in {"IN_QUEUE", "IN_PROGRESS"}:
            try:
                await self._catalog()
                await self._readback()
            except NativeAdmissionError:
                self.ledger.request_stop(self.session_id)
                with contextlib.suppress(NativeAdmissionError):
                    await self.stop()
                raise
        return view

    async def cancel_operation(self, operation_id):
        try:
            view = await self.jobs.cancel(operation_id)
            self.ledger.observe_job(
                str(operation_id), job_id=view.provider_job_id, status=view.status, now=self.clock()
            )
            return view
        finally:
            self.ledger.request_stop(self.session_id)
            await self.stop()

    async def stop(self):
        async with self.stop_lock:
            await self._stop()

    async def _stop(self):
        if self.session_id is None:
            return
        row = self.ledger.view(self.session_id)
        if row["state"] == "stopped":
            return
        self.ledger.request_stop(self.session_id)
        if not row["endpoint"]:
            candidates = await self.transport.owned_candidates(self.session_id)
            if len(candidates) != 1:
                raise NativeAdmissionError(
                    "Uncertain creation remains fenced; exact cleanup identity is required"
                )
            now = self.clock()
            proof = parse_owned_cleanup(
                candidates[0],
                session_id=self.session_id,
                expected_image=self.qualification.image,
                expected_volume=self.qualification.volume_id,
                observed_at=now,
                now=now,
            )
            self.ledger.record_cleanup_target(self.session_id, proof, now=now)
            row = self.ledger.view(self.session_id)
        await self.transport.delete_owned(self.ledger, self.session_id, now=self.clock())
        raw = await self.transport.endpoint(row["endpoint"])
        workers = await self.transport.workers(row["endpoint"])
        counts = {"running", "idle", "initializing", "throttled", "unhealthy", "total"}
        absent = (
            isinstance(workers, dict)
            and workers.get("workers") == []
            and isinstance(workers.get("summary"), dict)
            and all(
                type(workers["summary"].get(key)) is int and workers["summary"][key] == 0
                for key in counts
            )
        )
        self.ledger.observe_absence(
            self.session_id,
            endpoint_id=row["endpoint"],
            endpoint_absent=raw is None,
            workers_absent=absent,
            now=self.clock(),
        )
        # No credit or settlement from an empty history. Actual aligned records
        # are upserted; finality still requires the separate reviewed audit.
        start = datetime.fromtimestamp(row["created"] // 3600 * 3600, UTC).isoformat()
        end = datetime.fromtimestamp((int(self.clock()) // 3600 + 1) * 3600, UTC).isoformat()
        records = await self.transport.billing(row["endpoint"], start, end)
        self.ledger.apply_billing(self.session_id, records)

    async def _sweep(self):
        # Local convenience only. PC-off safety comes from qualified provider
        # lifecycle; never claim this asyncio task is independent protection.
        while True:
            await asyncio.sleep(2)
            async with self.lock:
                if self.session_id in self.ledger.due_sessions(now=self.clock()):
                    with contextlib.suppress(NativeAdmissionError, OSError):
                        await self.stop()
                    if self.ledger.view(self.session_id)["state"] == "stopped":
                        return

    async def close(self):
        if self.cleanup_tasks:
            tasks = tuple(self.cleanup_tasks)
            try:
                async with asyncio.timeout(20):
                    await asyncio.gather(*tasks, return_exceptions=True)
            except TimeoutError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        if self.sweep_task:
            self.sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.sweep_task
        try:
            async with self.lock:
                await self.stop()
        finally:
            await self.transport.close()
