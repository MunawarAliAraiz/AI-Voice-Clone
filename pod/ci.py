"""Hosted image qualification/evidence. No Runpod or model-download calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
IMAGE = re.compile(r"ghcr\.io/[a-z0-9._/-]+")


def docker(*args: str) -> str:
    executable = shutil.which("docker")
    if not executable:
        raise ValueError("Docker with Linux containers is required")
    return subprocess.run(  # noqa: S603
        [executable, *args], check=True, capture_output=True, text=True,
    ).stdout


def immutable_image(image: str, digest: str) -> str:
    if not IMAGE.fullmatch(image) or not DIGEST.fullmatch(digest):
        raise ValueError("Use a GHCR image and immutable sha256 digest")
    return f"{image}@{digest}"


def published_sizes(reference: str) -> dict[str, object]:
    document = json.loads(docker("buildx", "imagetools", "inspect", "--raw", reference))
    if "manifests" in document:
        candidates = [
            item for item in document["manifests"]
            if item.get("platform", {}).get("os") == "linux"
            and item.get("platform", {}).get("architecture") == "amd64"
        ]
        if len(candidates) != 1:
            raise ValueError("Expected one linux/amd64 runtime manifest")
        manifest_digest = candidates[0]["digest"]
        child = immutable_image(reference.split("@")[0], manifest_digest)
        document = json.loads(docker("buildx", "imagetools", "inspect", "--raw", child))
    else:
        manifest_digest = reference.split("@")[1]
    layers = document.get("layers", [])
    if not layers or any(not isinstance(layer.get("size"), int) for layer in layers):
        raise ValueError("Published manifest has no trustworthy layer sizes")
    return {
        "platform_manifest_digest": manifest_digest,
        "compressed_layer_bytes": sum(layer["size"] for layer in layers),
        "layer_count": len(layers),
        "expanded_disk_bytes": None,
        "size_note": "Compressed registry layers; expanded Pod disk use is unmeasured.",
    }


def smoke(reference: str) -> None:
    image, separator, digest = reference.partition("@")
    if not separator:
        raise ValueError("Smoke testing requires a published immutable image")
    immutable_image(image, digest)
    docker("pull", reference)
    with tempfile.TemporaryDirectory(prefix="vcs-installer-smoke-") as volume:
        # Docker receives the token via inherited environment, never argv or logs.
        previous = os.environ.get("POD_WORKER_TOKEN")
        os.environ["POD_WORKER_TOKEN"] = secrets.token_urlsafe(32)
        container = ""
        try:
            container = docker(
                "run", "--detach", "--env", "POD_WORKER_TOKEN", "--mount",
                f"type=bind,source={volume},target=/workspace", reference,
            ).strip()
            for _ in range(60):
                try:
                    docker("exec", container, "/opt/venvs/api/bin/python",
                           "/opt/vcs/pod/healthcheck.py")
                    break
                except subprocess.CalledProcessError:
                    time.sleep(1)
            else:
                raise ValueError("CPU installer did not become healthy")
            code = (
                "import importlib.util,json,os,urllib.request,urllib.error; "
                "assert importlib.util.find_spec('torch') is None; "
                "opener=urllib.request.build_opener(urllib.request.ProxyHandler({})); "
                "req=urllib.request.Request('http://127.0.0.1:8000/v1/models',"
                "headers={'Authorization':'Bearer '+os.environ['POD_WORKER_TOKEN']}); "
                "data=json.load(opener.open(req,timeout=5)); "
                "assert data.get('protocol_version') == 1; "
                "assert isinstance(data.get('models'),list); "
                "req=urllib.request.Request('http://127.0.0.1:8000/v1/capacity',"
                "headers={'Authorization':'Bearer '+os.environ['POD_WORKER_TOKEN']}); "
                "capacity=json.load(opener.open(req,timeout=5)); "
                "assert capacity.get('protocol_version') == 1; "
                "assert capacity.get('state') == 'not_started'"
            )
            docker("exec", container, "/opt/venvs/api/bin/python", "-c", code)
            # Unauthenticated health must not leak account/model details.
            unauthorized = (
                "import urllib.request,urllib.error; "
                "opener=urllib.request.build_opener(urllib.request.ProxyHandler({})); "
                "exec(\"try:\\n opener.open('http://127.0.0.1:8000/v1/health',timeout=5)\\n"
                " raise AssertionError('Unauthenticated health accepted')\\n"
                "except urllib.error.HTTPError as error:\\n assert error.code == 401\")"
            )
            docker("exec", container, "/opt/venvs/api/bin/python", "-c", unauthorized)
        finally:
            if container:
                docker("rm", "--force", container)
            if previous is None:
                os.environ.pop("POD_WORKER_TOKEN", None)
            else:
                os.environ["POD_WORKER_TOKEN"] = previous
    print("CPU installer: authenticated health/models/capacity, denied anonymous access, no torch; "
          "no weights downloaded.")


def flex_smoke_code() -> str:
    """Fixed GPUless qualification; never start the SDK or download a model."""
    return "\n".join([
        "import importlib.util, inspect, os, sys",
        "from importlib.metadata import version",
        "from pathlib import Path",
        "assert importlib.util.find_spec('torch') is None",
        "assert version('runpod') == '1.12.0'",
        "sys.path.insert(0, '/opt/vcs/pod')",
        "from flex_start import WorkerRuntime, load_sdk",
        "from start import prepare_cache_environment",
        "from preflight import verify_imports",
        "sdk = load_sdk()",
        "assert callable(sdk.serverless.start)",
        "from runpod.http_client import AsyncClientSession",
        "from runpod.serverless.modules.rp_progress import _async_progress_update",
        "from runpod.serverless.modules.rp_logger import RunPodLogger",
        "assert inspect.iscoroutinefunction(_async_progress_update)",
        "assert AsyncClientSession is not None",
        "assert RunPodLogger().level == 'NOTSET'",
        "assert not os.environ.get('RUNPOD_WEBHOOK_GET_JOB')",
        "assert not os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT')",
        "assert os.environ.get('RUNPOD_REALTIME_PORT') == '0'",
        "from app.remote_worker.flex_handler import FlexHandler",
        "assert inspect.iscoroutinefunction(FlexHandler.handle)",
        "assert inspect.iscoroutinefunction(WorkerRuntime.handle)",
        "namespace = prepare_cache_environment(mount_root='/runpod-volume')",
        "assert namespace == Path('/runpod-volume')",
        "assert os.environ['HF_HOME'] == '/runpod-volume/hf-cache'",
        "assert os.environ['HF_HUB_CACHE'] == '/runpod-volume/hf-cache/hub'",
        "assert os.environ['HF_HUB_OFFLINE'] == '1'",
        "assert os.environ['TRANSFORMERS_OFFLINE'] == '1'",
        "assert os.environ['VCS_MODEL_VOLUME_ROOT'] == '/runpod-volume'",
        "assert os.environ['VCS_DATA_DIR'].startswith('/tmp/vcs-runtime-')",
        "assert os.environ['TMPDIR'].startswith('/tmp/vcs-runtime-')",
        "assert not any(path.is_file() for path in Path('/runpod-volume').rglob('*'))",
        "os.environ['HF_HOME'] = '/runpod-volume/voice-clone/hf-cache'",
        "os.environ['HF_HUB_CACHE'] = '/runpod-volume/voice-clone/hf-cache/hub'",
        "namespace = prepare_cache_environment(mount_root='/runpod-volume')",
        "assert namespace == Path('/runpod-volume/voice-clone')",
        "assert os.environ['TORCH_HOME'] == '/runpod-volume/voice-clone/torch-cache'",
        "assert Path('/runpod-volume/hf-cache/hub').is_dir()",
        "assert Path('/runpod-volume/voice-clone/hf-cache/hub').is_dir()",
        "assert not any(path.is_file() for path in Path('/runpod-volume').rglob('*'))",
        "verify_imports(require_cuda=False)",
    ])


def smoke_flex(reference: str) -> None:
    image, separator, digest = reference.partition("@")
    if not separator:
        raise ValueError("Smoke testing requires a published immutable image")
    immutable_image(image, digest)
    docker("pull", reference)
    with tempfile.TemporaryDirectory(prefix="vcs-flex-smoke-") as volume:
        # No endpoint channels, credentials, network, GPU or default entrypoint.
        # --rm guarantees even failed smoke containers leave no running process.
        docker(
            "run", "--rm", "--network", "none", "--mount",
            f"type=bind,source={volume},target=/runpod-volume",
            "--entrypoint", "/opt/venvs/flex/bin/python", reference,
            "-c", flex_smoke_code(),
        )
    print("Flex: pinned SDK/hooks, torch-free API, runtime imports and mounted cache; "
          "network disabled; CUDA, generation, provider lifecycle and billing not tested.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    disk = commands.add_parser("disk")
    disk.add_argument("--target", choices=("gpu", "installer", "flex"), required=True)
    name = commands.add_parser("name")
    name.add_argument("--repository", required=True)
    name.add_argument("--target", choices=("gpu", "installer", "flex"), required=True)
    test = commands.add_parser("smoke")
    test.add_argument("--image", required=True)
    flex_test = commands.add_parser("smoke-flex")
    flex_test.add_argument("--image", required=True)
    evidence = commands.add_parser("evidence")
    evidence.add_argument("--image", required=True)
    evidence.add_argument("--digest", required=True)
    evidence.add_argument("--target", choices=("gpu", "installer", "flex"), required=True)
    evidence.add_argument("--source-commit", required=True)
    evidence.add_argument("--run-url", required=True)
    evidence.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "disk":
        required = (45 if args.target in {"gpu", "flex"} else 8) * 1024**3
        free = shutil.disk_usage(ROOT).free
        if free < required:
            raise ValueError(
                f"{args.target} build needs at least {required // 1024**3} GiB free; "
                f"found {free // 1024**3}"
            )
        print(f"Free hosted-runner space: {free // 1024**3} GiB")
    elif args.command == "name":
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
            raise ValueError("Invalid repository name")
        print(f"image=ghcr.io/{args.repository.lower()}-{args.target}")
    elif args.command == "smoke":
        smoke(args.image)
    elif args.command == "smoke-flex":
        smoke_flex(args.image)
    else:
        reference = immutable_image(args.image, args.digest)
        if not re.fullmatch(r"[a-f0-9]{40}", args.source_commit):
            raise ValueError("Expected a complete source commit SHA")
        record = {
            "schema_version": 1,
            "target": args.target,
            "source_commit": args.source_commit,
            "image": reference,
            "python_base": os.environ["PYTHON_IMAGE"],
            "uv_base": os.environ["UV_IMAGE"],
            "debian_snapshot": "20260901T000000Z",
            "workflow_run": args.run_url,
            "dependency_locks": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted((ROOT / "pod/requirements").glob("*.lock"))
            },
            "qualification": {
                "runtime_imports": "passed during image build",
                "cpu_service": "passed" if args.target == "installer" else "not applicable",
                "cuda": "not tested",
                "real_generation": "not tested",
                "listening": "not tested",
                "billing": "not tested",
                **({
                    "sdk_version": "1.12.0",
                    "sdk_hooks": "passed in network-disabled CPU container",
                    "sdk_lifecycle": "not tested on provider",
                    "cache_namespaces": "passed in network-disabled CPU container",
                    "network_disabled_smoke": "passed",
                    "provider_cleanup": "not tested",
                    "spending_protection": "not qualified",
                } if args.target == "flex" else {}),
            },
            **published_sizes(reference),
        }
        args.output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
