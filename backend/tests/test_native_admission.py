"""Synthetic native admission/SQLite regression checks; no provider transport."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.runpod.flex_protocol import FlexInput
from app.runpod.native_admission import (
    NativeAdmissionError,
    NativeQualification,
    parse_catalog,
    parse_endpoint,
    parse_workers,
    prepare_envelope,
)
from app.runpod.native_ledger import NativeLedger

NOW = 1_800_000_000.0
IMAGE = "ghcr.io/example/flex@sha256:" + "a" * 64
HASH = "b" * 64


def qualification(**changes):
    return replace(
        NativeQualification(
            IMAGE,
            "volume1",
            "US-NE-1",
            "ADA_24",
            HASH,
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
        ),
        **changes,
    )


def raw_catalog():
    return {
        "gpus": [
            {
                "id": "NVIDIA GeForce RTX 4090",
                "pool": "ADA_24",
                "memory": 24,
                "price": {"secure": 0.01, "serverless": 1.10},
                "dataCenters": [{"id": "US-NE-1", "availability": "HIGH"}],
            }
        ]
    }


def catalog():
    return parse_catalog(raw_catalog(), observed_at=NOW, product_context="SERVERLESS")


def endpoint_raw(sid):
    return {
        "id": "endpoint1",
        "name": "vcs-native-" + sid,
        "type": "QUEUE",
        "image": IMAGE,
        "networkVolumes": ["volume1"],
        "dataCenterIds": ["US-NE-1"],
        "flashboot": "OFF",
        "version": 1,
        "env": {"VCS_NATIVE_SESSION_ID": sid, "VCS_NATIVE_DEDICATED": "true"},
        "gpu": {"pools": ["ADA_24"], "count": 1},
        "workers": {"min": 0, "max": 1, "idleTimeout": 60},
        "timeout": 100000,
    }


def endpoint(sid):
    return parse_endpoint(
        endpoint_raw(sid), session_id=sid, qualification=qualification(), observed_at=NOW, now=NOW
    )


def workers_raw():
    return {
        "endpointVersion": 1,
        "summary": {
            "running": 1,
            "idle": 0,
            "initializing": 0,
            "throttled": 0,
            "unhealthy": 0,
            "total": 1,
        },
        "workers": [
            {
                "id": "worker1",
                "status": "RUNNING",
                "isStale": False,
                "version": 1,
                "gpuCount": 1,
                "image": IMAGE,
                "uptimeSeconds": 3,
                "gpuTypeId": "NVIDIA GeForce RTX 4090",
                "dataCenterId": "US-NE-1",
            }
        ],
    }


def worker_evidence(sid):
    return parse_workers(
        workers_raw(),
        endpoint=endpoint(sid),
        catalog=catalog(),
        qualification=qualification(),
        observed_at=NOW,
        now=NOW,
    )


def ledger(tmp_path, usd="2"):
    result = NativeLedger(tmp_path / "shared" / "native.sqlite", "account1")
    result.approve_once(usd, approval_receipt=HASH)
    return result


def opened(result):
    sid = str(uuid4())
    result.open_session(
        sid,
        qualification=qualification(),
        catalog=catalog(),
        minimum_vram_gb=24,
        duration_seconds=360,
        disk_reserve_usd="0.001",
        now=NOW,
    )
    return sid


def active(result):
    sid = opened(result)
    assert result.claim_create(sid, now=NOW)
    result.bind_endpoint(sid, endpoint(sid), now=NOW)
    return sid


def operation(result, sid, at=NOW, execution=20):
    op = str(uuid4())
    result.reserve_intent(sid, op, HASH, execution_seconds=execution, queue_seconds=0, now=at)
    assert result.claim_submit(op, now=at)
    result.record_job(op, "job-" + op)
    return op


def stopped(result, sid, at=NOW + 100):
    result.request_stop(sid)
    result.observe_absence(
        sid, endpoint_id="endpoint1", endpoint_absent=True, workers_absent=True, now=at
    )
    result.observe_absence(
        sid, endpoint_id="endpoint1", endpoint_absent=True, workers_absent=True, now=at + 1
    )


def bill(amount="0.05", start=NOW, hours=1):
    return {
        "serverlessId": "endpoint1",
        "startTime": datetime.fromtimestamp(start, UTC).isoformat(),
        "endTime": datetime.fromtimestamp(start + 3600 * hours, UTC).isoformat(),
        "totalAmount": amount,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("effective_workers", 0),
        ("effective_workers", True),
        ("startup_seconds", float("nan")),
        ("restart_attempts", -1),
        ("native_execution_verified", False),
        ("native_idle_verified", False),
        ("startup_retry_stop_verified", False),
        ("expires_at", NOW),
        ("receipt_sha256", ""),
        ("image", "latest"),
    ],
)
def test_missing_or_invalid_qualification_blocks(field, value):
    with pytest.raises(NativeAdmissionError):
        qualification(**{field: value}).check(NOW)


@pytest.mark.parametrize("value", [None, 0, -1, True, "NaN", float("inf")])
def test_invalid_serverless_rate_blocks(value):
    raw = raw_catalog()
    raw["gpus"][0]["price"]["serverless"] = value
    with pytest.raises(NativeAdmissionError):
        parse_catalog(raw, observed_at=NOW, product_context="SERVERLESS")


def test_rate_uses_serverless_and_mixed_pool_worst_price():
    raw = raw_catalog()
    extra = deepcopy(raw["gpus"][0])
    extra["id"] = "NVIDIA Other GPU"
    extra["price"]["serverless"] = 2.72
    raw["gpus"].append(extra)
    ev = parse_catalog(raw, observed_at=NOW, product_context="SERVERLESS")
    assert ev.ceiling(qualification(), now=NOW, minimum_vram_gb=24) == 756


@pytest.mark.parametrize("kind", ["pod", "stale", "vram", "region", "duplicate"])
def test_catalog_context_and_compatibility(kind):
    raw = raw_catalog()
    if kind == "duplicate":
        raw["gpus"].append(deepcopy(raw["gpus"][0]))
    if kind == "region":
        raw["gpus"][0]["dataCenters"] = []
    with pytest.raises(NativeAdmissionError):
        ev = parse_catalog(
            raw, observed_at=NOW, product_context="POD" if kind == "pod" else "SERVERLESS"
        )
        ev.ceiling(
            qualification(),
            now=NOW + (301 if kind == "stale" else 0),
            minimum_vram_gb=48 if kind == "vram" else 24,
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("image", IMAGE.replace("a", "c")),
        ("networkVolumes", ["shared"]),
        ("type", "LOAD_BALANCER"),
        ("flashboot", "FLASHBOOT"),
        ("timeout", True),
        ("version", None),
    ],
)
def test_endpoint_changed_or_unknown_fields_block(key, value):
    sid = str(uuid4())
    raw = endpoint_raw(sid)
    raw[key] = value
    with pytest.raises(NativeAdmissionError):
        parse_endpoint(raw, session_id=sid, qualification=qualification(), observed_at=NOW, now=NOW)


@pytest.mark.parametrize(
    "key,value",
    [
        ("gpuTypeId", "unknown"),
        ("isStale", True),
        ("gpuCount", True),
        ("version", 2),
        ("image", "latest"),
        ("uptimeSeconds", float("nan")),
    ],
)
def test_actual_worker_rate_count_and_version(key, value):
    sid = str(uuid4())
    raw = workers_raw()
    raw["workers"][0][key] = value
    with pytest.raises(NativeAdmissionError):
        parse_workers(
            raw,
            endpoint=endpoint(sid),
            catalog=catalog(),
            qualification=qualification(),
            observed_at=NOW,
            now=NOW,
        )


def test_spike_and_incomplete_summary_block():
    sid = str(uuid4())
    raw = workers_raw()
    raw["workers"].append(dict(raw["workers"][0], id="worker2"))
    with pytest.raises(NativeAdmissionError):
        parse_workers(
            raw,
            endpoint=endpoint(sid),
            catalog=catalog(),
            qualification=qualification(),
            observed_at=NOW,
            now=NOW,
        )
    raw = workers_raw()
    raw["summary"]["total"] = 0
    with pytest.raises(NativeAdmissionError):
        parse_workers(
            raw,
            endpoint=endpoint(sid),
            catalog=catalog(),
            qualification=qualification(),
            observed_at=NOW,
            now=NOW,
        )


def test_envelope_remains_strict_v1_without_deadline_fields():
    req = FlexInput(operation_id=uuid4(), operation="models")
    body, digest = prepare_envelope(req, now=NOW, deadline=NOW + 100, execution_seconds=20)
    assert set(body) == {"input", "policy"}
    assert body["policy"] == {"executionTimeout": 20000, "ttl": 100000}
    assert body["input"]["protocol_version"] == 1 and len(digest) == 64
    with pytest.raises(NativeAdmissionError):
        prepare_envelope(req, now=NOW, deadline=NOW + 9, execution_seconds=5)
    forged = req.model_copy(update={"payload": {"n": float("nan")}})
    with pytest.raises(NativeAdmissionError):
        prepare_envelope(forged, now=NOW, deadline=NOW + 100, execution_seconds=20)


def test_restart_profile_and_account_cannot_reset_budget(tmp_path):
    result = ledger(tmp_path)
    sid = opened(result)
    before = result.available_micro()
    reopened = NativeLedger(result.path, "account1")
    reopened.approve_once("2", approval_receipt=HASH)
    assert reopened.available_micro() == before
    with pytest.raises(NativeAdmissionError):
        reopened.approve_once("3", approval_receipt=HASH)
    with pytest.raises(NativeAdmissionError):
        NativeLedger(result.path, "another-account")
    with pytest.raises(NativeAdmissionError):
        opened(reopened)
    assert reopened.view(sid)["state"] == "registered"


def test_concurrent_sqlite_admission_allows_one_session(tmp_path):
    result = ledger(tmp_path)

    def start(_):
        try:
            return opened(NativeLedger(result.path, "account1"))
        except NativeAdmissionError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(value is not None for value in pool.map(start, range(16))) == 1


def test_once_only_create_and_submission_lost_response(tmp_path):
    result = ledger(tmp_path)
    sid = opened(result)
    assert result.claim_create(sid, now=NOW)
    assert not NativeLedger(result.path, "account1").claim_create(sid, now=NOW)
    result.bind_endpoint(sid, endpoint(sid), now=NOW)
    op = str(uuid4())
    result.reserve_intent(sid, op, HASH, execution_seconds=20, queue_seconds=0, now=NOW)
    assert result.claim_submit(op, now=NOW)
    assert not result.claim_submit(op, now=NOW)
    with pytest.raises(NativeAdmissionError):
        operation(result, sid)
    with pytest.raises(NativeAdmissionError):
        result.observe_job(op, job_id=None, status="404", now=NOW + 400)
    assert result.view(sid)["warm_until"] is None
    assert result.available_micro() < 2_000_000


def test_concurrent_submit_claim_allows_one(tmp_path):
    result = ledger(tmp_path)
    sid = active(result)
    op = str(uuid4())
    result.reserve_intent(sid, op, HASH, execution_seconds=20, queue_seconds=0, now=NOW)
    with ThreadPoolExecutor(max_workers=8) as pool:
        wins = list(
            pool.map(
                lambda _: NativeLedger(result.path, "account1").claim_submit(op, now=NOW), range(12)
            )
        )
    assert sum(wins) == 1


def test_warm_multiple_jobs_fixed_deadline_and_no_poll_extension(tmp_path):
    result = ledger(tmp_path)
    sid = active(result)
    deadline, held = result.view(sid)["deadline"], result.available_micro()
    op = operation(result, sid)
    result.observe_job(op, job_id="job-" + op, status="COMPLETED", now=NOW + 20)
    assert result.view(sid)["warm_until"] == NOW + 80
    result.observe_job(op, job_id="job-" + op, status="COMPLETED", now=NOW + 40)
    assert result.view(sid)["warm_until"] == NOW + 80
    second = operation(result, sid, at=NOW + 45)
    assert result.view(sid)["warm_until"] is None
    result.observe_job(second, job_id="job-" + second, status="COMPLETED", now=NOW + 60)
    assert result.view(sid)["deadline"] == deadline
    assert result.available_micro() == held
    with pytest.raises(NativeAdmissionError):
        operation(result, sid, at=NOW + 120)


def test_cumulative_execution_reservations_and_expired_submit(tmp_path):
    result = ledger(tmp_path)
    sid = active(result)
    for index in range(2):
        op = operation(result, sid, at=NOW + index, execution=100)
        result.observe_job(op, job_id="job-" + op, status="COMPLETED", now=NOW + index + 1)
    with pytest.raises(NativeAdmissionError):
        operation(result, sid, at=NOW + 3, execution=100)
    with pytest.raises(NativeAdmissionError):
        result.claim_create(sid, now=NOW + 400)


def test_duplicate_operation_changed_payload_refused(tmp_path):
    result = ledger(tmp_path)
    sid = active(result)
    op = str(uuid4())
    args = dict(execution_seconds=20, queue_seconds=0, now=NOW)
    assert result.reserve_intent(sid, op, HASH, **args)["state"] == "prepared"
    assert result.reserve_intent(sid, op, HASH, **args)["state"] == "prepared"
    with pytest.raises(NativeAdmissionError):
        result.reserve_intent(sid, op, "c" * 64, **args)


def test_worker_epochs_persist_and_repeated_reads_do_not_charge_twice(tmp_path):
    result = ledger(tmp_path)
    sid = active(result)
    ev = worker_evidence(sid)
    result.observe_workers(sid, ev, now=NOW)
    NativeLedger(result.path, "account1").observe_workers(sid, ev, now=NOW)
    with pytest.raises(NativeAdmissionError):
        result.observe_workers(sid, replace(ev, workers=(("worker2", ev.workers[0][1]),)), now=NOW)
    with result._transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM epochs").fetchone()[0] == 1


def test_stop_requires_owned_two_separate_absences_and_failure_resets(tmp_path):
    result = ledger(tmp_path)
    sid = active(result)
    result.request_stop(sid)
    with pytest.raises(NativeAdmissionError):
        result.observe_absence(
            sid, endpoint_id="shared", endpoint_absent=True, workers_absent=True, now=NOW
        )
    args = dict(endpoint_id="endpoint1", endpoint_absent=True, workers_absent=True)
    result.observe_absence(sid, now=NOW, **args)
    with pytest.raises(NativeAdmissionError):
        result.observe_absence(sid, now=NOW, **args)
    result.observe_absence(
        sid, endpoint_id="endpoint1", endpoint_absent=False, workers_absent=True, now=NOW + 1
    )
    result.observe_absence(sid, now=NOW + 2, **args)
    assert result.view(sid)["state"] == "stopping"
    result.observe_absence(sid, now=NOW + 3, **args)
    assert result.view(sid)["state"] == "stopped"


def test_billing_overlap_idempotent_late_revision_and_no_missing_credit(tmp_path):
    result = ledger(tmp_path)
    sid = active(result)
    held = result.available_micro()
    assert result.apply_billing(sid, [bill(), bill()]) == 50000
    assert result.apply_billing(sid, [bill("0.01")]) == 50000
    assert result.available_micro() == held
    assert result.apply_billing(sid, [bill("0.06"), bill("0.02", NOW + 3600)]) == 80000
    with pytest.raises(NativeAdmissionError):
        result.apply_billing(sid, [bill("0.07", hours=2)])
    assert result.view(sid)["paid"] == 80000
    stopped(result, sid)
    with pytest.raises(NativeAdmissionError):
        result.settle_reviewed_billing(sid, receipt_sha256=HASH, now=NOW + 120)
    result.settle_reviewed_billing(sid, receipt_sha256=HASH, now=NOW + 131)
    assert result.available_micro() == 1_920_000
    result.apply_billing(sid, [bill("0.09")])
    assert result.available_micro() == 1_890_000


def test_missing_bills_or_wrong_endpoint_cannot_credit(tmp_path):
    result = ledger(tmp_path)
    sid = active(result)
    stopped(result, sid)
    result.apply_billing(sid, [])
    with pytest.raises(NativeAdmissionError):
        result.settle_reviewed_billing(sid, receipt_sha256=HASH, now=NOW + 200)
    bad = bill()
    bad["serverlessId"] = "other-app"
    with pytest.raises(NativeAdmissionError):
        result.apply_billing(sid, [bad])


def test_actual_overspend_freezes_new_allowance(tmp_path):
    result = ledger(tmp_path, "0.15")
    sid = active(result)
    result.apply_billing(sid, [bill("0.20")])
    stopped(result, sid)
    assert result.available_micro() < 0
    with pytest.raises(NativeAdmissionError):
        opened(result)


def test_no_scripts_keys_or_transport_in_journal(tmp_path):
    result = ledger(tmp_path)
    sid = active(result)
    operation(result, sid)
    raw = result.path.read_bytes()
    assert b"script secret" not in raw and b"api key secret" not in raw
    assert len(result.journal_digest(sid)) == 64


def test_stale_catalog_admission_refresh_and_conservative_rate_increase(tmp_path):
    result = ledger(tmp_path)
    sid = active(result)
    before = result.available_micro()
    raw = raw_catalog()
    raw["gpus"][0]["price"]["serverless"] = 2.72
    fresh_catalog = parse_catalog(raw, observed_at=NOW + 10, product_context="SERVERLESS")
    result.refresh_catalog(sid, fresh_catalog, minimum_vram_gb=24, now=NOW + 10)
    assert result.available_micro() < before
    assert result.view(sid)["rate"] == 756
    result.refresh_catalog(sid, catalog(), minimum_vram_gb=24, now=NOW + 11)
    assert result.view(sid)["rate"] == 756
    # A separate longer session permits clock checking after quote freshness ends.
    stopped(result, sid, at=NOW + 20)
    later = str(uuid4())
    result.open_session(
        later,
        qualification=qualification(),
        catalog=catalog(),
        minimum_vram_gb=24,
        duration_seconds=600,
        disk_reserve_usd="0.001",
        now=NOW + 100,
    )
    with pytest.raises(NativeAdmissionError, match="stale"):
        result.claim_create(later, now=NOW + 301)


def test_rate_increase_cannot_exceed_approved_allowance(tmp_path):
    result = ledger(tmp_path, "0.15")
    sid = active(result)
    before = result.view(sid)
    raw = raw_catalog()
    raw["gpus"][0]["price"]["serverless"] = 4.79
    ev = parse_catalog(raw, observed_at=NOW + 10, product_context="SERVERLESS")
    with pytest.raises(NativeAdmissionError):
        result.refresh_catalog(sid, ev, minimum_vram_gb=24, now=NOW + 10)
    assert result.view(sid)["rate"] == before["rate"]
    assert result.view(sid)["reserved"] == before["reserved"]


def test_clock_rollback_and_partial_qualification_lifetime_block(tmp_path):
    result = ledger(tmp_path)
    sid = active(result)
    op = operation(result, sid)
    result.observe_job(op, job_id="job-" + op, status="COMPLETED", now=NOW + 20)
    with pytest.raises(NativeAdmissionError, match="clock"):
        operation(result, sid, at=NOW + 10)
    assert result.view(sid)["warm_until"] == NOW + 80
    result.request_stop(sid)
    with pytest.raises(NativeAdmissionError):
        result.observe_absence(
            sid, endpoint_id="endpoint1", endpoint_absent=True, workers_absent=True, now=NOW
        )
    other = ledger(tmp_path / "other")
    with pytest.raises(NativeAdmissionError, match="lifetime"):
        other.open_session(
            str(uuid4()),
            qualification=qualification(expires_at=NOW + 300),
            catalog=catalog(),
            minimum_vram_gb=24,
            duration_seconds=360,
            disk_reserve_usd="0.001",
            now=NOW,
        )


def test_direct_forged_rate_and_noninteger_worker_version_rejected():
    from app.runpod.native_admission import CatalogEvidence

    ev = catalog()
    with pytest.raises(NativeAdmissionError):
        CatalogEvidence(NOW, (replace(ev.gpus[0], rate_micro_per_second=-1),)).ceiling(
            qualification(), now=NOW, minimum_vram_gb=24
        )
    sid = str(uuid4())
    raw = workers_raw()
    raw["endpointVersion"] = True
    with pytest.raises(NativeAdmissionError):
        parse_workers(
            raw,
            endpoint=endpoint(sid),
            catalog=catalog(),
            qualification=qualification(),
            observed_at=NOW,
            now=NOW,
        )
    raw = workers_raw()
    raw["workers"][0]["gpuTypeId"] = []
    with pytest.raises(NativeAdmissionError):
        parse_workers(
            raw,
            endpoint=endpoint(sid),
            catalog=catalog(),
            qualification=qualification(),
            observed_at=NOW,
            now=NOW,
        )


def test_late_create_cleanup_allowed_after_deadline_and_qualification_expiry(tmp_path):
    from app.runpod.native_admission import parse_owned_cleanup

    result = ledger(tmp_path)
    sid = opened(result)
    assert result.claim_create(sid, now=NOW)
    late = NOW + 8000
    raw = endpoint_raw(sid)
    raw["workers"]["min"] = 1
    proof = parse_owned_cleanup(
        raw,
        session_id=sid,
        expected_image=IMAGE,
        expected_volume="volume1",
        observed_at=late,
        now=late,
    )
    result.record_cleanup_target(sid, proof, now=late)
    assert result.view(sid)["state"] == "stopping"
    assert result.due_sessions(now=late) == (sid,)
    stopped(result, sid, at=late)
    assert result.view(sid)["state"] == "stopped"
    assert result.available_micro() < 2_000_000
    with pytest.raises(NativeAdmissionError):
        result.claim_create(sid, now=late)
    raw["networkVolumes"] = ["unrelated"]
    with pytest.raises(NativeAdmissionError):
        parse_owned_cleanup(
            raw,
            session_id=sid,
            expected_image=IMAGE,
            expected_volume="volume1",
            observed_at=late,
            now=late,
        )


def test_due_cleanup_uses_warm_end_without_burning_full_reserved_window(tmp_path):
    result = ledger(tmp_path)
    sid = active(result)
    op = operation(result, sid)
    result.observe_job(op, job_id="job-" + op, status="COMPLETED", now=NOW + 20)
    assert result.due_sessions(now=NOW + 79) == ()
    assert result.due_sessions(now=NOW + 80) == (sid,)
    assert result.view(sid)["deadline"] > NOW + 80


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "1001", "-1", True])
def test_invalid_approval_or_oversized_budget_is_rejected(tmp_path, amount):
    result = NativeLedger(tmp_path / "budget.sqlite", "account1")
    with pytest.raises(NativeAdmissionError):
        result.approve_once(amount, approval_receipt=HASH)


def test_stop_does_not_clear_undocumented_unknown_create(tmp_path):
    result = ledger(tmp_path)
    sid = opened(result)
    result.claim_create(sid, now=NOW)
    result.request_stop(sid)
    with pytest.raises(NativeAdmissionError):
        result.observe_absence(
            sid, endpoint_id="guessed", endpoint_absent=True, workers_absent=True, now=NOW + 400
        )
    with pytest.raises(NativeAdmissionError):
        opened(result)
    assert result.available_micro() < 2_000_000
