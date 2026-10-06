"""Allowlisted runtime failure metadata; private exception text stays private."""

from __future__ import annotations

from ..exceptions import AppError

SAFE_EXCEPTION_CLASSES = frozenset({
    "RuntimeError", "ValueError", "TypeError", "KeyError", "ImportError",
    "ModuleNotFoundError", "FileNotFoundError", "OSError", "AssertionError",
    "NotImplementedError", "OutOfMemoryError", "LocalEntryNotFoundError",
    "OfflineModeIsEnabled", "IncompleteSnapshotError",
})
SAFE_FAILURE_STAGES = frozenset({"load", "generate", "analyze", "convert"})
CUDA_ARCHITECTURE_UNSUPPORTED = "CUDA_ARCHITECTURE_UNSUPPORTED"


def runtime_error_code(error: BaseException) -> str:
    """Classify known CUDA failures inside the runtime, never at the public API.

    These messages identify kernel/architecture compatibility failures. An
    arbitrary CUDA error, illegal access, or OOM must retain its own category.
    No substring from an exception is returned as diagnostic metadata.
    """
    if isinstance(error, RuntimeError):
        message = str(error).lower()
        if any(marker in message for marker in (
            "no kernel image is available for execution on the device",
            "cuda error: invalid device function",
            "cuda error: device kernel image is invalid",
            "not compatible with the current pytorch installation",
        )):
            return CUDA_ARCHITECTURE_UNSUPPORTED
    return type(error).__name__


def with_worker_diagnostics[T: AppError](
    error: T, response: object, *, stage: str, model_id: str,
) -> T:
    """Carry trusted operation identity and a bounded exception class."""
    code = getattr(response, "error_code", None)
    error.with_worker_error(code)
    if code == CUDA_ARCHITECTURE_UNSUPPORTED:
        error.extensions["worker_error_code"] = code
    exception_class = getattr(response, "error_class", None) or code
    if isinstance(exception_class, str) and exception_class in SAFE_EXCEPTION_CLASSES:
        error.extensions["worker_exception_class"] = exception_class
    if stage in SAFE_FAILURE_STAGES:
        error.extensions["worker_stage"] = stage
    error.extensions["model_id"] = model_id
    return error
