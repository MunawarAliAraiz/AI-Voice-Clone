"""Credential transport and storage checks without a container or GPU."""

import importlib.util
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
