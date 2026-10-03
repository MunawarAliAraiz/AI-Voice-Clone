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
     "import torchaudio; from importlib.metadata import version; "
     "assert PerthImplicitWatermarker is not None; "
     "assert version('chatterbox-tts') == '0.1.7'; "
     "assert torch.__version__ == '2.8.0+cu128'; "
     "assert torchaudio.__version__ == '2.8.0+cu128'; "
     "assert torch.version.cuda == '12.8'")),
    ("omnivoice", "VCS_OMNIVOICE_PYTHON", "from omnivoice import OmniVoice"),
    ("text", "VCS_QWEN_ANALYZER_PYTHON",
     ("from transformers import AutoModelForCausalLM, AutoTokenizer; "
      "import accelerate, bitsandbytes")),
)


def child_check_code(imports: str, *, require_cuda: bool) -> str:
    code = "import torch; " + imports
    if require_cuda:
        # Device discovery alone succeeds on an unsupported CUDA architecture.
        # A tiny real kernel establishes executable support without weights.
        code += (
            "; assert torch.cuda.is_available(), 'CUDA unavailable'"
            "; capability = torch.cuda.get_device_capability()"
            "; assert capability[0] < 10 or "
            "tuple(map(int, torch.version.cuda.split('.'))) >= (12, 8), "
            "'Blackwell requires the CUDA 12.8 runtime'"
            "; probe = torch.ones(2, device='cuda')"
            "; assert (probe + probe).sum().item() == 4.0"
            "; torch.cuda.synchronize()"
        )
    return code


def verify_imports(*, require_cuda: bool) -> None:
    if importlib.util.find_spec("torch") is not None:
        raise ValueError("The worker API interpreter must not contain torch")
    for name, setting, imports in RUNTIMES:
        interpreter = os.environ.get(setting, "")
        if not interpreter:
            raise ValueError(f"Missing {setting}")
        code = child_check_code(imports, require_cuda=require_cuda)
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
