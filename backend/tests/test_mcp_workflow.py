"""Direction and bounded waiting keep the durable job contract intact."""

import asyncio

import pytest

pytest.importorskip("mcp")
from mcp.server.mcpserver.exceptions import ToolError

from app.mcp import __main__ as tools


@pytest.mark.asyncio
async def test_generation_carries_reference_and_direction_without_rewriting_text(
    monkeypatch,
) -> None:
    requests = []

    async def request(method, path, *, body=None):
        requests.append((method, path, body))
        return {"id": 7, "status": "queued"}

    monkeypatch.setattr(tools, "_request", request)
    plan = {"segments": [{"index": 0, "emotion": "calm", "pause_after_ms": 100}]}
    result = await tools.generate_speech(
        "Original script", 4, "en", speed=0.9, apply_direction=True, direction_plan=plan
    )
    assert result == {"id": 7, "status": "queued"}
    assert requests == [
        (
            "POST",
            "/api/generate",
            {
                "text": "Original script",
                "profile_id": 4,
                "language": "en",
                "speed": 0.9,
                "apply_direction": True,
                "direction_plan": plan,
            },
        )
    ]
    with pytest.raises(ToolError, match="apply_direction"):
        await tools.generate_speech("script", 4, "en", direction_plan=plan)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_wait_timeout_never_enqueues_and_preserves_running_job(monkeypatch) -> None:
    paths = []

    async def request(method, path, *, body=None):
        paths.append((method, path))
        return {"id": 8, "status": "running", "eta_sec": 99}

    monkeypatch.setattr(tools, "_request", request)
    result = await tools.wait_for_job(8, timeout_seconds=0)
    assert result["timed_out"] and not result["settled"]
    assert result["job"]["status"] == "running"
    assert paths == [("GET", "/api/jobs/8")]


@pytest.mark.parametrize("state", ["succeeded", "failed", "cancelled"])
@pytest.mark.asyncio
async def test_wait_distinguishes_terminal_status(monkeypatch, state) -> None:
    async def request(*args, **kwargs):
        return {"id": 8, "status": state}

    monkeypatch.setattr(tools, "_request", request)
    result = await tools.wait_for_job(8)
    assert result["settled"] and not result["timed_out"]
    assert result["job"]["status"] == state


@pytest.mark.parametrize("duration", [-1, 26, float("nan")])
@pytest.mark.asyncio
async def test_wait_refuses_unbounded_timeout(duration) -> None:
    with pytest.raises(ToolError):
        await tools.wait_for_job(1, duration)


@pytest.mark.asyncio
async def test_wait_bounds_a_stalled_http_request_without_cancelling_job(monkeypatch) -> None:
    paths = []

    async def request(method, path, *, body=None):
        paths.append((method, path))
        await asyncio.sleep(10)
        return {"id": 8, "status": "running"}

    monkeypatch.setattr(tools, "_request", request)
    result = await asyncio.wait_for(tools.wait_for_job(8, 0), timeout=2)
    assert result["timed_out"] and result["job"] is None
    assert result["job_id"] == 8
    assert paths == [("GET", "/api/jobs/8")]
