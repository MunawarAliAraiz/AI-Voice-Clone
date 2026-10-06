"""Synthetic receipt parsing only. No provider qualification or HTTP occurs."""

from __future__ import annotations

import builtins
import hashlib
import json
import os
import socket
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from app.runpod import native_qualification as loader
from app.runpod.native_admission import NativeQualification

NOW = 1_800_000_000.0
IMAGE = "ghcr.io/example/flex@sha256:" + "a" * 64
MANIFEST = "c" * 64
CONTEXT = loader.NativeQualificationContext(IMAGE, "volume1", "US-NE-1", "ADA_24", MANIFEST)


def document():
    qualification = NativeQualification(
        IMAGE,
        "volume1",
        "US-NE-1",
        "ADA_24",
        "b" * 64,
        NOW + 7200,
        1,
        10,
        0,
        100,
        30,
        30,
        3600,
        True,
        True,
        True,
    )
    fields = asdict(qualification)
    del fields["receipt_sha256"]
    return {
        "schema_version": 1,
        "provider": loader.PROVIDER,
        "protocol_version": 1,
        "sdk_version": "1.12.0",
        "manifest_sha256": MANIFEST,
        "source_protocol_sha256": loader.SOURCE_PROTOCOL_SHA256,
        "qualified_at": NOW - 100,
        "qualification": fields,
    }


def anchor(data=None, *, payload=None):
    if payload is None:
        payload = json.dumps(document() if data is None else data).encode()
    return loader._ReviewedReceipt(payload, hashlib.sha256(payload).hexdigest())


def parse(receipt):
    return loader._read_reviewed_receipt(receipt, CONTEXT, now=NOW)


def test_shipped_registry_is_empty_and_cannot_qualify():
    assert loader._REVIEWED_RECEIPTS == ()
    result = loader.native_qualification_status(CONTEXT, now=NOW)
    assert not result.available
    assert result.qualification is None
    assert result.code == "qualification_unavailable"
    assert set(result.public_status()) == {"available", "code", "detail"}


def test_profile_receipt_and_environment_cannot_unlock(tmp_path, monkeypatch):
    path = tmp_path / "native-qualification.json"
    path.write_bytes(anchor().payload)
    for key in ("NATIVE_QUALIFICATION_PATH", "NATIVE_QUALIFIED", "RUNPOD_NATIVE_ENABLED"):
        monkeypatch.setenv(key, str(path) if key.endswith("PATH") else "1")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    result = loader.native_qualification_status(CONTEXT, now=NOW)
    assert result.code == "qualification_unavailable"
    assert not result.available
    assert path.read_bytes() == anchor().payload


def test_loader_uses_no_file_reads_network_or_environment(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Qualification must stay offline and source-embedded")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert loader.native_qualification_status(CONTEXT, now=NOW).qualification is None


def test_reviewed_protocol_source_pin_matches_source():
    source = Path(loader.__file__).with_name("flex_protocol.py").read_bytes()
    assert (
        hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest() == loader.SOURCE_PROTOCOL_SHA256
    )


def test_synthetic_private_anchor_parses_and_binds_receipt_hash():
    receipt = anchor()
    result = parse(receipt)
    assert result.available
    assert result.qualification.receipt_sha256 == receipt.sha256
    result.qualification.check(NOW)
    # A synthetically valid parser fixture does not enter the shipped registry.
    assert not loader.native_qualification_status(CONTEXT, now=NOW).available


@pytest.mark.parametrize(
    "change",
    [
        {"image": "latest"},
        {"volume_id": ""},
        {"region": "bad/region"},
        {"pool": None},
        {"manifest_sha256": "bad"},
        {"sdk_version": "1.13.0"},
    ],
)
def test_invalid_context_denied(change):
    result = loader.native_qualification_status(replace(CONTEXT, **change), now=NOW)
    assert result.code == "invalid_context"
    assert result.qualification is None


@pytest.mark.parametrize("now", [True, -1, float("nan"), float("inf"), "1800000000"])
def test_invalid_clock_denied(now):
    assert loader.native_qualification_status(CONTEXT, now=now).code == "invalid_context"


@pytest.mark.parametrize("sha", ["b" * 64, "", "A" * 64, None])
def test_digest_mismatch_or_shape_cannot_grant_trust(sha):
    assert parse(replace(anchor(), sha256=sha)).code == "receipt_untrusted"


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"{",
        b"[]",
        b"null",
        b"true",
        b"\xff",
        b"x" * 65_537,
        b'{"schema_version":1,"schema_version":1}',
        b'{"qualified_at":NaN}',
        b'{"qualified_at":Infinity}',
    ],
    ids=[
        "empty",
        "json",
        "array",
        "null",
        "boolean",
        "utf8",
        "oversize",
        "duplicate",
        "nan",
        "infinity",
    ],
)
def test_malformed_payload_denied(payload):
    result = parse(anchor(payload=payload))
    assert not result.available
    assert result.qualification is None
    assert result.code in {"receipt_invalid", "receipt_untrusted"}


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("schema_version", True, "receipt_invalid"),
        ("schema_version", 2, "receipt_invalid"),
        ("protocol_version", True, "receipt_invalid"),
        ("protocol_version", 2, "receipt_invalid"),
        ("provider", "pod", "receipt_mismatch"),
        ("sdk_version", "1.13.0", "receipt_mismatch"),
        ("manifest_sha256", "d" * 64, "receipt_mismatch"),
        ("source_protocol_sha256", "d" * 64, "receipt_mismatch"),
        ("qualified_at", NOW + 1, "receipt_invalid"),
        ("qualified_at", True, "receipt_invalid"),
    ],
)
def test_metadata_mismatch_denied(field, value, code):
    data = document()
    data[field] = value
    assert parse(anchor(data)).code == code


@pytest.mark.parametrize(
    "field,value",
    [
        ("image", "ghcr.io/other/flex@sha256:" + "a" * 64),
        ("volume_id", "volume2"),
        ("region", "US-WEST"),
        ("pool", "HOPPER_80"),
    ],
)
def test_exact_configuration_mismatch_denied(field, value):
    data = document()
    data["qualification"][field] = value
    assert parse(anchor(data)).code == "receipt_mismatch"


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("expires_at", NOW, "receipt_expired"),
        ("expires_at", NOW - 1, "receipt_expired"),
        ("expires_at", True, "receipt_invalid"),
        ("effective_workers", True, "receipt_invalid"),
        ("effective_workers", 0, "receipt_invalid"),
        ("effective_workers", 17, "receipt_invalid"),
        ("startup_seconds", 601, "receipt_invalid"),
        ("restart_attempts", 5, "receipt_invalid"),
        ("execution_seconds", 0, "receipt_invalid"),
        ("stop_reserve_seconds", 0, "receipt_invalid"),
        ("billing_lag_seconds", 0, "receipt_invalid"),
        ("max_session_seconds", 3601, "receipt_invalid"),
        ("native_execution_verified", False, "receipt_invalid"),
        ("native_idle_verified", 1, "receipt_invalid"),
        ("startup_retry_stop_verified", False, "receipt_invalid"),
    ],
)
def test_qualification_checks_remain_enforced(field, value, code):
    data = document()
    data["qualification"][field] = value
    assert parse(anchor(data)).code == code


@pytest.mark.parametrize("location", ["root", "qualification"])
@pytest.mark.parametrize("mutation", ["extra", "missing"])
def test_exact_receipt_fields_required(location, mutation):
    data = document()
    target = data if location == "root" else data["qualification"]
    if mutation == "extra":
        target["unlock"] = True
    else:
        target.pop(next(iter(target)))
    assert parse(anchor(data)).code == "receipt_invalid"


def test_duplicate_nested_field_denied():
    payload = anchor().payload.replace(
        b'"effective_workers": 1', b'"effective_workers": 1, "effective_workers": 1'
    )
    assert parse(anchor(payload=payload)).code == "receipt_invalid"


def test_error_detail_never_echoes_receipt_data():
    data = document()
    data["sdk_version"] = "SECRET_PROVIDER_BODY"
    status = parse(anchor(data)).public_status()
    assert "SECRET" not in json.dumps(status)
    assert "sha256" not in status
