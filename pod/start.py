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
    # The desktop may explicitly isolate this app's cache within a volume used
    # by another app. Preserve that approved location across CPU and GPU Pods.
    cache_home = os.environ.get("HF_HOME", "/workspace/hf-cache")
    if cache_home not in {"/workspace/hf-cache", "/workspace/voice-clone/hf-cache"}:
        raise ValueError("Unexpected model cache location")
    hub_cache = cache_home + "/hub"
    if os.environ.get("HF_HUB_CACHE", hub_cache) != hub_cache:
        raise ValueError("Model cache locations do not match")
    cache_parent = (
        "/workspace/voice-clone"
        if cache_home.startswith("/workspace/voice-clone/") else "/workspace"
    )
    torch_home = cache_parent + "/torch-cache"
    torch_inductor = cache_parent + "/torch-inductor"
    mount = Path("/workspace")
    if not mount.is_mount():
        raise ValueError("Mount the persistent network volume at /workspace before starting")
    for location in (cache_home, hub_cache, torch_home, torch_inductor):
        path = Path(location)
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
    os.environ["HF_HOME"] = cache_home
    os.environ["HF_HUB_CACHE"] = hub_cache
    os.environ["TORCH_HOME"] = torch_home
    os.environ["TORCHINDUCTOR_CACHE_DIR"] = torch_inductor
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
