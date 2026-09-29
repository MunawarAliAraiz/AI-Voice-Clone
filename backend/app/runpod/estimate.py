"""Provider-rate-based compute estimates; never labels them invoice amounts."""

from __future__ import annotations

from typing import Any

from ..inference.spec import ModelSpec

MIN_FULL_FEATURE_VRAM_GB = 48


def estimate_tts_costs(
    gpu_types: list[dict[str, Any]], *, spec: ModelSpec, text: str
) -> list[dict[str, Any]]:
    # 14 characters per spoken second is only a planning assumption. The
    # current catalog's RTF/load figures came from a particular GPU, so the
    # projected time is explicitly labeled uncalibrated for another SKU.
    audio_sec = max(1.0, len(text.strip()) / 14.0)
    render_sec = audio_sec * (spec.est_rtf if spec.est_rtf is not None else 1.0)
    rows: list[dict[str, Any]] = []
    for gpu in gpu_types:
        vram = int(gpu.get("memory", 0))
        if vram < MIN_FULL_FEATURE_VRAM_GB:
            continue
        rate = gpu.get("price", {}).get("secure")
        if rate is None:
            continue
        rate = float(rate)
        rows.append({
            "gpu_id": gpu["id"], "gpu_name": gpu.get("name", gpu["id"]),
            "vram_gb": vram, "availability": gpu.get("availability", "UNKNOWN"),
            "hourly_gpu_usd": rate,
            "warm_estimated_sec": round(render_sec, 1),
            "cold_estimated_sec": round(render_sec + spec.est_load_sec, 1),
            "warm_estimated_compute_usd": round(rate * render_sec / 3600, 6),
            "cold_estimated_compute_usd": round(
                rate * (render_sec + spec.est_load_sec) / 3600, 6
            ),
            "calibration": "reference-model timing; this GPU has not been benchmarked",
            "excludes": "Pod idle time and persistent storage",
        })
    return rows
