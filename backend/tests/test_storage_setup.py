"""Full setup orchestration on tiny fake pinned files; no live provider calls."""

from __future__ import annotations

import asyncio
import hashlib
import json
from html import escape
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.inference.remote_scheduler import RemoteWorkerError
from app.remote_worker.model_install import REQUIRED_MODEL_IDS, model_graph, release_manifest_id
from app.runpod import controller as cloud_module
from app.runpod import storage_setup as module
from app.runpod.controller import CloudController, verified_models
from app.runpod.storage_access import StorageBinding, StorageCredentials
from app.runpod.storage_setup import StorageModelSetup, confirmed_setup_proof
from app.runpod.storage_transfer import StorageTransferError
from tests.test_storage_transfer import DATA, S3, download, entry


class StorageServer(S3):
    def __init__(self):
        super().__init__()
        self.bad_metadata = False
        self.foreign_upload = False
        self.empty_sentinels = False
        self.leading_keys = False

    def __call__(self, request):
        key = request.url.path.removeprefix("/vol_test").lstrip("/")
        query = dict(request.url.params)
        if request.method == "GET" and "list-type" in query:
            self.calls.append((request.method, key, query, request.content))
            rows = "".join(
                f"<Contents><Key>{escape('/' + k if self.leading_keys else k)}</Key>"
                f"<Size>{len(v)}</Size></Contents>"
                for k, v in self.objects.items()
            )
            if self.empty_sentinels and not rows:
                rows = "<Contents/>"
            return httpx.Response(
                200,
                text=f"<ListBucketResult><Name>vol_test</Name><IsTruncated>false</IsTruncated>{rows}</ListBucketResult>",
            )
        if request.method == "GET" and "uploads" in query:
            self.calls.append((request.method, key, query, request.content))
            rows = (
                "<Upload><Key>other/file.bin</Key><UploadId>foreign</UploadId></Upload>"
                if self.foreign_upload
                else ""
            )
            if self.empty_sentinels and not rows:
                rows = "<Upload/>"
            return httpx.Response(
                200,
                text=f"<ListMultipartUploadsResult><Bucket>vol_test</Bucket><IsTruncated>false</IsTruncated>{rows}</ListMultipartUploadsResult>",
            )
        if request.method == "GET" and key not in self.objects and "uploadId" not in query:
            self.calls.append((request.method, key, query, request.content))
            return httpx.Response(404)
        if self.bad_metadata and request.method == "GET" and key.endswith(".vcs-manifest.json"):
            self.calls.append((request.method, key, query, request.content))
            return httpx.Response(200, content=b"corrupt")
        return super().__call__(request)


def binding(**changes):
    return StorageBinding("a" * 64, "vol_test", "US-NE-1", **changes)


def setup(tmp_path, server, *, size=60, qualified=True, downloader=download, **kwargs):
    return StorageModelSetup(
        StorageCredentials("user_testing", "rps_test_secret"),
        binding(),
        tmp_path,
        volume_size_gb=size,
        qualified=lambda selected: qualified,
        transport=httpx.MockTransport(server),
        manifest_loader=lambda repo, rev: (entry(),),
        downloader=downloader,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_full_graph_metadata_published_only_after_hashes_and_read_back(tmp_path):
    server = StorageServer()
    manager = setup(tmp_path, server)
    try:
        result = await manager.run_setup()
        assert result["files_ready"] and verified_models(result)
        keys = set(server.objects)
        for model_id in REQUIRED_MODEL_IDS:
            for repo, rev in model_graph(model_id):
                folder = "hf-cache/hub/models--" + repo.replace("/", "--")
                assert server.objects[folder + "/refs/main"] == rev.encode()
                assert folder + "/snapshots/" + rev + "/.vcs-manifest.json" in keys
        first_meta = next(
            i
            for i, (method, key, _, _) in enumerate(server.calls)
            if method == "PUT" and (key.endswith("/refs/main") or key.endswith(".json"))
        )
        assert all(
            key.endswith("model.bin")
            for method, key, _, _ in server.calls[:first_meta]
            if method == "PUT"
        )
        state = {
            "models": result["models"],
            "storage_proof_hash": hashlib.sha256(
                json.dumps(result["proof"], sort_keys=True).encode()
            ).hexdigest(),
        }
        assert confirmed_setup_proof(tmp_path, binding(), state)
        assert not confirmed_setup_proof(
            tmp_path, binding(prefix="voice-clone/hf-cache/hub/"), state
        )
        state["storage_proof_hash"] = "fake"
        assert not confirmed_setup_proof(tmp_path, binding(), state)
        assert result["proof"]["mounted_files_verified"] is False
        assert not list(manager.root.rglob("model.bin"))
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_unqualified_setup_never_contacts_storage_or_downloads(tmp_path):
    server = StorageServer()
    manager = setup(tmp_path, server, qualified=False)
    try:
        with pytest.raises(StorageTransferError, match="compatibility check"):
            await manager.run_setup()
        assert server.calls == [] and not manager.status()["files_ready"]
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["checksum", "metadata", "capacity", "foreign_upload", "foreign_identity"]
)
async def test_failures_never_publish_verified_setup_proof(tmp_path, failure):
    server = StorageServer()
    size = 60
    if failure == "metadata":
        server.bad_metadata = True
    if failure == "capacity":
        size = 1
    if failure == "foreign_upload":
        server.foreign_upload = True
    if failure == "foreign_identity":
        server.objects["hf-cache/hub/.vcs-volume.json"] = b'{"app_id":"other"}'

    def bad_download(repo, rev, spec, stage, **kwargs):
        (stage / spec.path).write_bytes(b"x" * len(DATA))

    manager = setup(
        tmp_path, server, size=size, downloader=bad_download if failure == "checksum" else download
    )
    try:
        with pytest.raises(StorageTransferError):
            await manager.run_setup()
        assert not manager.status()["files_ready"]
        assert not (manager.root / "setup-proof.json").exists()
        assert not any(".vcs-installed" in key for key in server.objects)
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_reopen_rechecks_existing_model_bytes_without_model_upload(tmp_path):
    server = StorageServer()
    manager = setup(tmp_path, server)
    await manager.run_setup()
    await manager.close()
    before = len(server.calls)
    manager = setup(tmp_path, server)
    try:
        assert not manager.status()["files_ready"]
        await manager.run_setup()
        assert not any(
            method == "PUT" and key.endswith("model.bin")
            for method, key, _, _ in server.calls[before:]
        )
        assert any(
            method == "GET" and key.endswith("model.bin")
            for method, key, _, _ in server.calls[before:]
        )
    finally:
        await manager.close()


def test_qualification_is_region_and_code_bound_not_account(monkeypatch):
    receipt = {
        "version": 1,
        "region": "US-NE-1",
        "protocol_version": "s3-pinned-relay-v1",
        "transfer_code_sha256": "c" * 64,
        "graph_manifest_id": release_manifest_id(),
        "put_readback_verified": True,
        "multipart_readback_verified": True,
        "owned_cleanup_verified": True,
    }

    class FakePath:
        def with_name(self, name):
            return self

        def is_file(self):
            return True

        def is_symlink(self):
            return False

        def stat(self):
            return SimpleNamespace(st_size=1000)

        def read_text(self, **kwargs):
            return json.dumps(receipt)

    monkeypatch.setattr(module, "Path", lambda *args: FakePath())
    monkeypatch.setattr(module, "transfer_code_sha256", lambda: "c" * 64)
    assert module.storage_writes_qualified(binding())
    assert module.storage_writes_qualified(StorageBinding("b" * 64, "another_volume", "US-NE-1"))
    assert not module.storage_writes_qualified(
        StorageBinding("b" * 64, "another_volume", "US-CA-2")
    )
    receipt["transfer_code_sha256"] = "d" * 64
    assert not module.storage_writes_qualified(binding())


@pytest.mark.asyncio
async def test_controller_s3_path_never_provisions_pod_and_consumes_proof(tmp_path, monkeypatch):
    server = StorageServer()
    settings = Settings(data_dir=tmp_path, desktop_static_dir=tmp_path)

    class Keys:
        def __init__(self, data_dir):
            pass

        def get_key(self):
            return "dummy_management_key"

        def has_key(self):
            return True

    monkeypatch.setattr(cloud_module, "RunpodKeyStore", Keys)
    monkeypatch.setattr(cloud_module, "storage_writes_qualified", lambda selected: True)
    manager = setup(tmp_path, server)
    cloud = CloudController(settings, storage_setup_factory=lambda *args, **kwargs: manager)
    selected = {"id": "vol_test", "size": 60, "dataCenter": "US-NE-1", "cache_namespace": "legacy"}
    account = hashlib.sha256(b"dummy_management_key").hexdigest()
    cloud.write({"volume": selected, "account": account})
    # Bind test manager to the saved account; no normal profile is read or edited.
    manager.binding = StorageBinding(account, "vol_test", "US-NE-1")
    monkeypatch.setattr(
        cloud, "_storage_credentials", lambda: (manager.credentials, manager.binding)
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("S3 setup must not rent a Pod")

    monkeypatch.setattr(cloud, "_provision", forbidden)
    await cloud.resume_auto_setup()
    await cloud.setup_task
    snapshot = await cloud.snapshot()
    assert snapshot["files_ready"] and snapshot["ready"]
    assert snapshot["generation_ready"] is False and snapshot["compute_start_blocked_reason"]
    assert snapshot["setup_transport"] == "s3" and not snapshot["compute"]
    cloud.mark_files_invalid()
    assert not cloud.is_ready(cloud.read()) and cloud.read()["volume"] == selected
    assert cloud.read()["models_verified"] is False and cloud.read()["requires_model_repair"]


@pytest.mark.asyncio
async def test_cache_missing_revokes_ready_releases_without_idle_or_retry(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, desktop_static_dir=tmp_path)
    cloud = CloudController(settings)
    selected = {"id": "vol_test", "size": 200}
    cloud.write(
        {
            "volume": selected,
            "ready": True,
            "models_verified": True,
            "compute": {"kind": "generation"},
        }
    )
    monkeypatch.setattr(cloud, "is_ready", lambda state: state.get("ready") is True)
    pair = SimpleNamespace(url="https://worker.example", token="dummy")
    monkeypatch.setattr(
        cloud_module, "WorkerPairStore", lambda data_dir: SimpleNamespace(get=lambda: pair)
    )
    closed, released = [], []

    class Remote:
        async def shutdown(self):
            closed.append(True)

    cloud.remote_factory = lambda *args: Remote()

    async def release():
        assert cloud.active == 0
        released.append(True)
        state = cloud.read()
        state.pop("compute", None)
        cloud.write(state)

    async def forbidden():
        pytest.fail("Missing model cache must not wait60seconds or rerent")

    monkeypatch.setattr(cloud, "_release", release)
    monkeypatch.setattr(cloud, "_idle_release", forbidden)
    with pytest.raises(RemoteWorkerError) as raised:
        async with cloud.session():
            raise RemoteWorkerError("MODEL_CACHE_MISSING", 500)
    assert raised.value.code == "MODEL_CACHE_MISSING"
    assert released == [True] and closed == [True] and cloud.release_task is None
    assert cloud.read()["volume"] == selected and cloud.read()["ready"] is False
    assert cloud.read()["models_verified"] is False and cloud.active == 0


@pytest.mark.asyncio
async def test_setup_close_drains_download_and_preserves_partial(tmp_path):
    import threading
    import time

    server = StorageServer()
    began, stopped = threading.Event(), threading.Event()

    def partial(repo, rev, spec, stage, *, cancel, **kwargs):
        (stage / (spec.path + ".vcs-incomplete")).write_bytes(DATA[:3])
        began.set()
        while not cancel.is_set():
            time.sleep(0.005)
        stopped.set()
        raise InterruptedError("private detail")

    manager = setup(tmp_path, server, downloader=partial)
    task = asyncio.create_task(manager.run_setup())
    assert await asyncio.to_thread(began.wait, 2)
    await manager.close()
    assert stopped.is_set() and task.done()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(manager.root.rglob("*.vcs-incomplete"))
    assert manager.status()["setup_phase"] == "cancelled"
    assert not manager.status()["files_ready"] and not server.objects


@pytest.mark.asyncio
async def test_auto_setup_respects_manual_pause_but_resumes_app_exit(tmp_path, monkeypatch):
    cloud = CloudController(Settings(data_dir=tmp_path, desktop_static_dir=tmp_path))
    selected = {"id": "vol_test", "size": 60, "dataCenter": "US-NE-1"}
    monkeypatch.setattr(
        cloud,
        "_storage_credentials",
        lambda: (StorageCredentials("user_testing", "rps_test_secret"), binding()),
    )
    monkeypatch.setattr(cloud, "is_ready", lambda state: False)
    monkeypatch.setattr(cloud_module, "storage_writes_qualified", lambda selected: True)
    starts = []

    async def start(*args):
        starts.append(True)

    monkeypatch.setattr(cloud, "_start_storage_setup", start)
    cloud.write({"volume": selected, "auto_setup_enabled": False, "pause_reason": "manual"})
    await cloud.resume_auto_setup()
    assert starts == []
    cloud.write({"volume": selected, "auto_setup_enabled": True, "pause_reason": "app_exit"})
    await cloud.resume_auto_setup()
    assert starts == [True]


@pytest.mark.asyncio
async def test_cache_failure_does_not_stop_another_active_operation(tmp_path, monkeypatch):
    cloud = CloudController(Settings(data_dir=tmp_path, desktop_static_dir=tmp_path))
    cloud.write(
        {
            "ready": True,
            "models_verified": True,
            "volume": {"id": "vol_test"},
            "compute": {"kind": "generation"},
        }
    )
    monkeypatch.setattr(cloud, "is_ready", lambda state: state.get("ready") is True)
    pair = SimpleNamespace(url="https://worker.example", token="dummy")
    monkeypatch.setattr(
        cloud_module, "WorkerPairStore", lambda data_dir: SimpleNamespace(get=lambda: pair)
    )

    class Remote:
        async def shutdown(self):
            pass

    cloud.remote_factory = lambda *args: Remote()
    releases = []

    async def release():
        assert cloud.active == 0
        releases.append(True)

    monkeypatch.setattr(cloud, "_release", release)
    async with cloud.session():
        with pytest.raises(RemoteWorkerError):
            async with cloud.session():
                raise RemoteWorkerError("MODEL_CACHE_MISSING", 500)
        assert cloud.active == 1 and releases == []
    assert releases == [True] and cloud.release_task is None


@pytest.mark.asyncio
async def test_truly_empty_xml_sentinels_are_no_objects_or_uploads(tmp_path):
    server = StorageServer()
    server.empty_sentinels = True
    manager = setup(tmp_path, server)
    try:
        result = await manager.run_setup()
        assert result["files_ready"]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_proof_requires_every_checked_cache_metadata_object(tmp_path):
    server = StorageServer()
    manager = setup(tmp_path, server)
    try:
        result = await manager.run_setup()
        proof = result["proof"]
        proof["metadata_readback"].pop(next(iter(proof["metadata_readback"])))
        (manager.root / "setup-proof.json").write_text(json.dumps(proof), encoding="utf-8")
        state = {
            "models": result["models"],
            "storage_proof_hash": hashlib.sha256(
                json.dumps(proof, sort_keys=True).encode()
            ).hexdigest(),
        }
        assert not confirmed_setup_proof(tmp_path, binding(), state)
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_inventory_accepts_exact_single_provider_root_slash_only(tmp_path):
    server = StorageServer()
    server.leading_keys = True
    for model_id in REQUIRED_MODEL_IDS:
        for repo, rev in model_graph(model_id):
            key = (
                "hf-cache/hub/models--"
                + repo.replace("/", "--")
                + "/snapshots/"
                + rev
                + "/model.bin"
            )
            server.objects[key] = DATA
    manager = setup(tmp_path, server)
    try:
        result = await manager.run_setup()
        assert result["capacity"]["missing_write_bytes"] == 0
        assert not any(
            method == "PUT" and key.endswith("model.bin") for method, key, _, _ in server.calls
        )
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_pending_upload_uses_canonical_owned_key_after_provider_root_slash(tmp_path):
    manager = setup(tmp_path, StorageServer())
    repo, revision = next(iter(model_graph(REQUIRED_MODEL_IDS[0])))
    item = entry()
    key = manager._key(repo, revision, item)
    journal, _, record = manager._journal(repo, revision, item, key)
    record["upload_id"] = "owned-upload"
    journal.write_text(json.dumps(record), encoding="utf-8")
    checked = []

    async def request(*args, **kwargs):
        return httpx.Response(
            200,
            text=(
                "<ListMultipartUploadsResult><Bucket>vol_test</Bucket>"
                "<IsTruncated>false</IsTruncated>"
                f"<Upload><Key>/{key}</Key><UploadId>owned-upload</UploadId></Upload>"
                "</ListMultipartUploadsResult>"
            ),
        )

    async def parts(actual_key, upload_id, count):
        checked.append(actual_key)
        return {1: (3, "mock")}

    manager._request = request
    manager._parts = parts
    try:
        assert await manager._multipart_occupied({key: (repo, revision, item)}) == 3
        assert checked == [key]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_upload_inventory_rejects_duplicate_truncation_flags(tmp_path):
    manager = setup(tmp_path, StorageServer())

    async def request(*args, **kwargs):
        return httpx.Response(
            200,
            text=(
                "<ListMultipartUploadsResult><Bucket>vol_test</Bucket>"
                "<IsTruncated>false</IsTruncated><IsTruncated>true</IsTruncated>"
                "</ListMultipartUploadsResult>"
            ),
        )

    manager._request = request
    try:
        with pytest.raises(StorageTransferError, match="Pending storage uploads"):
            await manager._multipart_occupied({})
    finally:
        await manager.close()
