"""Offline SDK/lifecycle and mounted-cache checks; never invoke provider channels."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

POD = Path(__file__).resolve().parents[2] / "pod"


def load():
    name = "vcs_flex_start_tests"
    spec = importlib.util.spec_from_file_location(name, POD / "flex_start.py")
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(POD), *sys.path]):
        spec.loader.exec_module(module)
    return module


def request():
    return {
        "id": "provider-job",
        "input": {
            "operation_id": str(uuid4()),
            "operation": "models",
            "payload": {},
            "private": "must-not-reach-progress",
        },
    }


def progress(job, stage="loading_model"):
    return {
        "protocol_version": 1,
        "operation_id": job["input"]["operation_id"],
        "stage": stage,
        "model_id": "voxcpm2",
    }


class Bridge:
    def __init__(self, **kwargs):
        self.namespace = kwargs["volume_namespace"]
        self.started = self.closed = 0
        self.loops = []
        self.calls = []

    async def start(self):
        self.started += 1
        self.loops.append(asyncio.get_running_loop())

    async def handle(self, job, callback):
        self.calls.append(job)
        self.loops.append(asyncio.get_running_loop())
        await callback(progress(job))
        return {
            "protocol_version": 1,
            "operation_id": job["input"]["operation_id"],
            "status_code": 200,
            "body": {"ready": False},
        }

    async def close(self):
        self.closed += 1
        self.loops.append(asyncio.get_running_loop())


def test_sdk_owns_loop_persistent_lifespan_closes_on_shutdown(tmp_path):
    module = load()
    bridges, updates, configs = [], [], []

    def factory(**kwargs):
        bridges.append(Bridge(**kwargs))
        return bridges[-1]

    async def sender(job, update):
        updates.append((job, update))

    def start(config):
        configs.append(config)

        async def run():
            for _ in range(2):
                assert (await config["handler"](request()))["status_code"] == 200

        asyncio.run(run())  # Exact SDK1.12.0 loop ownership pattern.

    sdk = SimpleNamespace(serverless=SimpleNamespace(start=start))
    with patch.object(module, "prepare", return_value=tmp_path):
        module.main(sdk=sdk, bridge_factory=factory, progress_sender=sender)
    assert len(bridges) == 1
    assert bridges[0].started == 1 and bridges[0].closed == 1
    assert len(set(bridges[0].loops)) == 1
    assert bridges[0].namespace == tmp_path
    assert configs[0]["refresh_worker"] is False
    assert all(configs[0]["concurrency_modifier"](value) == 1 for value in [-1, 1, 100])
    assert len(updates) == 2
    assert all(job == {"id": "provider-job"} for job, _ in updates)
    assert "private" not in json.dumps(updates)


async def test_progress_rejects_unknown_model_stage_extra_fields_and_wrong_identity(tmp_path):
    module = load()
    updates = []

    class MalformedProgress(Bridge):
        async def handle(self, job, callback):
            row = progress(job)
            for bad in [
                {**row, "stage": "private-script"},
                {**row, "model_id": "/private/token"},
                {**row, "operation_id": str(uuid4())},
                {**row, "secret": "private-token"},
                {**row, "protocol_version": 2},
            ]:
                await callback(bad)
            await callback(row)
            return {"status_code": 200}

    async def sender(job, update):
        updates.append((job, update))

    runtime = module.WorkerRuntime(
        tmp_path,
        bridge_factory=MalformedProgress,
        progress_sender=sender,
    )
    assert (await runtime.handle(request()))["status_code"] == 200
    assert len(updates) == 1
    await runtime.close()
    await runtime._lifecycle_task


async def test_async_sdk_delivery_is_awaited_and_cancelled(tmp_path, monkeypatch):
    module = load()
    events = []

    class Session:
        async def __aenter__(self):
            events.append("session-open")
            return self

        async def __aexit__(self, *_exc):
            events.append("session-closed")

    async def sdk_send(session, job, update):
        events.append(("sending", job, update))
        await asyncio.Event().wait()

    monkeypatch.setitem(
        sys.modules,
        "runpod.http_client",
        SimpleNamespace(
            AsyncClientSession=Session,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "runpod.serverless.modules.rp_progress",
        SimpleNamespace(
            _async_progress_update=sdk_send,
        ),
    )
    job = request()
    task = asyncio.create_task(module.send_progress({"id": job["id"]}, progress(job)))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events[0] == "session-open" and events[-1] == "session-closed"
    assert events[1][1] == {"id": "provider-job"}


def test_logger_disabled_before_import_and_unsupported_sdk_rejected(monkeypatch):
    module = load()
    levels = []
    sdk = SimpleNamespace()

    class Logger:
        def set_level(self, value):
            levels.append(value)

    monkeypatch.setitem(sys.modules, "runpod", sdk)
    monkeypatch.setitem(
        sys.modules,
        "runpod.serverless.modules.rp_logger",
        SimpleNamespace(
            RunPodLogger=Logger,
        ),
    )
    monkeypatch.setenv("RUNPOD_LOG_LEVEL", "DEBUG")
    monkeypatch.setattr(module, "version", lambda name: "1.12.0")
    assert module.load_sdk() is sdk
    assert os.environ["RUNPOD_LOG_LEVEL"] == "NOTSET" and levels == ["NOTSET"]
    monkeypatch.setattr(module, "version", lambda name: "9.0.0")
    with pytest.raises(ValueError, match="SDK version"):
        module.load_sdk()


@pytest.mark.parametrize(
    "arguments,extra",
    [
        (["worker.py", "--rp_serve_api"], {}),
        (["worker.py", "--rp_debugger"], {}),
        (["worker.py", "--test_input", "private-script"], {}),
        (["worker.py"], {"RUNPOD_REALTIME_PORT": "8000"}),
        (["worker.py"], {"RUNPOD_WEBHOOK_GET_JOB": ""}),
        (["worker.py"], {"RUNPOD_WEBHOOK_POST_OUTPUT": ""}),
    ],
)
def test_rejects_http_debug_local_test_modes_before_storage_writes(arguments, extra):
    module = load()
    env = {
        "RUNPOD_WEBHOOK_GET_JOB": "provider-channel",
        "RUNPOD_WEBHOOK_POST_OUTPUT": "provider-output",
        **extra,
    }
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(sys, "argv", arguments),
        patch.object(module, "prepare_cache_environment") as storage,
    ):
        with pytest.raises(ValueError):
            module.prepare()
        storage.assert_not_called()


@pytest.mark.parametrize("relative", ["hf-cache", "voice-clone/hf-cache"])
def test_actual_mount_namespace_preserved_existing_weights_not_deleted(tmp_path, relative):
    module = load()
    start = sys.modules[module.prepare_cache_environment.__module__]
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    volume = tmp_path / "volume"
    model = volume / relative / "hub" / "existing-pinned-weight"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"existing-model-not-downloaded")

    def mapped(value):
        if str(value).startswith("/runpod-volume"):
            return volume / str(value).removeprefix("/runpod-volume").lstrip("/")
        return Path(value)

    home = "/runpod-volume/" + relative
    env = {
        "RUNPOD_WEBHOOK_GET_JOB": "provider-channel",
        "RUNPOD_WEBHOOK_POST_OUTPUT": "provider-output",
        "POD_WORKER_TOKEN": "legacy-secret",
        "HF_HOME": home,
        "HF_HUB_CACHE": home + "/hub",
        "TMPDIR": "/runpod-volume/tmp",
        "VCS_DATA_DIR": "/runpod-volume/audio",
    }
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(sys, "argv", ["worker.py"]),
        patch.object(start, "Path", side_effect=mapped),
        patch.object(Path, "is_mount", return_value=True),
        patch.object(start.tempfile, "mkdtemp", return_value=str(runtime)),
        patch.object(module, "verify_imports") as imports,
    ):
        namespace = module.prepare()
        assert os.environ["HF_HOME"] == home
        assert os.environ["HF_HUB_CACHE"] == home + "/hub"
        assert os.environ["HF_HUB_OFFLINE"] == os.environ["TRANSFORMERS_OFFLINE"] == "1"
        assert os.environ["VCS_MODEL_VOLUME_ROOT"] == "/runpod-volume"
        assert os.environ["VCS_DATA_DIR"] == str(runtime / "data")
        assert os.environ["TMPDIR"] == str(runtime / "tmp")
        assert start.tempfile.tempdir is None
        assert "POD_WORKER_TOKEN" not in os.environ
        assert os.environ["RUNPOD_LOG_LEVEL"] == "NOTSET"
        imports.assert_called_once_with(require_cuda=True)
        assert namespace == volume / ("voice-clone" if relative.startswith("voice-clone") else "")
    assert model.read_bytes() == b"existing-model-not-downloaded"


@pytest.mark.parametrize(
    "environment",
    [
        {"HF_HOME": "/workspace/hf-cache"},
        {"HF_HOME": "/runpod-volume/another-app/hf-cache"},
        {"HF_HOME": "/runpod-volume/hf-cache/../private"},
        {"HF_HOME": "/tmp/cache"},
        {"HF_HOME": "/runpod-volume/hf-cache", "HF_HUB_CACHE": "/runpod-volume/elsewhere"},
        {"VCS_REMOTE_WORKER_URL": "https://private/token"},
    ],
)
def test_mount_configuration_rejected_before_writes(environment):
    module = load()
    env = {
        "RUNPOD_WEBHOOK_GET_JOB": "provider-channel",
        "RUNPOD_WEBHOOK_POST_OUTPUT": "provider-output",
        **environment,
    }
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(sys, "argv", ["worker.py"]),
        patch.object(Path, "mkdir") as mkdir,
    ):
        with pytest.raises(ValueError):
            module.prepare()
        mkdir.assert_not_called()


def test_missing_actual_mount_is_rejected():
    module = load()
    env = {
        "RUNPOD_WEBHOOK_GET_JOB": "provider-channel",
        "RUNPOD_WEBHOOK_POST_OUTPUT": "provider-output",
    }
    with (
        patch.dict(os.environ, env, clear=True),
        patch.object(sys, "argv", ["worker.py"]),
        patch.object(Path, "is_mount", return_value=False),
        patch.object(Path, "mkdir") as mkdir,
    ):
        with pytest.raises(ValueError, match="persistent network volume"):
            module.prepare()
        mkdir.assert_not_called()


def test_fixed_startup_error_never_prints_exception_or_calls_sdk(capsys):
    module = load()
    sdk = SimpleNamespace(serverless=SimpleNamespace(start=lambda _: pytest.fail("SDK called")))
    with patch.object(module, "prepare", side_effect=RuntimeError("private-token secret-script")):
        with pytest.raises(SystemExit):
            module.main(sdk=sdk)
    output = capsys.readouterr()
    assert "could not start" in output.err
    assert "private" not in output.err and "secret" not in output.err


async def test_failure_envelope_preserves_valid_identity_without_exception_text(tmp_path):
    module = load()

    class Broken(Bridge):
        async def handle(self, *_args):
            raise RuntimeError("private-token user-script")

    runtime = module.WorkerRuntime(tmp_path, bridge_factory=Broken)
    job = request()
    result = await runtime.handle(job)
    assert result["status_code"] == 500
    assert result["operation_id"] == job["input"]["operation_id"]
    assert "private" not in json.dumps(result)
    await runtime.close()
    await runtime._lifecycle_task


def test_flex_lock_complete_hashes_preserves_api_pins_and_has_no_torch():
    def entries(name):
        content = (POD / "requirements" / name).read_text()
        blocks = re.split(r"\n(?=[a-zA-Z0-9_.-]+==)", content)
        result = {}
        for block in blocks:
            pin = re.search(r"^([a-zA-Z0-9_.-]+)==([^\s\\]+)", block, re.M)
            if pin:
                assert re.search(r"--hash=sha256:[a-f0-9]{64}", block)
                result[pin[1]] = pin[2]
        return result

    api, flex = entries("api.lock"), entries("flex.lock")
    assert len(flex) == 99 and flex["runpod"] == "1.12.0"
    assert all(flex[name] == pin for name, pin in api.items())
    assert not any(
        name.startswith(("torch", "nvidia-")) and name != "nvidia-ml-py" for name in flex
    )
    assert "-c api.lock" in (POD / "requirements" / "flex.in").read_text()
    source = (POD / "lock.py").read_text()
    assert "0.11.32" in source and "x86_64-manylinux_2_28" in source and '"3.12"' in source


def test_separate_image_target_and_entrypoint_do_not_start_public_listener():
    docker = (POD / "Dockerfile").read_text()
    target = docker.split("FROM gpu AS flex", 1)[1]
    assert "/opt/venvs/flex" in target and "--require-hashes" in target
    assert "HEALTHCHECK NONE" in target
    assert '"torch"' not in (POD / "flex_start.py").read_text()
    assert "uvicorn" not in (POD / "flex_start.py").read_text()
    assert "runpod==1.12.0" in (POD / "requirements" / "flex.in").read_text()
    assert "version('runpod') == '1.12.0'" in target
    assert "find_spec('torch') is None" in target
    assert "RUNPOD_LOG_LEVEL=NOTSET" in target
    assert "/runpod-volume/hf-cache" in target


async def test_provider_job_cancellation_propagates_and_lifecycle_closes(tmp_path):
    module = load()
    events = []

    class Waiting(Bridge):
        async def handle(self, job, callback):
            events.append("running")
            try:
                await asyncio.Event().wait()
            finally:
                events.append("cancel-cleanup")

    runtime = module.WorkerRuntime(tmp_path, bridge_factory=Waiting)
    task = asyncio.create_task(runtime.handle(request()))
    await asyncio.sleep(0.02)
    task.cancel()  # SDK1.12.0 JobScaler.stop_job() cancels the async handler task.
    with pytest.raises(asyncio.CancelledError):
        await task
    await runtime.close()
    await runtime._lifecycle_task
    assert events == ["running", "cancel-cleanup"]
    assert runtime._bridge.closed == 1


def test_different_sdk_loop_cannot_reuse_closed_worker(tmp_path):
    module = load()

    async def no_delivery(*_args):
        pass

    runtime = module.WorkerRuntime(
        tmp_path,
        bridge_factory=Bridge,
        progress_sender=no_delivery,
    )

    async def run():
        return await runtime.handle(request())

    assert asyncio.run(run())["status_code"] == 200
    assert runtime._bridge.closed == 1
    result = asyncio.run(run())
    assert result["status_code"] == 503
    assert result["body"]["code"] == "WORKER_UNAVAILABLE"


async def test_invalid_worker_progress_never_sends_or_exposes_input(tmp_path):
    module = load()
    updates = []

    class InvalidTypes(Bridge):
        async def handle(self, job, callback):
            row = progress(job)
            for invalid in [
                {**row, "stage": []},
                {**row, "model_id": {}},
                {**row, "protocol_version": True},
                {**row, "operation_id": "secret"},
            ]:
                await callback(invalid)
            return {"status_code": 200}

    async def sender(*values):
        updates.append(values)

    runtime = module.WorkerRuntime(tmp_path, bridge_factory=InvalidTypes, progress_sender=sender)
    assert (await runtime.handle(request()))["status_code"] == 200
    assert updates == []
    await runtime.close()
    await runtime._lifecycle_task
