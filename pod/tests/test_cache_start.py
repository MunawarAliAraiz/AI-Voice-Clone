"""GPU startup must use the same approved storage location as model setup."""

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

POD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POD))


def load_start():
    spec = importlib.util.spec_from_file_location("vcs_cache_start", POD / "start.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "cache_home", [None, "/workspace/hf-cache", "/workspace/voice-clone/hf-cache"]
)
def test_preparation_preserves_approved_cache_and_isolates_torch(tmp_path, cache_home):
    start = load_start()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    environment = {
        "POD_WORKER_TOKEN": "x" * 32,
        "TORCH_HOME": "/unapproved/torch",
        "TORCHINDUCTOR_CACHE_DIR": "/unapproved/compiler",
    }
    if cache_home:
        environment.update(HF_HOME=cache_home, HF_HUB_CACHE=cache_home + "/hub")

    def mapped_path(value):
        if str(value).startswith("/workspace"):
            return tmp_path / "volume" / str(value).removeprefix("/workspace").lstrip("/")
        return Path(value)

    with patch.dict(os.environ, environment, clear=True), \
            patch.object(start, "Path", side_effect=mapped_path), \
            patch.object(Path, "is_mount", return_value=True), \
            patch.object(start.tempfile, "mkdtemp", return_value=str(runtime)), \
            patch.object(start, "verify_imports") as imports:
        start.prepare()
        chosen = cache_home or "/workspace/hf-cache"
        parent = "/workspace/voice-clone" if "voice-clone" in chosen else "/workspace"
        assert os.environ["HF_HOME"] == chosen
        assert os.environ["HF_HUB_CACHE"] == chosen + "/hub"
        assert os.environ["TORCH_HOME"] == parent + "/torch-cache"
        assert os.environ["TORCHINDUCTOR_CACHE_DIR"] == parent + "/torch-inductor"
        assert mapped_path(chosen + "/hub").is_dir()
        assert mapped_path(parent + "/torch-cache").is_dir()
        assert mapped_path(parent + "/torch-inductor").is_dir()
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
        imports.assert_called_once_with(require_cuda=True)
        if "voice-clone" in chosen:
            assert not (tmp_path / "volume" / "hf-cache").exists()
            assert not (tmp_path / "volume" / "torch-cache").exists()


@pytest.mark.parametrize("environment", [
    {"HF_HOME": "/workspace/another-app/hf-cache"},
    {"HF_HOME": "/tmp/hf-cache"},  # noqa: S108 -- rejected input, never written
    {"HF_HOME": "/workspace/hf-cache/../other"},
    {"HF_HOME": ""},
    {"HF_HOME": "/workspace/voice-clone/hf-cache", "HF_HUB_CACHE": "/workspace/hf-cache/hub"},
    {"HF_HOME": "/workspace/hf-cache", "HF_HUB_CACHE": "/workspace/voice-clone/hf-cache/hub"},
    {"HF_HUB_CACHE": "/tmp/hub"},  # noqa: S108 -- rejected input, never written
])
def test_unexpected_or_mismatched_cache_rejected_before_writes(environment):
    start = load_start()
    with patch.dict(os.environ, {"POD_WORKER_TOKEN": "x" * 32, **environment}, clear=True), \
            patch.object(start.Path, "mkdir") as mkdir, \
            patch.object(start, "verify_imports") as imports:
        with pytest.raises(ValueError, match="cache"):
            start.prepare()
        mkdir.assert_not_called()
        imports.assert_not_called()
