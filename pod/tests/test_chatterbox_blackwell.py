"""Offline dependency and preflight gates; never claim real CUDA generation."""

import hashlib
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

POD = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(f"pod_{name}", POD / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blocks(name):
    text = (POD / "requirements" / f"{name}.lock").read_text()
    return dict(re.findall(r"^([a-z0-9-]+)==([^\n]+(?:\n[ \t]+[^\n]+)*)", text, re.MULTILINE))


def test_chatterbox_keeps_model_version_and_has_matching_hashed_cuda128_pair():
    requirements = (POD / "requirements" / "chatterbox.in").read_text()
    overrides = (POD / "requirements" / "chatterbox.overrides.in").read_text()
    locked = blocks("chatterbox")
    assert "chatterbox-tts==0.1.7" in requirements
    assert locked["chatterbox-tts"].startswith("0.1.7 \\")
    for package in ("torch", "torchaudio"):
        assert f"{package}==2.8.0+cu128" in requirements
        assert f"{package}==2.8.0+cu128" in overrides
        assert locked[package].startswith("2.8.0+cu128 \\")
        assert locked[package] == blocks("voxcpm")[package]
    assert len(locked) == 125
    assert all(re.search(r"--hash=sha256:[a-f0-9]{64}", block) for block in locked.values())


def test_unrelated_dependency_versions_are_preserved():
    locked = blocks("chatterbox")
    unrelated = {
        package: block.split()[0]
        for package, block in locked.items()
        if not package.startswith("nvidia-")
        and package not in {"torch", "torchaudio", "triton", "sympy"}
    }
    # Fingerprint of the original CUDA12.4 lock's107 unrelated version pins.
    version_list = "\n".join(f"{k}=={v}" for k, v in sorted(unrelated.items()))
    assert len(unrelated) == 107
    assert hashlib.sha256(version_list.encode()).hexdigest() == (
        "15ef83e0f1562f3581a38b70eb3e7159bd70fc839f3943956b99869edc0675d5"
    )


def test_lock_script_applies_override_only_to_chatterbox(monkeypatch):
    lock = load("lock")
    commands = []
    monkeypatch.setattr(lock.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(lock.subprocess, "check_output", lambda *a, **k: "uv 0.11.32 fixture")
    monkeypatch.setattr(lock.subprocess, "run", lambda command, **kwargs: commands.append(command))
    lock.main()
    assert len(commands) == 6
    for command in commands:
        assert "--python-version" in command and "3.12" in command
        assert "--python-platform" in command and "x86_64-manylinux_2_28" in command
        assert "--no-python-downloads" in command and "--generate-hashes" in command
        if any(item.endswith("chatterbox.in") for item in command):
            assert command[command.index("--override") + 1].endswith("chatterbox.overrides.in")
            assert command[command.index("--torch-backend") + 1] == "cu128"
        else:
            assert "--override" not in command


def test_image_sync_uses_hashed_lock_without_a_metadata_reresolution():
    dockerfile = (POD / "Dockerfile").read_text()
    command = next(line for line in dockerfile.splitlines() if "chatterbox.lock" in line)
    assert "uv pip sync" in command and "--require-hashes" in command
    assert "--torch-backend cu128" in command and "--only-binary :all:" in command
    assert "--no-deps" not in command


def _torch(monkeypatch, *, available=True, capability=(12, 0), cuda="12.8", fails=False):
    calls = []
    module = ModuleType("torch")
    module.version = SimpleNamespace(cuda=cuda)

    class Probe:
        def __add__(self, other):
            calls.append("kernel")
            if fails:
                raise RuntimeError("no kernel image is available for execution on the device")
            return self

        def sum(self):
            return self

        def item(self):
            return 4.0

    def allocate(size, *, device):
        calls.append(("allocate", size, device))
        return Probe()

    module.cuda = SimpleNamespace(
        is_available=lambda: available,
        get_device_capability=lambda: capability,
        synchronize=lambda: calls.append("synchronize"),
    )
    module.ones = allocate
    monkeypatch.setitem(sys.modules, "torch", module)
    return calls


def test_import_only_preflight_never_allocates_gpu(monkeypatch):
    preflight = load("preflight")
    calls = _torch(monkeypatch)
    code = preflight.child_check_code("pass", require_cuda=False)
    exec(code, {})  # noqa: S102 -- fixed repository preflight code, no external input
    assert calls == []


def test_device_discovery_is_followed_by_a_kernel_and_synchronization(monkeypatch):
    preflight = load("preflight")
    calls = _torch(monkeypatch)
    exec(preflight.child_check_code("pass", require_cuda=True), {})  # noqa: S102
    assert calls == [("allocate", 2, "cuda"), "kernel", "synchronize"]


@pytest.mark.parametrize("config,exception,reason", [
    ({"available": False}, AssertionError, "CUDA unavailable"),
    ({"cuda": "12.4"}, AssertionError, "Blackwell requires"),
    ({"fails": True}, RuntimeError, "no kernel image"),
])
def test_unsupported_runtime_is_refused_before_weights(monkeypatch, config, exception, reason):
    preflight = load("preflight")
    _torch(monkeypatch, **config)
    with pytest.raises(exception, match=reason):
        exec(preflight.child_check_code("pass", require_cuda=True), {})  # noqa: S102


def test_preflight_failure_does_not_publish_dependency_logs(monkeypatch):
    preflight = load("preflight")
    for _, setting, _ in preflight.RUNTIMES:
        monkeypatch.setenv(setting, "python")
    monkeypatch.setattr(preflight.importlib.util, "find_spec", lambda _: None)
    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="private path", stderr="secret model access URL",
    ))
    with pytest.raises(ValueError) as failure:
        preflight.verify_imports(require_cuda=True)
    assert "import/CUDA check failed" in str(failure.value)
    assert "private" not in str(failure.value) and "secret" not in str(failure.value)
