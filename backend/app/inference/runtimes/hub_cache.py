"""Offline snapshot resolution for pinned runtime checkpoints."""

from __future__ import annotations

import os


def hub_is_offline() -> bool:
    """Match Hub's accepted offline values without importing its runtime stack.

    An exact commit still triggers a tree-metadata lookup in Hub 1.33 unless
    snapshot_download receives local_files_only explicitly. The Pod installer
    prepares verified snapshots without that optional Hub metadata. Online web
    runtimes must retain their existing download behavior.
    """
    return any(
        os.environ.get(name, "").upper() in {"1", "ON", "YES", "TRUE"}
        for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    )
