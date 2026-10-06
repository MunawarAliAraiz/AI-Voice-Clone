"""Flex CI helpers only. No Docker, registry, provider or installed runtime."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

POD = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("vcs_flex_ci_tests", POD / "ci.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_flex_smoke_uses_ephemeral_mount_no_network_no_gpu_or_provider(monkeypatch, capsys):
    ci = load()
    calls = []
    monkeypatch.setattr(ci, "docker", lambda *args: calls.append(args) or "")
    reference = "ghcr.io/owner/worker-flex@sha256:" + "a" * 64
    ci.smoke_flex(reference)
    assert calls[0] == ("pull", reference)
    run = calls[1]
    assert run[:4] == ("run", "--rm", "--network", "none")
    assert run[run.index("--entrypoint") + 1] == "/opt/venvs/flex/bin/python"
    assert "target=/runpod-volume" in run[run.index("--mount") + 1]
    assert "--gpus" not in run and "--env" not in run
    assert reference in run and "-c" in run
    assert "verify_imports(require_cuda=False)" in run[-1]
    assert "sdk.serverless.start(" not in run[-1]
    assert "WorkerRuntime(" not in run[-1]
    assert "/workspace" not in run[-1]
    output = capsys.readouterr().out
    assert "generation, provider lifecycle and billing not tested" in output


@pytest.mark.parametrize("reference", [
    "ghcr.io/owner/flex:latest", "ghcr.io/owner/flex@sha256:abc",
    "https://private/token@sha256:" + "a" * 64,
])
def test_mutable_or_invalid_flex_smoke_rejected_before_docker(monkeypatch, reference):
    ci = load()
    calls = []
    monkeypatch.setattr(ci, "docker", lambda *args: calls.append(args))
    with pytest.raises(ValueError):
        ci.smoke_flex(reference)
    assert calls == []


def test_failed_flex_smoke_relies_on_auto_remove_without_deleting_host_paths(monkeypatch):
    ci = load()
    calls = []
    paths = []

    def docker(*args):
        calls.append(args)
        if args[0] == "run":
            mount = args[args.index("--mount") + 1]
            paths.append(Path(mount.split("source=", 1)[1].split(",target=", 1)[0]))
            assert paths[0].is_dir()
            raise ci.subprocess.CalledProcessError(1, "docker")

    monkeypatch.setattr(ci, "docker", docker)
    with pytest.raises(ci.subprocess.CalledProcessError):
        ci.smoke_flex("ghcr.io/owner/flex@sha256:" + "a" * 64)
    assert "--rm" in calls[-1] and not paths[0].exists()
    assert not any(command[0] in {"rm", "stop"} for command in calls)


def test_fixed_smoke_code_has_sdk_hooks_both_namespaces_and_no_work_calls():
    code = load().flex_smoke_code()
    compile(code, "<fixed-flex-smoke>", "exec")
    assert "inspect.iscoroutinefunction(_async_progress_update)" in code
    assert "find_spec('torch') is None" in code
    assert "version('runpod') == '1.12.0'" in code
    assert "RunPodLogger().level == 'NOTSET'" in code
    assert "/runpod-volume/hf-cache/hub" in code
    assert "/runpod-volume/voice-clone/hf-cache/hub" in code
    assert "prepare_cache_environment" in code
    assert "assert not os.environ.get('RUNPOD_WEBHOOK_GET_JOB')" in code
    assert "assert not os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT')" in code
    assert "HF_HUB_OFFLINE" in code and "TRANSFORMERS_OFFLINE" in code
    assert "require_cuda=False" in code
    for forbidden in (
        "serverless.start(", ".handle(", ".synthesize(", ".warm(",
        ".download(", "snapshot_download", "urllib.request", "uvicorn",
    ):
        assert forbidden not in code


def test_flex_name_and_hosted_disk_budget_without_shell(monkeypatch, capsys):
    ci = load()
    monkeypatch.setattr(sys, "argv", [
        "ci.py", "name", "--repository", "Owner/Studio", "--target", "flex",
    ])
    ci.main()
    assert capsys.readouterr().out.strip() == "image=ghcr.io/owner/studio-flex"
    monkeypatch.setattr(sys, "argv", ["ci.py", "disk", "--target", "flex"])
    monkeypatch.setattr(ci.shutil, "disk_usage", lambda _: SimpleNamespace(free=44 * 1024**3))
    with pytest.raises(ValueError, match="45 GiB"):
        ci.main()


def test_flex_evidence_includes_full_locks_and_never_claims_gpu_or_cost_proof(
    tmp_path, monkeypatch,
):
    ci = load()
    locks = tmp_path / "pod" / "requirements"
    locks.mkdir(parents=True)
    for name in ("api", "flex", "voxcpm", "chatterbox", "omnivoice", "text", "build"):
        (locks / f"{name}.lock").write_text("runpod==1.12.0 hash fixture")
    output = tmp_path / "release-flex.json"
    monkeypatch.setattr(ci, "ROOT", tmp_path)
    monkeypatch.setattr(ci, "published_sizes", lambda _reference: {
        "compressed_layer_bytes": 123, "expanded_disk_bytes": None,
    })
    monkeypatch.setenv("PYTHON_IMAGE", "python@sha256:" + "c" * 64)
    monkeypatch.setenv("UV_IMAGE", "uv@sha256:" + "d" * 64)
    monkeypatch.setattr(sys, "argv", [
        "ci.py", "evidence", "--image", "ghcr.io/owner/studio-flex",
        "--digest", "sha256:" + "a" * 64, "--target", "flex",
        "--source-commit", "b" * 40, "--run-url", "https://github.com/owner/studio/actions/runs/1",
        "--output", str(output),
    ])
    ci.main()
    record = json.loads(output.read_text())
    expected = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in locks.glob("*.lock")
    }
    assert record["dependency_locks"] == expected and "flex.lock" in expected
    qualification = record["qualification"]
    assert qualification["sdk_version"] == "1.12.0"
    assert qualification["network_disabled_smoke"] == "passed"
    assert qualification["cache_namespaces"] == "passed in network-disabled CPU container"
    assert qualification["spending_protection"] == "not qualified"
    for field in ("cuda", "real_generation", "listening", "billing"):
        assert qualification[field] == "not tested"
    assert qualification["sdk_lifecycle"] == "not tested on provider"
    assert qualification["provider_cleanup"] == "not tested"
    assert qualification["cpu_service"] == "not applicable"


def test_workflow_adds_flex_gate_before_evidence_without_changing_existing_gates():
    workflow = (POD.parent / ".github" / "workflows" / "pod-image.yml").read_text()
    assert "target: [installer, gpu, flex]" in workflow
    assert "if: matrix.target == 'installer'" in workflow
    assert 'python3 pod/ci.py smoke --image "$IMAGE"' in workflow
    assert "if: matrix.target == 'flex'" in workflow
    smoke = workflow.index('python3 pod/ci.py smoke-flex --image "$IMAGE"')
    evidence = workflow.index("python3 pod/ci.py evidence")
    assert smoke < evidence
    assert "platforms: linux/amd64" in workflow
    assert "target: ${{ matrix.target }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "RUNPOD_API" not in workflow and "POD_WORKER_TOKEN" not in workflow

