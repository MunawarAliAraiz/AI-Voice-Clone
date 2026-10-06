"""Offline native Serverless evidence and request preparation; no HTTP transport."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from typing import Any
from uuid import UUID

from .flex_protocol import FlexInput

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_IMAGE = re.compile(r"ghcr\.io/[a-z0-9/_.-]+@sha256:[a-f0-9]{64}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")


class NativeAdmissionError(ValueError):
    """Fixed explanation; never includes input, credentials or provider bodies."""


def identity(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise NativeAdmissionError("Invalid cloud identity")
    return value


def uuid_text(value: Any) -> str:
    try:
        result = UUID(str(value))
        if result.version not in range(1, 9):
            raise ValueError
        return str(result)
    except (ValueError, TypeError, AttributeError) as exc:
        raise NativeAdmissionError("Invalid operation identity") from exc


def integer(value: Any, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise NativeAdmissionError("Invalid bounded number")
    return value


def money_micro(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise NativeAdmissionError("Invalid money amount")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > 1000:
            raise ValueError
        return int((amount * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    except (ValueError, ArithmeticError) as exc:
        raise NativeAdmissionError("Invalid money amount") from exc


def timestamp(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NativeAdmissionError("Invalid observation time")
    if not math.isfinite(value) or value < 0:
        raise NativeAdmissionError("Invalid observation time")
    return float(value)


def fresh(observed: float, now: float, max_age: int) -> None:
    timestamp(now)
    timestamp(observed)
    if observed > now or now - observed > max_age:
        raise NativeAdmissionError("Cloud evidence is stale")


@dataclass(frozen=True)
class NativeQualification:
    """Trusted reviewed receipt, not request fields or an environment unlock.

    Instances are useful offline; production must load approved hash-bound evidence.
    Bounds are measured assumptions, never a provider-guaranteed dollar cap.
    """

    image: str
    volume_id: str
    region: str
    pool: str
    receipt_sha256: str
    expires_at: float
    effective_workers: int
    startup_seconds: int
    restart_attempts: int
    execution_seconds: int
    stop_reserve_seconds: int
    billing_lag_seconds: int
    max_session_seconds: int
    native_execution_verified: bool
    native_idle_verified: bool
    startup_retry_stop_verified: bool

    def check(self, now: float) -> None:
        if (
            not isinstance(self.image, str)
            or not _IMAGE.fullmatch(self.image)
            or not isinstance(self.receipt_sha256, str)
            or not _SHA.fullmatch(self.receipt_sha256)
        ):
            raise NativeAdmissionError("Qualified image evidence is required")
        for value in (self.volume_id, self.region, self.pool):
            identity(value)
        if timestamp(self.expires_at) <= timestamp(now):
            raise NativeAdmissionError("Native qualification expired")
        integer(self.effective_workers, 1, 16)
        integer(self.startup_seconds, 1, 600)
        integer(self.restart_attempts, 0, 4)
        integer(self.execution_seconds, 5, 600)
        integer(self.stop_reserve_seconds, 30, 300)
        integer(self.billing_lag_seconds, 1, 86400)
        integer(self.max_session_seconds, 120, 3600)
        if any(
            value is not True
            for value in (
                self.native_execution_verified,
                self.native_idle_verified,
                self.startup_retry_stop_verified,
            )
        ):
            raise NativeAdmissionError("Native lifecycle qualification is incomplete")


@dataclass(frozen=True)
class GpuRate:
    gpu_id: str
    pool: str
    vram_gb: int
    rate_micro_per_second: int
    regions: tuple[str, ...]


@dataclass(frozen=True)
class CatalogEvidence:
    observed_at: float
    gpus: tuple[GpuRate, ...]

    def ceiling(
        self, qualification: NativeQualification, *, now: float, minimum_vram_gb: int
    ) -> int:
        qualification.check(now)
        fresh(self.observed_at, now, 300)
        integer(minimum_vram_gb, 1, 280)
        members = [gpu for gpu in self.gpus if gpu.pool == qualification.pool]
        for gpu in members:
            integer(gpu.vram_gb, 1, 280)
            integer(gpu.rate_micro_per_second, 1, 1_000_000)
        if not members or any(gpu.vram_gb < minimum_vram_gb for gpu in members):
            raise NativeAdmissionError("GPU pool compatibility is unconfirmed")
        if not any(qualification.region in gpu.regions for gpu in members):
            raise NativeAdmissionError("No compatible cloud availability")
        # Include all pool members: availability and fallback can change after reads.
        return max(gpu.rate_micro_per_second for gpu in members)


def parse_catalog(raw: Any, *, observed_at: float, product_context: str) -> CatalogEvidence:
    if product_context != "SERVERLESS" or not isinstance(raw, dict):
        raise NativeAdmissionError("Serverless catalog evidence is required")
    items = raw.get("gpus")
    if not isinstance(items, list) or not items or len(items) > 1000:
        raise NativeAdmissionError("Invalid GPU catalog")
    result, seen = [], set()
    for row in items:
        if not isinstance(row, dict):
            raise NativeAdmissionError("Invalid GPU catalog")
        gpu_id = row.get("id")
        if not isinstance(gpu_id, str) or not gpu_id.startswith("NVIDIA "):
            raise NativeAdmissionError("Unsupported GPU catalog entry")
        if len(gpu_id) > 128 or gpu_id in seen:
            raise NativeAdmissionError("Duplicate GPU catalog identity")
        seen.add(gpu_id)
        pool = identity(row.get("pool"))
        memory = integer(row.get("memory"), 1, 280)
        price = row.get("price")
        if not isinstance(price, dict) or "serverless" not in price:
            raise NativeAdmissionError("Serverless GPU rate is unavailable")
        hourly = money_micro(price["serverless"])
        if hourly <= 0:
            raise NativeAdmissionError("Serverless GPU rate is unavailable")
        centers = row.get("dataCenters")
        if not isinstance(centers, list):
            raise NativeAdmissionError("Serverless availability is unavailable")
        regions = []
        for center in centers:
            if not isinstance(center, dict) or center.get("availability") not in {
                "HIGH",
                "MEDIUM",
                "LOW",
                "NONE",
            }:
                raise NativeAdmissionError("Invalid serverless availability")
            region = identity(center.get("id"))
            if region in regions:
                raise NativeAdmissionError("Duplicate cloud region")
            if center["availability"] != "NONE":
                regions.append(region)
        result.append(GpuRate(gpu_id, pool, memory, (hourly + 3599) // 3600, tuple(regions)))
    return CatalogEvidence(timestamp(observed_at), tuple(result))


@dataclass(frozen=True)
class EndpointEvidence:
    endpoint_id: str
    session_id: str
    image: str
    volume_id: str
    region: str
    pool: str
    version: int
    observed_at: float


def parse_endpoint(
    raw: Any, *, session_id: str, qualification: NativeQualification, observed_at: float, now: float
) -> EndpointEvidence:
    qualification.check(now)
    fresh(observed_at, now, 30)
    session_id = uuid_text(session_id)
    if not isinstance(raw, dict):
        raise NativeAdmissionError("Invalid endpoint evidence")
    expected = {
        "type": "QUEUE",
        "image": qualification.image,
        "networkVolumes": [qualification.volume_id],
        "dataCenterIds": [qualification.region],
        "flashboot": "OFF",
        "name": "vcs-native-" + session_id,
    }
    if any(raw.get(key) != value for key, value in expected.items()):
        raise NativeAdmissionError("Endpoint ownership or configuration changed")
    env, gpu, workers = raw.get("env"), raw.get("gpu"), raw.get("workers")
    if (
        not isinstance(env, dict)
        or env.get("VCS_NATIVE_SESSION_ID") != session_id
        or env.get("VCS_NATIVE_DEDICATED") != "true"
    ):
        raise NativeAdmissionError("Dedicated endpoint ownership is unconfirmed")
    if (
        not isinstance(gpu, dict)
        or gpu.get("pools") != [qualification.pool]
        or type(gpu.get("count")) is not int
        or gpu["count"] != 1
    ):
        raise NativeAdmissionError("Endpoint GPU configuration changed")
    if not isinstance(workers, dict) or any(
        type(workers.get(key)) is not int or workers[key] != value
        for key, value in {"min": 0, "max": 1, "idleTimeout": 60}.items()
    ):
        raise NativeAdmissionError("Endpoint worker controls changed")
    if (
        type(raw.get("timeout")) is not int
        or raw["timeout"] != qualification.execution_seconds * 1000
    ):
        raise NativeAdmissionError("Endpoint execution limit changed")
    return EndpointEvidence(
        identity(raw.get("id")),
        session_id,
        qualification.image,
        qualification.volume_id,
        qualification.region,
        qualification.pool,
        integer(raw.get("version"), 1, 2**31),
        timestamp(observed_at),
    )


@dataclass(frozen=True)
class WorkerEvidence:
    endpoint_id: str
    observed_at: float
    workers: tuple[tuple[str, str], ...]  # worker ID and GPU ID, no payload


def parse_workers(
    raw: Any,
    *,
    endpoint: EndpointEvidence,
    catalog: CatalogEvidence,
    qualification: NativeQualification,
    observed_at: float,
    now: float,
) -> WorkerEvidence:
    qualification.check(now)
    fresh(observed_at, now, 30)
    fresh(endpoint.observed_at, now, 30)
    fresh(catalog.observed_at, now, 300)
    if (
        not isinstance(raw, dict)
        or type(raw.get("endpointVersion")) is not int
        or raw["endpointVersion"] != endpoint.version
    ):
        raise NativeAdmissionError("Worker configuration version is unconfirmed")
    rows, summary = raw.get("workers"), raw.get("summary")
    if (
        not isinstance(rows, list)
        or not isinstance(summary, dict)
        or len(rows) > qualification.effective_workers
    ):
        raise NativeAdmissionError("Effective worker count exceeded qualification")
    counts = dict.fromkeys(("running", "idle", "initializing", "throttled", "unhealthy"), 0)
    result, seen = [], set()
    rates = {gpu.gpu_id: gpu for gpu in catalog.gpus}
    for row in rows:
        if not isinstance(row, dict):
            raise NativeAdmissionError("Invalid worker evidence")
        worker_id = identity(row.get("id"))
        state = row.get("status")
        if not isinstance(state, str) or state.lower() not in counts or worker_id in seen:
            raise NativeAdmissionError("Invalid worker state or identity")
        seen.add(worker_id)
        counts[state.lower()] += 1
        gpu_type = row.get("gpuTypeId")
        if not isinstance(gpu_type, str):
            raise NativeAdmissionError("Invalid actual GPU identity")
        gpu = rates.get(gpu_type)
        if gpu is None or gpu.pool != endpoint.pool or row.get("dataCenterId") != endpoint.region:
            raise NativeAdmissionError("Actual GPU rate or location is unconfirmed")
        if (
            row.get("isStale") is not False
            or type(row.get("version")) is not int
            or row["version"] != endpoint.version
            or row.get("image") != endpoint.image
            or type(row.get("gpuCount")) is not int
            or row["gpuCount"] != 1
        ):
            raise NativeAdmissionError("Actual worker configuration changed")
        timestamp(row.get("uptimeSeconds"))
        result.append((worker_id, gpu.gpu_id))
    counts["total"] = len(rows)
    if any(
        type(summary.get(key)) is not int or summary[key] != value for key, value in counts.items()
    ):
        raise NativeAdmissionError("Worker summary is incomplete")
    return WorkerEvidence(endpoint.endpoint_id, timestamp(observed_at), tuple(result))


def prepare_envelope(
    value: FlexInput, *, now: float, deadline: float, execution_seconds: int
) -> tuple[dict, str]:
    """Validated protocol v1 and bounded native policy; does not reserve or POST."""
    try:
        checked = FlexInput.model_validate(value.model_dump(mode="python"))
        payload = checked.model_dump(mode="json")
        serialized = json.dumps(
            payload, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (ValueError, TypeError, AttributeError, RecursionError) as exc:
        raise NativeAdmissionError("Invalid native generation envelope") from exc
    remaining_ms = math.floor((timestamp(deadline) - timestamp(now)) * 1000)
    integer(execution_seconds, 5, 600)
    if not 10_000 <= remaining_ms <= 604_800_000 or execution_seconds * 1000 > remaining_ms:
        raise NativeAdmissionError("Insufficient admitted generation time")
    body = {
        "input": payload,
        "policy": {"executionTimeout": execution_seconds * 1000, "ttl": remaining_ms},
    }
    return body, hashlib.sha256(serialized).hexdigest()


def billing_time(value: Any) -> float:
    if not isinstance(value, str):
        raise NativeAdmissionError("Invalid billing interval")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError
        return timestamp(parsed.timestamp())
    except (ValueError, OverflowError) as exc:
        raise NativeAdmissionError("Invalid billing interval") from exc


@dataclass(frozen=True)
class OwnedCleanupEvidence:
    endpoint_id: str
    session_id: str
    image: str
    volume_id: str
    observed_at: float


def parse_owned_cleanup(
    raw: Any,
    *,
    session_id: str,
    expected_image: str,
    expected_volume: str,
    observed_at: float,
    now: float,
) -> OwnedCleanupEvidence:
    """Exact dedicated ownership only; expired qualification cannot block cleanup.

    Wrong runtime controls block admission but must not hide an owned cleanup
    target. Changed image/volume/name/markers or shared endpoints still refuse.
    """
    session_id = uuid_text(session_id)
    identity(expected_volume)
    fresh(observed_at, now, 30)
    if not isinstance(expected_image, str) or not _IMAGE.fullmatch(expected_image):
        raise NativeAdmissionError("Invalid owned image identity")
    if (
        not isinstance(raw, dict)
        or raw.get("type") != "QUEUE"
        or raw.get("name") != "vcs-native-" + session_id
        or raw.get("image") != expected_image
        or raw.get("networkVolumes") != [expected_volume]
    ):
        raise NativeAdmissionError("Dedicated cleanup ownership changed")
    env = raw.get("env")
    if (
        not isinstance(env, dict)
        or env.get("VCS_NATIVE_SESSION_ID") != session_id
        or env.get("VCS_NATIVE_DEDICATED") != "true"
    ):
        raise NativeAdmissionError("Dedicated cleanup ownership is unconfirmed")
    return OwnedCleanupEvidence(
        identity(raw.get("id")), session_id, expected_image, expected_volume, timestamp(observed_at)
    )
