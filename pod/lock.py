"""Regenerate isolated Linux dependency locks using the pinned uv resolver."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def main() -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("Install uv 0.11.32 to regenerate these locks")
    version = subprocess.check_output([uv, "--version"], text=True)  # noqa: S603
    if not version.startswith("uv 0.11.32 "):
        raise SystemExit("Use uv 0.11.32 to regenerate these locks")
    requirements = Path(__file__).resolve().parent / "requirements"
    for name in ("api", "build", "voxcpm", "chatterbox", "omnivoice", "text"):
        command = [
            uv, "pip", "compile", str(requirements / f"{name}.in"),
            "--python-version", "3.12", "--python-platform", "x86_64-manylinux_2_28",
            "--no-python-downloads", "--no-config", "--generate-hashes",
            "--only-binary", ":all:", "--emit-index-url", "--no-annotate", "--no-header",
            "--output-file", str(requirements / f"{name}.lock"),
        ]
        if name == "voxcpm":
            # These pinned releases have no usable Python 3.12 Linux wheels.
            command += ["--no-binary", "antlr4-python3-runtime,argbind,crcmod,jieba,oss2"]
        if name not in {"api", "build"}:
            command += ["--torch-backend", "cu124" if name == "chatterbox" else "cu128"]
        subprocess.run(command, check=True)  # noqa: S603 -- fixed resolver arguments, no shell


if __name__ == "__main__":
    main()
