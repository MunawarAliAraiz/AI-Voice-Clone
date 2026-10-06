"""Curated cloud errors: runtime exception text never crosses the HTTP boundary."""

from __future__ import annotations

from ..exceptions import AppError
from ..inference.catalog import CATALOG
from ..inference.error_diagnostics import (
    CUDA_ARCHITECTURE_UNSUPPORTED,
    SAFE_EXCEPTION_CLASSES,
    SAFE_FAILURE_STAGES,
)
from .model_pins import AUXILIARY_PINS

WORKER_MESSAGES = {
    "CUDA_ARCHITECTURE_UNSUPPORTED": (
        "This model's GPU runtime is not compatible with the selected GPU. "
        "Use a compatible GPU or check for an app update."
    ),
    "GPU_MEMORY_EXHAUSTED": (
        "The cloud GPU ran out of memory. Try a shorter script or a GPU with more memory."
    ),
    "MODEL_CACHE_MISSING": (
        "Required model files are missing from cloud storage. Open Runpod and check model setup."
    ),
    "MODEL_NOT_DOWNLOADED": (
        "Required model files are missing from cloud storage. Open Runpod and check model setup."
    ),
    "MODEL_LOAD_FAILED": (
        "The cloud model could not load. Check for an app update before retrying."
    ),
    "WORKER_CRASHED": (
        "The cloud inference process stopped unexpectedly. "
        "Try once more; if it repeats, check for an app update."
    ),
    "GENERATION_TIMEOUT": "Cloud generation took too long. Try a shorter script.",
    "VRAM_EXHAUSTED": (
        "This GPU does not have enough memory for the selected model. Use a GPU with more memory."
    ),
    "ANALYZER_UNAVAILABLE": (
        "The cloud speech-direction helper could not load or run. "
        "Check for an app update before retrying."
    ),
    "TRANSLITERATOR_UNAVAILABLE": (
        "The cloud script-conversion helper could not load or run. "
        "Check for an app update before retrying."
    ),
    "GENERATION_FAILED": (
        "The cloud model could not generate speech. "
        "Try once more; if it repeats, check for an app update."
    ),
    "INTERNAL_ERROR": (
        "The cloud worker encountered an unexpected error. Check for an app update before retrying."
    ),
}


def safe_worker_problem(error: AppError) -> dict:
    code = error.code if error.code in WORKER_MESSAGES else "INTERNAL_ERROR"
    # Wrapped helper failures retain their underlying known category. Never
    # inspect arbitrary exception text; traversing is bounded and cycle-safe.
    current: BaseException | None = error
    seen: set[int] = set()
    classified = False
    diagnostics = {}
    model_ids = {spec.id for spec in CATALOG.specs} | set(AUXILIARY_PINS)
    for _ in range(16):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        extensions = getattr(current, "extensions", {})
        if isinstance(extensions, dict):
            for source, target, allowed in (
                ("worker_exception_class", "exception_class", SAFE_EXCEPTION_CLASSES),
                ("worker_stage", "stage", SAFE_FAILURE_STAGES),
                ("model_id", "model_id", model_ids),
            ):
                value = extensions.get(source)
                if isinstance(value, str) and value in allowed:
                    diagnostics.setdefault(target, value)
        runtime_code = extensions.get("worker_error_code") if isinstance(extensions, dict) else None
        if not isinstance(runtime_code, str):
            runtime_code = type(current).__name__
        if not classified and runtime_code == "OutOfMemoryError":
            code = "GPU_MEMORY_EXHAUSTED"
            classified = True
        elif not classified and runtime_code in {"LocalEntryNotFoundError", "OfflineModeIsEnabled"}:
            code = "MODEL_CACHE_MISSING"
            classified = True
        elif not classified and runtime_code == CUDA_ARCHITECTURE_UNSUPPORTED:
            code = CUDA_ARCHITECTURE_UNSUPPORTED
            classified = True
        current = current.__cause__ or current.__context__
    status = error.http_status if 400 <= error.http_status <= 599 else 500
    if code in {"GPU_MEMORY_EXHAUSTED", CUDA_ARCHITECTURE_UNSUPPORTED}:
        status = 503
    return {
        "type": "https://voiceclone.local/problems/" + code.lower().replace("_", "-"),
        "title": "Cloud worker could not complete this request",
        "status": status,
        "code": code,
        "detail": WORKER_MESSAGES[code],
        **({"diagnostics": diagnostics} if diagnostics else {}),
    }
