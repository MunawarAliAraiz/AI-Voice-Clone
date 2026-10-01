"""Credential transport and storage checks without a container or GPU."""

import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

POD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POD))


def load(name):
    spec = importlib.util.spec_from_file_location(name, POD / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_requires_immutable_image():
    build = load("build")
    digest = "python:3.12.11-slim-bookworm@sha256:" + "a" * 64
    assert build.digest_ref(digest) == digest
    with pytest.raises(Exception, match="sha256 digest"):
        build.digest_ref("python:latest")


def test_start_rejects_remote_scheduler_and_missing_mount():
    start = load("start")
    with patch.dict("os.environ", {"POD_WORKER_TOKEN": "x" * 32,
                                   "VCS_REMOTE_WORKER_URL": "https://worker.example"}, clear=True):
        with pytest.raises(ValueError, match="remote scheduler"):
            start.prepare()
    with patch.dict("os.environ", {"POD_WORKER_TOKEN": "x" * 32}, clear=True):
        with patch.object(start.Path, "is_mount", return_value=False):
            with pytest.raises(ValueError, match="persistent network volume"):
                start.prepare()


def test_start_moves_legacy_user_data_off_persistent_volume(tmp_path):
    start = load("start")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    original_path = Path

    def mapped_path(value):
        path = original_path(value)
        if str(value).startswith("/workspace"):
            return tmp_path / "volume" / str(value).removeprefix("/workspace").lstrip("/")
        return path

    environment = {"POD_WORKER_TOKEN": "x" * 32, "TMPDIR": "/workspace/tmp",
                   "VCS_DATA_DIR": "/workspace/runtime", "VCS_ALLOW_FAKE_RUNTIME": "true"}
    with patch.dict("os.environ", environment, clear=True), \
            patch.object(start, "Path", side_effect=mapped_path), \
            patch.object(Path, "is_mount", return_value=True), \
            patch.object(start.tempfile, "mkdtemp", return_value=str(runtime)), \
            patch.object(start, "verify_imports") as imports:
        start.prepare()
        assert os.environ["TMPDIR"] == str(runtime / "tmp")
        assert os.environ["VCS_DATA_DIR"] == str(runtime / "data")
        assert os.environ["HF_TOKEN_PATH"] == str(runtime / "hf-token")
        assert os.environ["HF_HOME"] == "/workspace/hf-cache"
        assert os.environ["VCS_ALLOW_FAKE_RUNTIME"] == "false"
        imports.assert_called_once_with(require_cuda=True)


def test_health_probe_refuses_redirect():
    health = load("healthcheck")
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/credential-target")
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_request = health.urllib.request.Request
    def request(*_, **kwargs):
        return original_request(f"http://127.0.0.1:{server.server_port}/v1/health", **kwargs)
    try:
        with patch.dict("os.environ", {"POD_WORKER_TOKEN": "x" * 32}), \
                patch.object(health.urllib.request, "Request", side_effect=request):
            assert health.main() == 1
        assert calls == ["/v1/health"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_release_size_ignores_attestation_manifests():
    ci = load("ci")
    reference = "ghcr.io/owner/worker@sha256:" + "a" * 64
    index = {"manifests": [
        {"digest": "sha256:" + "b" * 64,
         "platform": {"os": "linux", "architecture": "amd64"}},
        {"digest": "sha256:" + "c" * 64,
         "platform": {"os": "unknown", "architecture": "unknown"}},
    ]}
    manifest = {"layers": [{"size": 10}, {"size": 20}]}
    with patch.object(ci, "docker", side_effect=[json.dumps(index), json.dumps(manifest)]):
        result = ci.published_sizes(reference)
    assert result["compressed_layer_bytes"] == 30
    assert result["platform_manifest_digest"] == "sha256:" + "b" * 64
    assert result["expanded_disk_bytes"] is None


def test_release_rejects_ambiguous_platform_and_mutable_reference():
    ci = load("ci")
    with pytest.raises(ValueError, match="immutable"):
        ci.immutable_image("ghcr.io/owner/worker", "latest")
    with patch.object(ci, "docker", return_value=json.dumps({"manifests": []})):
        with pytest.raises(ValueError, match="one linux/amd64"):
            ci.published_sizes("ghcr.io/owner/worker@sha256:" + "a" * 64)


def test_installer_smoke_restores_environment_and_passes_secret_without_argv():
    ci = load("ci")
    calls = []

    def fake_docker(*args):
        calls.append(args)
        return "container-id\n" if args[0] == "run" else ""

    with patch.dict(os.environ, {"POD_WORKER_TOKEN": "previous-secret"}), \
            patch.object(ci, "docker", side_effect=fake_docker):
        ci.smoke("ghcr.io/owner/installer@sha256:" + "a" * 64)
        assert os.environ["POD_WORKER_TOKEN"] == "previous-secret"  # noqa: S105 -- fixture
    run = next(args for args in calls if args[0] == "run")
    assert run[run.index("--env") + 1] == "POD_WORKER_TOKEN"
    assert not any("previous-secret" in item for args in calls for item in args)
    assert ("rm", "--force", "container-id") in calls


def test_installer_smoke_cleans_failed_container():
    ci = load("ci")
    calls = []

    def fake_docker(*args):
        calls.append(args)
        if args[0] == "run":
            return "container-id\n"
        if args[:2] == ("exec", "container-id"):
            raise ci.subprocess.CalledProcessError(1, "docker")
        return ""

    with patch.object(ci, "docker", side_effect=fake_docker), \
            patch.object(ci.time, "sleep"):
        with pytest.raises(ValueError, match="did not become healthy"):
            ci.smoke("ghcr.io/owner/installer@sha256:" + "a" * 64)
    assert ("rm", "--force", "container-id") in calls
