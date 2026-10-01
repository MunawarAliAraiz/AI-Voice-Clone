"""Build the worker with immutable base inputs. Does not push or create a Pod."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path


def digest_ref(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._:/-]+@sha256:[a-f0-9]{64}", value):
        raise argparse.ArgumentTypeError(
            "Supply a container image reference pinned by sha256 digest"
        )
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-image", type=digest_ref, required=True,
                        help="Digest of python:3.12-slim-bookworm for linux/amd64")
    parser.add_argument("--uv-image", type=digest_ref, required=True,
                        help="Digest of ghcr.io/astral-sh/uv:0.11.32")
    parser.add_argument("--tag", required=True, help="Local image tag to build")
    parser.add_argument("--target", choices=("installer", "gpu"), default="gpu",
                        help="Cheap CPU model installer or complete GPU runtime")
    args = parser.parse_args()
    docker = shutil.which("docker")
    if docker is None:
        parser.error("Docker with Linux containers is required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", args.tag):
        parser.error("Use a valid container image tag")
    root = Path(__file__).resolve().parents[1]
    # Validated references are argv values; no shell or command text interpolation.
    subprocess.run([  # noqa: S603
        docker, "build", "--platform", "linux/amd64", "--file", str(root / "pod/Dockerfile"),
        "--target", args.target,
        "--build-arg", f"PYTHON_IMAGE={args.python_image}",
        "--build-arg", f"UV_IMAGE={args.uv_image}", "--tag", args.tag, str(root),
    ], check=True)


if __name__ == "__main__":
    main()
