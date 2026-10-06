"""Read-only native qualification from reviewed, source-embedded evidence.

No receipt is approved in this release. Files, environment variables, request
fields and profile settings cannot add one. Hash matching here protects receipt
integrity relative to trusted application source; it is not a signature check
or evidence that the provider lifecycle was actually qualified.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields
from typing import Any

from .native_admission import NativeAdmissionError, NativeQualification, identity, timestamp

PROVIDER = "runpod-serverless-flex"
PROTOCOL_VERSION = 1
SDK_VERSION = "1.12.0"
# Reviewed source pin, LF-normalized. A source regression test detects drift.
SOURCE_PROTOCOL_SHA256 = "101505782d5fc6d1b8b84c6dd3fd8ebeec7db00ff51b11184793e8fbe88e25ed"
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_IMAGE = re.compile(r"ghcr\.io/[a-z0-9/_.-]+@sha256:[a-f0-9]{64}\Z")
_MAX_RECEIPT_BYTES = 65_536


@dataclass(frozen=True)
class NativeQualificationContext:
    """Exact trusted controller context; this never grants receipt trust."""

    image: str
    volume_id: str
    region: str
    pool: str
    manifest_sha256: str
    sdk_version: str = SDK_VERSION

    def check(self) -> None:
        if not isinstance(self.image, str) or not _IMAGE.fullmatch(self.image):
            raise NativeAdmissionError("Immutable native image is required")
        for value in (self.volume_id, self.region, self.pool):
            identity(value)
        if not isinstance(self.manifest_sha256, str) or not _SHA.fullmatch(self.manifest_sha256):
            raise NativeAdmissionError("Pinned model manifest is required")
        if self.sdk_version != SDK_VERSION:
            raise NativeAdmissionError("Native SDK is not qualified")


@dataclass(frozen=True)
class NativeQualificationStatus:
    available: bool
    code: str
    detail: str
    qualification: NativeQualification | None = None

    def public_status(self) -> dict[str, bool | str]:
        """Non-secret status only; no paths, evidence bodies or admission flags."""
        return {"available": self.available, "code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class _ReviewedReceipt:
    # Both bytes and digest must be added to reviewed application source.
    # A digest supplied by a request or an adjacent JSON file is never trusted.
    payload: bytes
    sha256: str


# Intentionally empty. Filling this requires real provider lifecycle evidence
# and an explicit application-source review, not passing synthetic offline tests.
_REVIEWED_RECEIPTS: tuple[_ReviewedReceipt, ...] = ()


def _blocked(code: str, detail: str) -> NativeQualificationStatus:
    return NativeQualificationStatus(False, code, detail)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate receipt field")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Nonfinite receipt field")


def _read_reviewed_receipt(
    receipt: _ReviewedReceipt, context: NativeQualificationContext, *, now: float
) -> NativeQualificationStatus:
    """Private parser for compiled anchors. Not a caller-supplied receipt API."""
    if (
        type(receipt.payload) is not bytes
        or not 0 < len(receipt.payload) <= _MAX_RECEIPT_BYTES
        or not isinstance(receipt.sha256, str)
        or not _SHA.fullmatch(receipt.sha256)
        or hashlib.sha256(receipt.payload).hexdigest() != receipt.sha256
    ):
        return _blocked(
            "receipt_untrusted", "Native qualification evidence failed its review check."
        )
    try:
        data = json.loads(
            receipt.payload, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
        expected = {
            "schema_version",
            "provider",
            "protocol_version",
            "sdk_version",
            "manifest_sha256",
            "source_protocol_sha256",
            "qualified_at",
            "qualification",
        }
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError
        if (
            type(data["protocol_version"]) is not int
            or data["protocol_version"] != PROTOCOL_VERSION
        ):
            raise ValueError
        if (
            data["provider"] != PROVIDER
            or data["sdk_version"] != context.sdk_version
            or data["source_protocol_sha256"] != SOURCE_PROTOCOL_SHA256
            or data["manifest_sha256"] != context.manifest_sha256
        ):
            return _blocked("receipt_mismatch", "Native qualification does not match this release.")
        if timestamp(data["qualified_at"]) > now:
            raise ValueError
        raw = data["qualification"]
        field_names = {field.name for field in fields(NativeQualification)} - {"receipt_sha256"}
        if not isinstance(raw, dict) or set(raw) != field_names:
            raise ValueError
        if any(
            raw[key] != getattr(context, key) for key in ("image", "volume_id", "region", "pool")
        ):
            return _blocked(
                "receipt_mismatch", "Native qualification does not match this storage and GPU."
            )
        qualification = NativeQualification(**raw, receipt_sha256=receipt.sha256)
        expiry = timestamp(qualification.expires_at)
        if expiry <= now:
            return _blocked(
                "receipt_expired",
                "Native qualification has expired. Generation remains unavailable.",
            )
        if timestamp(data["qualified_at"]) >= expiry:
            raise ValueError
        qualification.check(now)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return _blocked(
            "receipt_invalid", "Native qualification evidence is incomplete or invalid."
        )
    return NativeQualificationStatus(
        True,
        "qualified",
        "Native lifecycle evidence is reviewed for this configuration.",
        qualification,
    )


def native_qualification_status(
    context: NativeQualificationContext, *, now: float
) -> NativeQualificationStatus:
    """Read-only production loader. No disk, network, environment or profile access.

    A positive result is one prerequisite for future admission; it does not rent
    compute, remove existing holds, validate current prices or grant a budget.
    """
    try:
        if type(context) is not NativeQualificationContext:
            raise ValueError
        context.check()
        checked_now = timestamp(now)
    except (ValueError, TypeError, OverflowError):
        return _blocked("invalid_context", "The native generation configuration is not qualified.")
    if not _REVIEWED_RECEIPTS:
        return _blocked(
            "qualification_unavailable",
            "Native generation is not enabled yet. Provider shutdown tests are still required.",
        )
    failures: list[NativeQualificationStatus] = []
    for receipt in _REVIEWED_RECEIPTS:
        result = _read_reviewed_receipt(receipt, context, now=checked_now)
        if result.available:
            return result
        failures.append(result)
    return next(
        (failure for failure in failures if failure.code != "receipt_mismatch"), failures[0]
    )
