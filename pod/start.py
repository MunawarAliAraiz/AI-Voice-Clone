"""Validate Pod configuration, keep user audio ephemeral, then exec uvicorn."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from preflight import verify_imports


def prepare() -> None:
    token = os.environ.get("POD_WORKER_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{24,512}", token):
        raise ValueError("POD_WORKER_TOKEN must be a 24-512 character URL-safe secret")
    if os.environ.get("VCS_REMOTE_WORKER_URL") or os.environ.get("VCS_DESKTOP_STATIC_DIR"):
        raise ValueError("Pod workers cannot use desktop or remote scheduler settings")
    mount = Path("/workspace")
    if not mount.is_mount():
        raise ValueError("Mount the persistent network volume at /workspace before starting")
    for name in ("hf-cache", "hf-cache/hub", "torch-cache", "torch-inductor"):
        path = mount / name
        path.mkdir(parents=True, exist_ok=True)
        if not os.access(path, os.W_OK):
            raise ValueError("The persistent model cache is not writable")
    # Set these even if a legacy template supplies /workspace/tmp or data_dir.
    # A new Pod never retains a local database, uploaded voice or generated file.
    runtime = Path(tempfile.mkdtemp(prefix="vcs-runtime-", dir="/tmp"))
    runtime.chmod(0o700)
    os.environ["VCS_DATA_DIR"] = str(runtime / "data")
    os.environ["TMPDIR"] = str(runtime / "tmp")
    Path(os.environ["TMPDIR"]).mkdir(mode=0o700)
    os.environ["HF_HOME"] = "/workspace/hf-cache"
    os.environ["HF_HUB_CACHE"] = "/workspace/hf-cache/hub"
    os.environ["TORCH_HOME"] = "/workspace/torch-cache"
    os.environ["TORCHINDUCTOR_CACHE_DIR"] = "/workspace/torch-inductor"
    # A gated model token is supplied only as an environment secret. Do not
    # call huggingface_hub.login(), which would write it to the network volume.
    os.environ["HF_TOKEN_PATH"] = str(runtime / "hf-token")
    os.environ["HF_STORED_TOKENS_PATH"] = str(runtime / "hf-stored-tokens")
    # CPU setup downloads and verifies the complete pinned graph first.
    # GPU runtimes must never fetch a moving default checkpoint during generation.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["VCS_ALLOW_FAKE_RUNTIME"] = "false"
    verify_imports(require_cuda=True)


def main() -> None:
    try:
        prepare()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        # Validation messages contain no credential values or response bodies.
        print(f"Worker preflight failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    os.execv(sys.executable, [  # noqa: S606 -- fixed local executable and arguments
        sys.executable, "-m", "uvicorn", "app.remote_worker.main:app",
        "--host", "0.0.0.0", "--port", "8000", "--workers", "1",  # noqa: S104
        "--no-access-log",
    ])


if __name__ == "__main__":
    main()
