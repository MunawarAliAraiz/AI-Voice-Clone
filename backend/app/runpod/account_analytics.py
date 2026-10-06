"""Account-wide billing summaries; unknown amounts never become zero.

Source contract: build/runpod-openapi.json /v2/billing BillingAmounts. Its
Serverless platform charges are included in compute; the deprecated fee is zero.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta

from .client import RunpodApiError, RunpodClient

PERIODS = {"24h": ("hour", 24), "7d": ("day", 7), "30d": ("day", 30)}
CATEGORIES = (
    ("pod_gpu", "Pod GPU", ("podGpuAmount",)),
    ("pod_cpu", "Pod CPU", ("podCpuAmount",)),
    ("pod_disk", "Pod disk", ("podDiskAmount",)),
    (
        "serverless",
        "Serverless",
        (
            "serverlessGpuAmount",
            "serverlessCpuAmount",
            "serverlessDiskAmount",
            "serverlessFeeAmount",
        ),
    ),
    ("storage", "Persistent storage", ("storageStandardAmount", "storageHighPerformanceAmount")),
    ("endpoints", "Public endpoints", ("endpointAmount",)),
    ("clusters", "Clusters", ("clusterGpuAmount", "clusterDiskAmount", "clusterNetworkingAmount")),
)


def amount(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _date(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError
    return result.astimezone(UTC)


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def summarize_account_billing(
    period: str, billing: dict, balance: dict, *, now: datetime | None = None
) -> dict:
    if period not in PERIODS:
        raise ValueError("Choose 24 hours, 7 days or 30 days.")
    bucket, count = PERIODS[period]
    step = timedelta(hours=1) if bucket == "hour" else timedelta(days=1)
    try:
        metadata = billing["metadata"]
        query = metadata["query"]
        start, end = _date(query["startTime"]), _date(query["endTime"])
        if query["bucketSize"] != bucket or end - start != step * count:
            raise ValueError
        if start.minute or start.second or start.microsecond or (bucket == "day" and start.hour):
            raise ValueError
        records = billing["records"]
        if not isinstance(records, list) or len(records) > count:
            raise ValueError
        indexed = {}
        for record in records:
            row_start, row_end = _date(record["startTime"]), _date(record["endTime"])
            if (
                row_start < start
                or row_end > end
                or row_end - row_start != step
                or (row_start - start) % step != timedelta(0)
                or row_start in indexed
            ):
                raise ValueError
            indexed[row_start] = amount(record.get("totalAmount"))
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise RunpodApiError("Runpod returned an invalid billing response") from None
    warnings = [
        "This is spending for your whole Runpod account, including other apps and stopped machines."
    ]
    totals = metadata.get("totals")
    if not isinstance(totals, dict):
        totals = {}
    categories = []
    for category_id, label, fields in CATEGORIES:
        values = [amount(totals.get(field)) for field in fields]
        try:
            value = math.fsum(values) if all(v is not None for v in values) else None
        except OverflowError:
            value = None
        if value is not None and not math.isfinite(value):
            value = None
        if category_id == "serverless" and values[-1] not in (None, 0):
            value = None
            warnings.append(
                "Runpod returned an unexpected Serverless fee. Its cost breakdown is unavailable."
            )
        categories.append({"id": category_id, "label": label, "amount_usd": value})
    total = amount(totals.get("totalAmount"))
    if total is None or any(c["amount_usd"] is None for c in categories):
        warnings.append(
            "Some billing amounts are unavailable. Unknown amounts are not counted as zero."
        )
    history = []
    for index in range(count):
        row_start = start + step * index
        history.append(
            {
                "start": _stamp(row_start),
                "end": _stamp(row_start + step),
                "total_usd": indexed.get(row_start),
            }
        )
    if any(row["total_usd"] is None for row in history):
        warnings.append("Some time periods have no confirmed billing amount.")
    balance_value = amount(balance.get("balance_usd"))
    hourly_value = amount(balance.get("account_hourly_spend_usd"))
    if balance_value is None:
        warnings.append("Your current account balance is unavailable.")
    if hourly_value is None:
        warnings.append("Your current hourly account spending is unavailable.")
    return {
        "period": period,
        "checked_at": _stamp(now or datetime.now(UTC)),
        "window": {"start": _stamp(start), "end": _stamp(end), "bucket": bucket},
        "balance_usd": balance_value,
        "hourly_spend_usd": hourly_value,
        "total_usd": total,
        "categories": categories,
        "history": history,
        "warnings": warnings,
    }


async def account_analytics(client: RunpodClient, period: str) -> dict:
    if period not in PERIODS:
        raise ValueError("Choose 24 hours, 7 days or 30 days.")
    # Both are reads; await all outcomes before the owning route closes its client.
    billing, balance = await asyncio.gather(
        client.account_billing(period), client.balance(), return_exceptions=True
    )
    if isinstance(billing, BaseException):
        raise RunpodApiError(
            "Cannot read Runpod account billing. Check API access and try again."
        ) from None
    if isinstance(balance, BaseException) or not isinstance(balance, dict):
        balance = {}
    return summarize_account_billing(period, billing, balance)
