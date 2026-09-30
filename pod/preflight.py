"""Import checks in isolated child interpreters; never download or load weights."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess

RUNTIMES = (
    ("voxcpm", "VCS_VOXCPM_PYTHON",
     ("from voxcpm.core import VoxCPM; import inspect; "
      "assert 'voxcpm_model_path' in inspect.signature(VoxCPM).parameters")),
    ("chatterbox", "VCS_CHATTERBOX_PYTHON",
     ("from chatterbox.mtl_tts import ChatterboxMultilingualTTS; "
     "from perth import PerthImplicitWatermarker; "
     "assert PerthImplicitWatermarker is not None")),
    ("omnivoice", "VCS_OMNIVOICE_PYTHON", "from omnivoice import OmniVoice"),
    ("text", "VCS_QWEN_ANALYZER_PYTHON",
     ("from transformers import AutoModelForCausalLM, AutoTokenizer; "
      "import accelerate, bitsandbytes")),
)


def verify_imports(*, require_cuda: bool) -> None:
    if importlib.util.find_spec("torch") is not None:
        raise ValueError("The worker API interpreter must not contain torch")
    for name, setting, imports in RUNTIMES:
        interpreter = os.environ.get(setting, "")
        if not interpreter:
            raise ValueError(f"Missing {setting}")
        code = "import torch; " + imports
        if require_cuda:
            code += "; assert torch.cuda.is_available(), 'CUDA unavailable'"
        # The image supplies each interpreter path; launching it is the point
        # of the isolated-runtime check. Library output stays off public logs.
        result = subprocess.run(  # noqa: S603
            [interpreter, "-c", code], capture_output=True, text=True, timeout=180, check=False,
        )
        if result.returncode:
            # Do not forward dependency logs, which may include model access URLs.
            raise ValueError(f"{name} import/CUDA check failed; inspect this runtime manually")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--imports-only", action="store_true")
    args = parser.parse_args()
    verify_imports(require_cuda=not args.imports_only)
