"""Account billing aggregation uses mocked reads only, no account profile access."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import runpod as routes
from app.config import Settings
from app.runpod.account_analytics import (
    CATEGORIES,
    PERIODS,
    account_analytics,
    summarize_account_billing,
)
from app.runpod.client import RunpodApiError, RunpodClient

KEY = "dummy_private_key_never_live"


def fixture(period="24h"):
    bucket, count = PERIODS[period]
    step = timedelta(hours=1) if bucket == "hour" else timedelta(days=1)
    start = datetime(2026, 9, 1, tzinfo=UTC)
    end = start + step * count

    def stamp(value):
        return value.isoformat().replace("+00:00", "Z")

    totals = {field: 0.0 for _, _, fields in CATEGORIES for field in fields}
    totals.update(totalAmount=9.0, podGpuAmount=6.0, podCpuAmount=1.0, storageStandardAmount=2.0)
    return {
        "metadata": {
            "query": {"startTime": stamp(start), "endTime": stamp(end), "bucketSize": bucket},
            "recordCount": count,
            "totals": totals,
        },
        "records": [
            {
                "startTime": stamp(start + step * n),
                "endTime": stamp(start + step * (n + 1)),
                "totalAmount": 9.0 / count,
            }
            for n in range(count)
        ],
    }


def balance():
    return {"balance_usd": 2.1239885743, "account_hourly_spend_usd": 0.58 / 24}


@pytest.mark.parametrize("period", ["24h", "7d", "30d"])
def test_account_summary_provider_totals_once_and_all_buckets(period):
    summary = summarize_account_billing(
        period, fixture(period), balance(), now=datetime(2026, 10, 4, tzinfo=UTC)
    )
    assert summary["period"] == period and summary["total_usd"] == 9.0
    assert sum(c["amount_usd"] for c in summary["categories"]) == 9.0
    assert len(summary["history"]) == PERIODS[period][1]
    assert summary["balance_usd"] == 2.1239885743
    assert summary["checked_at"] == "2026-10-04T00:00:00Z"
    assert any("whole Runpod account" in warning for warning in summary["warnings"])


@pytest.mark.parametrize("value", [None, -1, True, "0", float("nan"), float("inf"), {}, 10**400])
def test_invalid_missing_components_are_null_not_zero(value):
    billing = fixture()
    billing["metadata"]["totals"]["serverlessDiskAmount"] = value
    billing["metadata"]["totals"]["totalAmount"] = value
    billing["records"][0]["totalAmount"] = value
    summary = summarize_account_billing(
        "24h", billing, {"balance_usd": value, "account_hourly_spend_usd": value}
    )
    assert summary["total_usd"] is None
    assert next(c for c in summary["categories"] if c["id"] == "serverless")["amount_usd"] is None
    assert summary["history"][0]["total_usd"] is None
    assert summary["balance_usd"] is None and summary["hourly_spend_usd"] is None


def test_missing_totals_and_record_are_unknown():
    billing = fixture()
    billing["metadata"].pop("totals")
    billing["records"].pop(2)
    summary = summarize_account_billing("24h", billing, {})
    assert summary["total_usd"] is None and all(
        c["amount_usd"] is None for c in summary["categories"]
    )
    assert len(summary["history"]) == 24 and summary["history"][2]["total_usd"] is None
    assert any("balance is unavailable" in w for w in summary["warnings"])


def test_deprecated_nonzero_serverless_fee_is_not_double_counted():
    billing = fixture()
    billing["metadata"]["totals"].update(serverlessGpuAmount=3, serverlessFeeAmount=1)
    summary = summarize_account_billing("24h", billing, balance())
    assert next(c for c in summary["categories"] if c["id"] == "serverless")["amount_usd"] is None
    assert summary["total_usd"] == 9 and any(
        "unexpected Serverless fee" in w for w in summary["warnings"]
    )


def test_category_sum_overflow_is_unknown():
    billing = fixture()
    billing["metadata"]["totals"].update(clusterGpuAmount=1e308, clusterDiskAmount=1e308)
    summary = summarize_account_billing("24h", billing, balance())
    assert next(c for c in summary["categories"] if c["id"] == "clusters")["amount_usd"] is None


@pytest.mark.parametrize(
    "change", ["duplicate", "outside", "timezone", "bucket", "window", "records", "record_size"]
)
def test_bad_provider_time_window_or_records_rejected(change):
    billing = fixture()
    if change == "duplicate":
        billing["records"][1] = copy.deepcopy(billing["records"][0])
    if change == "outside":
        billing["records"][0]["startTime"] = "2025-01-01T00:00:00Z"
    if change == "timezone":
        billing["metadata"]["query"]["startTime"] = "2026-09-01T00:00:00"
    if change == "bucket":
        billing["metadata"]["query"]["bucketSize"] = "day"
    if change == "window":
        billing["metadata"]["query"]["endTime"] = "2026-09-03T00:00:00Z"
    if change == "records":
        billing["records"] = "private-provider-data"
    if change == "record_size":
        billing["records"][0]["endTime"] = "2026-09-01T02:00:00Z"
    with pytest.raises(RunpodApiError, match="invalid billing"):
        summarize_account_billing("24h", billing, balance())


@pytest.mark.asyncio
@pytest.mark.parametrize("period", ["24h", "7d", "30d"])
async def test_client_fixed_aggregate_endpoint_without_resource_filter(period):
    calls = []

    def provider(request):
        calls.append(request)
        if request.url.path == "/graphql":
            return httpx.Response(
                200, json={"data": {"myself": {"clientBalance": 2.0, "currentSpendPerHr": 0.02}}}
            )
        return httpx.Response(200, json=fixture(period))

    client = RunpodClient(KEY, transport=httpx.MockTransport(provider))
    try:
        summary = await account_analytics(client, period)
        assert summary["balance_usd"] == 2 and summary["total_usd"] == 9
    finally:
        await client.close()
    billing_call = next(r for r in calls if r.url.path == "/v2/billing")
    assert billing_call.method == "GET" and billing_call.url.host == "api.runpod.io"
    assert dict(billing_call.url.params) == {
        "bucketSize": PERIODS[period][0],
        "lastN": str(PERIODS[period][1]),
    }
    assert not any("pods" in r.url.path or "network-volumes" in r.url.path for r in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
async def test_provider_denied_or_redirect_is_private(status):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://evil.example"}, text=KEY)

    client = RunpodClient(KEY, transport=httpx.MockTransport(provider))
    try:
        with pytest.raises(RunpodApiError) as raised:
            await account_analytics(client, "24h")
        assert KEY not in str(raised.value)
        assert len(calls) == 2 and all(r.url.host == "api.runpod.io" for r in calls)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [b"private" + KEY.encode(), b"x" * (1024 * 1024 + 1), b"[]"],
    ids=["bad_json", "oversized", "not_object"],
)
async def test_billing_response_bounded_and_validated(content):
    client = RunpodClient(
        KEY, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content))
    )
    try:
        with pytest.raises(RunpodApiError) as raised:
            await client.account_billing("24h")
        assert KEY not in str(raised.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_balance_unavailable_preserves_billing_with_warning():
    def provider(request):
        return (
            httpx.Response(403, text=KEY)
            if request.url.path == "/graphql"
            else httpx.Response(200, json=fixture())
        )

    client = RunpodClient(KEY, transport=httpx.MockTransport(provider))
    try:
        summary = await account_analytics(client, "24h")
        assert summary["total_usd"] == 9 and summary["balance_usd"] is None
        assert any("balance is unavailable" in w for w in summary["warnings"])
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bad_period_makes_no_provider_calls():
    calls = []
    client = RunpodClient(KEY, transport=httpx.MockTransport(lambda r: calls.append(r)))
    try:
        with pytest.raises(ValueError):
            await account_analytics(client, "all")
        with pytest.raises(ValueError):
            await client.account_billing("all")
        assert not calls
    finally:
        await client.close()


@pytest.fixture
def api(tmp_path, monkeypatch):
    app = FastAPI()
    app.state.settings = Settings(data_dir=tmp_path, desktop_static_dir=tmp_path)
    app.include_router(routes.router, prefix="/api")
    monkeypatch.setattr(
        routes,
        "_key",
        lambda settings: (
            KEY if settings.desktop_static_dir is not None else routes._store(settings)
        ),
    )
    return TestClient(app)


def test_route_saved_key_shape_and_strict_period(api, monkeypatch):
    original = RunpodClient
    monkeypatch.setattr(
        routes,
        "RunpodClient",
        lambda key: original(
            key,
            transport=httpx.MockTransport(
                lambda r: (
                    httpx.Response(403)
                    if r.url.path == "/graphql"
                    else httpx.Response(200, json=fixture("7d"))
                )
            ),
        ),
    )
    response = api.get("/api/runpod/analytics/account?period=7d")
    assert response.status_code == 200 and response.json()["period"] == "7d"
    assert set(response.json()) == {
        "period",
        "checked_at",
        "window",
        "balance_usd",
        "hourly_spend_usd",
        "total_usd",
        "categories",
        "history",
        "warnings",
    }
    assert KEY not in response.text
    assert api.get("/api/runpod/analytics/account?period=all").status_code == 422
    api.app.state.settings.desktop_static_dir = None
    assert api.get("/api/runpod/analytics/account").status_code == 404
