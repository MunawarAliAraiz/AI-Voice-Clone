"""Actual pinned bytes, resumption, checksum adoption and CPU HTTP boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from functools import partial
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.remote_worker.installer_main import create_installer_app
from app.remote_worker.model_install import ModelInstaller, model_graph, model_pin
from app.remote_worker.model_manifest import ManifestFile, hub_manifest, transfer_file, verify_file

DATA = b"actual pinned test weights"
ENTRY = ManifestFile("weights.safetensors", len(DATA), "sha256", hashlib.sha256(DATA).hexdigest())


def snapshot(cache, repo, revision):
    return cache / ("models--" + repo.replace("/", "--")) / "snapshots" / revision


def fixture_installer(cache, monkeypatch, *, corrupt=False):
    monkeypatch.setattr(
        "app.remote_worker.model_install.shutil.disk_usage",
        lambda _: SimpleNamespace(free=200 * 1024**3),
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=b"x" * len(DATA) if corrupt else DATA)

    transfer = partial(transfer_file, transport=httpx.MockTransport(handler))
    return ModelInstaller(cache, manifest_loader=lambda *_: (ENTRY,), transfer=transfer), requests


def test_install_adopts_pinned_graph_and_rechecks_on_restart(tmp_path, monkeypatch):
    installer, requests = fixture_installer(tmp_path, monkeypatch)

    async def run():
        assert installer.start("voxcpm2")["state"] == "discovering"
        installer.start("voxcpm2")
        await installer._tasks["voxcpm2"]
        status = installer.status("voxcpm2")
        assert status["state"] == "installed"
        assert status["bytes_completed"] == status["bytes_total"] == len(DATA)
        assert status["files_verified"] == 1
        assert status["evidence"]["files"][0]["sha256"] == ENTRY.digest
        newer, reads = fixture_installer(tmp_path, monkeypatch)
        assert newer.status("voxcpm2")["state"] == "not_started"
        newer.start("voxcpm2")
        await newer._tasks["voxcpm2"]
        assert newer.status("voxcpm2")["state"] == "installed"
        assert not reads

    asyncio.run(run())
    assert len(requests) == 1


def test_complete_graph_capacity_deduplicates_snapshots_and_counts_partial_deficit(
    tmp_path, monkeypatch
):
    repo, revision = "org/model", "a" * 40
    root = snapshot(tmp_path, repo, revision)
    root.mkdir(parents=True)
    (root / (ENTRY.path + ".vcs-incomplete")).write_bytes(DATA[:8])
    monkeypatch.setattr(
        "app.remote_worker.model_install.shutil.disk_usage",
        lambda _: SimpleNamespace(free=10_000_000_000 + len(DATA) - 8),
    )
    installer = ModelInstaller(
        tmp_path, manifest_loader=lambda *_: (ENTRY,), graph=lambda _: ((repo, revision),)
    )
    result = asyncio.run(installer.capacity())
    assert result["model_files_bytes"] == len(DATA)
    assert result["partial_bytes"] == 8
    assert result["remaining_download_bytes"] == len(DATA) - 8
    assert result["missing_write_bytes"] == len(DATA) - 8
    assert result["reserve_bytes"] == 10_000_000_000
    assert result["sufficient"]
    (root / ENTRY.path).write_bytes(DATA)
    result = asyncio.run(installer.capacity())
    assert result["verified_bytes"] == len(DATA)
    assert result["missing_write_bytes"] == 0


def test_capacity_requires_all_remaining_models_plus_reserve_before_transfer(tmp_path, monkeypatch):
    installer, requests = fixture_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.remote_worker.model_install.shutil.disk_usage",
        lambda _: SimpleNamespace(free=10_000_000_000 + len(DATA)),
    )
    result = asyncio.run(installer.capacity())
    assert result["model_files_bytes"] == len(DATA) * 6
    assert not result["sufficient"]
    assert not requests


def test_full_corrupt_partial_is_repaired_instead_of_repeatedly_failing(tmp_path, monkeypatch):
    installer, requests = fixture_installer(tmp_path, monkeypatch)
    repo, revision = model_pin("voxcpm2")
    root = snapshot(tmp_path, repo, revision)
    root.mkdir(parents=True)
    (root / (ENTRY.path + ".vcs-incomplete")).write_bytes(b"x" * len(DATA))

    async def run():
        installer.start("voxcpm2")
        await installer._tasks["voxcpm2"]
        assert installer.status("voxcpm2")["state"] == "installed"

    asyncio.run(run())
    assert len(requests) == 1
    assert "Range" not in requests[0].headers
    assert (root / ENTRY.path).read_bytes() == DATA


def test_capacity_corrupt_complete_partial_counts_redownload_and_freed_temp_space(
    tmp_path, monkeypatch
):
    repo, revision = "org/model", "a" * 40
    root = snapshot(tmp_path, repo, revision)
    root.mkdir(parents=True)
    (root / (ENTRY.path + ".vcs-incomplete")).write_bytes(b"x" * len(DATA))
    monkeypatch.setattr(
        "app.remote_worker.model_install.shutil.disk_usage",
        lambda _: SimpleNamespace(free=10_000_000_000),
    )
    installer = ModelInstaller(
        tmp_path, manifest_loader=lambda *_: (ENTRY,), graph=lambda _: ((repo, revision),)
    )
    result = asyncio.run(installer.capacity())
    assert result["partial_bytes"] == 0
    assert result["remaining_download_bytes"] == len(DATA)
    assert result["missing_write_bytes"] == 0
    assert result["sufficient"]


def test_corrupt_download_is_not_ready_or_published(tmp_path, monkeypatch):
    installer, _ = fixture_installer(tmp_path, monkeypatch, corrupt=True)

    async def run():
        installer.start("voxcpm2")
        await installer._tasks["voxcpm2"]
        assert installer.status("voxcpm2")["state"] == "failed"

    asyncio.run(run())
    repo, rev = model_pin("voxcpm2")
    assert not (snapshot(tmp_path, repo, rev) / ENTRY.path).exists()
    assert not list(tmp_path.glob(".vcs-installed/*.json"))


def test_existing_corrupt_weights_are_repaired(tmp_path, monkeypatch):
    repo, rev = model_pin("voxcpm2")
    folder = snapshot(tmp_path, repo, rev)
    folder.mkdir(parents=True)
    (folder / ENTRY.path).write_bytes(b"x" * len(DATA))
    installer, requests = fixture_installer(tmp_path, monkeypatch)

    async def run():
        installer.start("voxcpm2")
        await installer._tasks["voxcpm2"]
        assert installer.status("voxcpm2")["state"] == "installed"

    asyncio.run(run())
    assert len(requests) == 1
    assert (folder / ENTRY.path).read_bytes() == DATA


def test_omnivoice_requires_whisper_graph(tmp_path, monkeypatch):
    installer, requests = fixture_installer(tmp_path, monkeypatch)

    async def run():
        installer.start("omnivoice_urdu")
        await installer._tasks["omnivoice_urdu"]
        status = installer.status("omnivoice_urdu")
        assert status["state"] == "installed"
        assert status["files_verified"] == 2
        assert {x["repo"] for x in status["evidence"]["files"]} == {
            "k2-fsa/OmniVoice",
            "openai/whisper-large-v3-turbo",
        }

    asyncio.run(run())
    assert len(requests) == 2
    assert len(model_graph("omnivoice_urdu")) == 2


def test_old_marker_cannot_claim_ready(tmp_path):
    repo, rev = model_pin("voxcpm2")
    snapshot(tmp_path, repo, rev).mkdir(parents=True)
    marker = tmp_path / ".vcs-installed" / f"voxcpm2-{rev}.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps({"revision": rev}))
    assert ModelInstaller(tmp_path).status("voxcpm2")["state"] == "not_started"


def test_unknown_and_unsupported_model(tmp_path):
    installer = ModelInstaller(tmp_path)
    with pytest.raises(KeyError):
        installer.start("unregistered")
    assert installer.start("f5_openbible_urdu")["state"] == "unsupported"


def test_range_download_resumes_and_verifies(tmp_path):
    partial_file = tmp_path / (ENTRY.path + ".vcs-incomplete")
    partial_file.write_bytes(DATA[:5])
    requests, progress = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            206, content=DATA[5:], headers={"Content-Range": f"bytes 5-{len(DATA) - 1}/{len(DATA)}"}
        )

    digest = transfer_file(
        "owner/repo",
        "a" * 40,
        ENTRY,
        tmp_path,
        progress=progress.append,
        cancel=threading.Event(),
        transport=httpx.MockTransport(handler),
    )
    assert requests[0].headers["range"] == "bytes=5-"
    assert progress[0] == 5 and progress[-1] == len(DATA)
    assert digest == ENTRY.digest and (tmp_path / ENTRY.path).read_bytes() == DATA


def test_cancelled_transfer_keeps_partial_bytes_and_retry_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr("app.remote_worker.model_manifest.CHUNK_BYTES", 8)
    cancel = threading.Event()

    def progress(amount):
        if amount >= 8:
            cancel.set()

    with pytest.raises(InterruptedError):
        transfer_file(
            "owner/repo",
            "a" * 40,
            ENTRY,
            tmp_path,
            progress=progress,
            cancel=cancel,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=DATA)),
        )
    partial_file = tmp_path / (ENTRY.path + ".vcs-incomplete")
    assert partial_file.read_bytes() == DATA[:8]
    assert not (tmp_path / ENTRY.path).exists()
    requests = []

    def resumed(request):
        requests.append(request)
        return httpx.Response(
            206, content=DATA[8:], headers={"Content-Range": f"bytes 8-{len(DATA) - 1}/{len(DATA)}"}
        )

    digest = transfer_file(
        "owner/repo",
        "a" * 40,
        ENTRY,
        tmp_path,
        progress=lambda _: None,
        cancel=threading.Event(),
        transport=httpx.MockTransport(resumed),
    )
    assert requests[0].headers["range"] == "bytes=8-"
    assert digest == ENTRY.digest
    assert (tmp_path / ENTRY.path).read_bytes() == DATA
    assert not partial_file.exists()


def test_ignored_range_restarts_cleanly(tmp_path):
    (tmp_path / (ENTRY.path + ".vcs-incomplete")).write_bytes(DATA[:4])
    transfer_file(
        "owner/repo",
        "a" * 40,
        ENTRY,
        tmp_path,
        progress=lambda _: None,
        cancel=threading.Event(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=DATA)),
    )
    assert (tmp_path / ENTRY.path).read_bytes() == DATA


def test_invalid_range_does_not_publish(tmp_path):
    (tmp_path / (ENTRY.path + ".vcs-incomplete")).write_bytes(DATA[:4])
    with pytest.raises(ValueError, match="resumed"):
        transfer_file(
            "owner/repo",
            "a" * 40,
            ENTRY,
            tmp_path,
            progress=lambda _: None,
            cancel=threading.Event(),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    206, content=DATA, headers={"Content-Range": "bytes 0-1/2"}
                )
            ),
        )
    assert not (tmp_path / ENTRY.path).exists()


def test_git_blob_checksum_is_verified(tmp_path):
    entry = ManifestFile(
        "config.json",
        2,
        "git-blob-sha1",
        hashlib.sha1(b"blob 2\0{}", usedforsecurity=False).hexdigest(),
    )
    path = tmp_path / entry.path
    path.write_bytes(b"{}")
    assert verify_file(path, entry) == hashlib.sha256(b"{}").hexdigest()


@pytest.mark.parametrize("path", ["../escape", "/abs", "a/../../escape", "a\\evil", "./foo"])
def test_manifest_rejects_unsafe_paths(path):
    with pytest.raises(ValueError):
        ManifestFile(path, 1, "sha256", "a" * 64)


def test_manifest_metadata_pagination_and_pins():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "type": "file",
                    "path": ENTRY.path,
                    "size": len(DATA),
                    "lfs": {"oid": ENTRY.digest},
                }
            ],
        )

    assert hub_manifest("owner/repo", "a" * 40, transport=httpx.MockTransport(handler)) == (ENTRY,)
    assert "/tree/" + "a" * 40 in seen[0].url.path
    with pytest.raises(ValueError):
        hub_manifest("owner/repo", "main")


def test_no_token_forwarded_to_download_redirect(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_TOKEN", "private-token")
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(302, headers={"Location": "https://cdn.example/weights"})
        return httpx.Response(200, content=DATA)

    transfer_file(
        "owner/repo",
        "a" * 40,
        ENTRY,
        tmp_path,
        progress=lambda _: None,
        cancel=threading.Event(),
        transport=httpx.MockTransport(handler),
    )
    assert seen[0].headers["authorization"] == "Bearer private-token"
    assert "authorization" not in seen[1].headers


def test_cpu_service_requires_auth_and_mounted_storage(tmp_path):
    app = create_installer_app(token="internal", cache_dir=tmp_path / "hub", volume_root=tmp_path)
    with TestClient(app) as client:
        assert client.get("/v1/health").status_code == 401
        headers = {"Authorization": "Bearer internal"}
        assert client.get("/v1/health", headers=headers).json()["role"] == "model_installer"
        status = client.get("/v1/setup", headers=headers).json()
        assert not status["ready"] and status["progress_pct"] is None
        assert len(status["manifest_id"]) == 64
        assert client.get("/v1/models/bad/install", headers=headers).status_code == 404
    with pytest.raises(ValueError, match="volume"):
        create_installer_app(
            token="internal", cache_dir=tmp_path.parent / "outside", volume_root=tmp_path
        )


def test_cpu_capacity_is_async_authenticated_and_marker_is_not_readiness(tmp_path, monkeypatch):
    import time

    installer, requests = fixture_installer(tmp_path / "hub", monkeypatch)
    app = create_installer_app(token="internal", cache_dir=tmp_path / "hub",
                               volume_root=tmp_path, installer=installer)
    headers = {"Authorization": "Bearer internal"}
    with TestClient(app) as client:
        assert client.post("/v1/capacity").status_code == 401
        assert client.post("/v1/setup", headers=headers).status_code == 409
        started = client.post("/v1/capacity", headers=headers)
        assert started.status_code == 202
        assert started.json()["state"] == "checking"
        for _ in range(100):
            result = client.get("/v1/capacity", headers=headers).json()
            if result["state"] == "complete":
                break
            time.sleep(0.01)
        assert result["state"] == "complete"
        assert result["sufficient"]
        assert result["app_owned"]
        assert not client.get("/v1/setup", headers=headers).json()["ready"]
        assert not requests
        marker = json.loads((tmp_path / "hub" / ".vcs-volume.json").read_text())
        assert marker["app_id"] == "studio.voiceclone.desktop"
        marker["app_id"] = "video-app"
        (tmp_path / "hub" / ".vcs-volume.json").write_text(json.dumps(marker))
        assert client.post("/v1/capacity", headers=headers).status_code == 409


def test_shutdown_joins_cancelled_capacity_thread_before_return(tmp_path, monkeypatch):
    entered, stopped = threading.Event(), threading.Event()
    installer, _ = fixture_installer(tmp_path, monkeypatch)

    def scan(cancel):
        entered.set()
        assert cancel.wait(timeout=2)
        stopped.set()
        raise InterruptedError("Stopped")

    monkeypatch.setattr(installer, "_capacity_sync", scan)

    async def run():
        installer.start_capacity()
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        await installer.shutdown()
        assert stopped.is_set()
        assert installer._capacity_task.done()

    asyncio.run(run())


def test_arabic_alias_reuses_verified_base_without_extra_transfer(tmp_path, monkeypatch):
    installer, requests = fixture_installer(tmp_path, monkeypatch)

    async def run():
        installer.start("voxcpm2")
        await installer._tasks["voxcpm2"]
        alias = installer.start("voxcpm2_urdu_arabic")
        assert alias["state"] == "installed"
        assert alias["alias_of"] == "voxcpm2"
        assert alias["evidence"]["model_id"] == "voxcpm2_urdu_arabic"
        assert installer.status("voxcpm2")["evidence"]["model_id"] == "voxcpm2"
        assert "voxcpm2_urdu_arabic" not in installer._tasks

    asyncio.run(run())
    assert len(requests) == 1


def test_retry_resets_stale_progress(tmp_path, monkeypatch):
    installer, _ = fixture_installer(tmp_path, monkeypatch)
    installer._states["voxcpm2"] = {
        "state": "failed",
        "revision": model_pin("voxcpm2")[1],
        "bytes_total": 20,
        "bytes_completed": 20,
        "files_total": 3,
        "files_verified": 3,
        "progress_pct": 100,
        "current_file": "stale",
        "evidence": {"old": True},
    }

    async def run():
        state = installer.start("voxcpm2")
        assert state["bytes_total"] is None and state["bytes_completed"] == 0
        assert state["current_file"] is None and "evidence" not in state
        await installer._tasks["voxcpm2"]

    asyncio.run(run())


def test_insufficient_storage_fails_before_download(tmp_path, monkeypatch):
    installer, requests = fixture_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.remote_worker.model_install.shutil.disk_usage", lambda _: SimpleNamespace(free=0)
    )

    async def run():
        installer.start("voxcpm2")
        await installer._tasks["voxcpm2"]
        assert installer.status("voxcpm2")["state"] == "failed"

    asyncio.run(run())
    assert not requests


def test_external_cancellation_joins_writer_before_unlock(tmp_path, monkeypatch):
    installer, _ = fixture_installer(tmp_path, monkeypatch)
    started, finished = threading.Event(), threading.Event()

    def transfer(*args, cancel, **kwargs):
        started.set()
        assert cancel.wait(2), "Writer cancellation was not signalled"
        finished.set()
        raise InterruptedError("cancelled")

    installer.transfer = transfer

    async def run():
        installer.start("voxcpm2")
        assert await asyncio.to_thread(started.wait, 2)
        installer._tasks["voxcpm2"].cancel()
        with pytest.raises(asyncio.CancelledError):
            await installer._tasks["voxcpm2"]
        assert finished.is_set()
        assert not installer._download_lock.locked()
        assert installer.status("voxcpm2")["state"] == "failed"

    asyncio.run(run())


def test_metadata_atomic_write_does_not_follow_destination_symlink(tmp_path):
    from app.remote_worker.model_manifest import atomic_json

    outside = tmp_path / "untouched.json"
    outside.write_text("original")
    link = tmp_path / "metadata.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Filesystem does not allow symlink creation")
    atomic_json(link, {"safe": True})
    assert outside.read_text() == "original"
    assert not link.is_symlink()
    assert json.loads(link.read_text()) == {"safe": True}


def test_snapshot_parent_symlink_escape_is_rejected(tmp_path, monkeypatch):
    installer, requests = fixture_installer(tmp_path / "cache", monkeypatch)
    repo, _revision = model_pin("voxcpm2")
    root = tmp_path / "cache" / ("models--" + repo.replace("/", "--"))
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "snapshots").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Filesystem does not allow symlink creation")

    async def run():
        installer.start("voxcpm2")
        await installer._tasks["voxcpm2"]
        assert installer.status("voxcpm2")["state"] == "failed"

    asyncio.run(run())
    assert not list(outside.iterdir()) and not requests
