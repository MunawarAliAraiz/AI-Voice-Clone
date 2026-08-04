"""
AI Voice Clone Studio — Inference layer.

THE STRUCTURAL INVARIANT OF THIS CODEBASE:

    `import torch` must not be reachable from `app.main`.

Only `inference/runtimes/**` and `inference/worker.py` may import torch, and
neither is imported by the API process — the worker is spawned as a subprocess,
not imported. Wave 4 verifies this mechanically:

    grep -rn "^import torch\\|^from torch" backend/app/

must match those two locations and nothing else. It is the single strongest
guarantee in the design: it is what lets the whole API test suite run on a
CPU-only laptop in ~30s, and what keeps a 4-second torch import out of startup.

This `__init__` deliberately exports only contract types. Importing
`InferenceScheduler` here would drag implementation into every consumer and
quietly destroy the seam that B2 codes against.
"""

from .catalog import CATALOG, ModelCatalog, build_catalog
from .protocol import (
    ModelStatus,
    SchedulerProtocol,
    SynthRequest,
    SynthResult,
    WireOp,
    WireRequest,
    WireResponse,
    WorkerHandle,
)
from .spec import LanguageSupport, License, ModelSpec, ModelState, RuntimeKind

__all__ = [
    "RuntimeKind",
    "License",
    "ModelState",
    "LanguageSupport",
    "ModelSpec",
    "ModelCatalog",
    "CATALOG",
    "build_catalog",
    "SchedulerProtocol",
    "SynthRequest",
    "SynthResult",
    "ModelStatus",
    "WorkerHandle",
    "WireOp",
    "WireRequest",
    "WireResponse",
]
