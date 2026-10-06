"""Tiny injected manifests/transports only; never download real weights or rent."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from types import SimpleNamespace
from xml.etree import ElementTree

import httpx
import pytest

from app.remote_worker.model_manifest import ManifestFile, transfer_file
from app.runpod.storage_access import StorageBinding, StorageCredentials
from app.runpod.storage_transfer import ModelStorageTransfer, StorageTransferError, TransferLimits

REPO = "Example/voice"
REV = "a" * 40
DATA = b"abcdefghij"
KEY = "hf-cache/hub/models--Example--voice/snapshots/" + REV + "/model.bin"


def entry(data=DATA, path="model.bin", algorithm="sha256"):
    digest = (
        hashlib.sha256(data).hexdigest()
        if algorithm == "sha256"
        else hashlib.sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()
    )
    return ManifestFile(path, len(data), algorithm, digest)


class S3:
    def __init__(self):
        self.objects = {}
        self.uploads = {}
        self.calls = []
        self.create_count = 0
        self.fail_part = None
        self.fail_create = False
        self.fail_complete = False
        self.fail_complete_no_publish = False
        self.truncation_xml = "<IsTruncated>false</IsTruncated>"
        self.bad_readback = False
        self.redirect = False

    def __call__(self, request):
        key = request.url.path.split("/vol_test/", 1)[1]
        query = dict(request.url.params)
        self.calls.append((request.method, key, query, request.content))
        if self.redirect:
            return httpx.Response(
                307, headers={"location": "https://evil.example"}, text="rps_private"
            )
        if request.method == "HEAD":
            return (
                httpx.Response(200, headers={"content-length": str(len(self.objects[key]))})
                if key in self.objects
                else httpx.Response(404)
            )
        if "uploads" in query:
            self.create_count += 1
            upload_id = f"upload{self.create_count}"
            self.uploads[upload_id] = {"key": key, "parts": {}}
            if self.fail_create:
                raise httpx.ReadTimeout("rps_private-provider-detail")
            return httpx.Response(
                200,
                text=(
                    f"<InitiateMultipartUploadResult><Bucket>vol_test</Bucket>"
                    f"<Key>{key}</Key><UploadId>{upload_id}</UploadId></InitiateMultipartUploadResult>"
                ),
            )
        if "uploadId" in query:
            upload = self.uploads[query["uploadId"]]
            if request.method == "GET":
                parts = "".join(
                    f"<Part><PartNumber>{number}</PartNumber><Size>{len(data)}</Size>"
                    f'<ETag>"{hashlib.md5(data, usedforsecurity=False).hexdigest()}"</ETag></Part>'
                    for number, data in sorted(upload["parts"].items())
                )
                return httpx.Response(
                    200,
                    text=f"<ListPartsResult><Bucket>vol_test</Bucket>"
                    f"<Key>{upload['key']}</Key>{self.truncation_xml}{parts}</ListPartsResult>",
                )
            if request.method == "PUT":
                number = int(query["partNumber"])
                if number == self.fail_part:
                    raise httpx.ReadError("rps_private-upload-detail")
                upload["parts"][number] = request.content
                return httpx.Response(
                    200,
                    headers={
                        "etag": '"'
                        + hashlib.md5(request.content, usedforsecurity=False).hexdigest()
                        + '"'
                    },
                )
            if self.fail_complete_no_publish:
                raise httpx.ReadTimeout("rps_private-completion-detail")
            root = ElementTree.fromstring(request.content)
            numbers = [int(node.findtext("PartNumber")) for node in root]
            self.objects[key] = b"".join(upload["parts"][number] for number in numbers)
            if self.fail_complete:
                raise httpx.ReadTimeout("rps_private-completion-detail")
            return httpx.Response(
                200,
                text=f"<CompleteMultipartUploadResult><Bucket>vol_test</Bucket>"
                f"<Key>{key}</Key></CompleteMultipartUploadResult>",
            )
        if request.method == "PUT":
            self.objects[key] = request.content
            return httpx.Response(200)
        content = self.objects[key]
        return httpx.Response(200, content=b"x" * len(content) if self.bad_readback else content)


def download(repo, revision, spec, stage, *, progress, cancel, cache_root):
    target = stage / spec.path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(DATA)
    progress(len(DATA))
    return hashlib.sha256(DATA).hexdigest()


def manager(tmp_path, server, *, spec=None, downloader=download, limits=None, **changes):
    binding = StorageBinding("a" * 64, "vol_test", "US-NE-1", **changes)
    return ModelStorageTransfer(
        StorageCredentials("user_testing", "rps_test_secret"),
        binding,
        tmp_path,
        transport=httpx.MockTransport(server),
        manifest_loader=lambda repo, rev: [spec or entry()],
        downloader=downloader,
        graph=lambda model: ((REPO, REV),),
        limits=limits,
    )


@pytest.mark.asyncio
async def test_small_file_stages_hashes_streams_and_reads_back(tmp_path):
    server = S3()
    relay = manager(tmp_path, server)
    try:
        result = await relay.transfer_model("voxcpm2")
        assert result["files_checked"] and not result["ready"]
        assert result["files"][0]["sha256"] == hashlib.sha256(DATA).hexdigest()
        assert server.objects[KEY] == DATA
        assert [call[0] for call in server.calls] == ["HEAD", "PUT", "HEAD", "GET"]
        assert relay.status()["models"]["voxcpm2"]["state"] == "files_checked"
        assert not relay.status()["ready"]
        assert not list(relay.root.rglob("model.bin"))
        progress = (relay.root / "progress.json").read_text()
        assert "rps_test_secret" not in progress and "user_testing" not in progress
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_existing_correct_model_is_hashed_not_downloaded_or_uploaded(tmp_path):
    server = S3()
    server.objects[KEY] = DATA

    def forbidden(*args, **kwargs):
        pytest.fail("Existing verified files must not be downloaded")

    relay = manager(tmp_path, server, downloader=forbidden)
    try:
        await relay.transfer_model("voxcpm2")
        assert [item[0] for item in server.calls] == ["HEAD", "GET"]
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_same_size_corrupt_model_is_replaced(tmp_path):
    server = S3()
    server.objects[KEY] = b"x" * len(DATA)
    relay = manager(tmp_path, server)
    try:
        await relay.transfer_model("voxcpm2")
        assert server.objects[KEY] == DATA
        assert sum(method == "GET" for method, *_ in server.calls) == 2
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_bad_staged_checksum_cannot_upload(tmp_path):
    server = S3()

    def bad(repo, rev, spec, stage, **kwargs):
        (stage / spec.path).write_bytes(b"x" * len(DATA))
        return hashlib.sha256(DATA).hexdigest()

    relay = manager(tmp_path, server, downloader=bad)
    try:
        with pytest.raises(StorageTransferError):
            await relay.transfer_model("voxcpm2")
        assert all(method == "HEAD" for method, *_ in server.calls)
        assert relay.status()["models"]["voxcpm2"]["state"] == "failed"
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_remote_readback_failure_retains_staging_and_is_not_ready(tmp_path):
    server = S3()
    server.bad_readback = True
    relay = manager(tmp_path, server)
    try:
        with pytest.raises(StorageTransferError, match="checksum"):
            await relay.transfer_model("voxcpm2")
        assert list(relay.root.rglob("model.bin"))
        assert not relay.status()["ready"]
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_git_blob_small_file_integrity_uses_blob_prefix(tmp_path):
    server = S3()
    relay = manager(tmp_path, server, spec=entry(algorithm="git-blob-sha1"))
    try:
        result = await relay.transfer_model("voxcpm2")
        assert result["files"][0]["sha256"] == hashlib.sha256(DATA).hexdigest()
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_large_file_multipart_and_complete_readback(tmp_path):
    server = S3()
    relay = manager(
        tmp_path,
        server,
        limits=TransferLimits(single_put_bytes=3, part_bytes=4, test_only_small_parts=True),
    )
    try:
        await relay.transfer_model("voxcpm2")
        assert server.objects[KEY] == DATA and server.create_count == 1
        assert [
            query["partNumber"]
            for method, key, query, data in server.calls
            if method == "PUT" and "partNumber" in query
        ] == ["1", "2", "3"]
        assert max(len(data) for method, key, query, data in server.calls if method == "PUT") <= 4
        assert [method for method, *_ in server.calls][-2:] == ["HEAD", "GET"]
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_interrupted_parts_resume_without_uploading_valid_part_again(tmp_path):
    server = S3()
    server.fail_part = 2
    limits = TransferLimits(single_put_bytes=3, part_bytes=4, test_only_small_parts=True)
    relay = manager(tmp_path, server, limits=limits)
    try:
        with pytest.raises(StorageTransferError):
            await relay.transfer_model("voxcpm2")
    finally:
        await relay.close()
    server.fail_part = None
    before = len(server.calls)
    relay = manager(tmp_path, server, limits=limits)
    try:
        assert relay.status()["models"]["voxcpm2"]["state"] == "paused"
        await relay.transfer_model("voxcpm2")
        assert server.create_count == 1
        resumed = server.calls[before:]
        assert not any(query.get("partNumber") == "1" for _, _, query, _ in resumed)
        assert server.objects[KEY] == DATA
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_corrupt_saved_part_is_reuploaded_and_whole_file_checked(tmp_path):
    server = S3()
    server.fail_part = 2
    limits = TransferLimits(single_put_bytes=3, part_bytes=4, test_only_small_parts=True)
    relay = manager(tmp_path, server, limits=limits)
    try:
        with pytest.raises(StorageTransferError):
            await relay.transfer_model("voxcpm2")
        server.uploads["upload1"]["parts"][1] = b"xxxx"
        server.fail_part = None
        before = len(server.calls)
        await relay.transfer_model("voxcpm2")
        assert any(query.get("partNumber") == "1" for _, _, query, _ in server.calls[before:])
        assert server.objects[KEY] == DATA
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_uncertain_start_is_never_replayed(tmp_path):
    server = S3()
    server.fail_create = True
    relay = manager(
        tmp_path,
        server,
        limits=TransferLimits(single_put_bytes=3, part_bytes=4, test_only_small_parts=True),
    )
    try:
        with pytest.raises(StorageTransferError):
            await relay.transfer_model("voxcpm2")
        server.fail_create = False
        with pytest.raises(StorageTransferError, match="start needs checking"):
            await relay.transfer_model("voxcpm2")
        assert server.create_count == 1
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_uncertain_completion_reconciles_final_bytes_without_mutation(tmp_path):
    server = S3()
    server.fail_complete = True
    relay = manager(
        tmp_path,
        server,
        limits=TransferLimits(single_put_bytes=3, part_bytes=4, test_only_small_parts=True),
    )
    try:
        with pytest.raises(StorageTransferError):
            await relay.transfer_model("voxcpm2")
        before = len(server.calls)
        result = await relay.transfer_model("voxcpm2")
        assert result["files_checked"]
        assert [method for method, *_ in server.calls[before:]] == ["HEAD", "GET"]
        assert not list(relay.root.rglob("model.bin"))
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_remote_foreign_upload_identity_refused(tmp_path):
    server = S3()
    server.fail_part = 2
    relay = manager(
        tmp_path,
        server,
        limits=TransferLimits(single_put_bytes=3, part_bytes=4, test_only_small_parts=True),
    )
    try:
        with pytest.raises(StorageTransferError):
            await relay.transfer_model("voxcpm2")
        server.fail_part = None
        server.uploads["upload1"]["key"] = "another-app/model.bin"
        with pytest.raises(StorageTransferError, match="does not match"):
            await relay.transfer_model("voxcpm2")
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_redirect_is_not_followed_and_errors_hide_provider_details(tmp_path):
    server = S3()
    server.redirect = True
    relay = manager(tmp_path, server)
    try:
        with pytest.raises(StorageTransferError) as error:
            await relay.transfer_model("voxcpm2")
        assert "rps_private" not in str(error.value)
        assert len(server.calls) == 1
        assert "rps_private" not in json.dumps(relay.status())
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_cancelled_downloader_joins_writer_and_preserves_partial(tmp_path):
    server = S3()
    started = threading.Event()
    stopped = threading.Event()

    def partial(repo, rev, spec, stage, *, progress, cancel, cache_root):
        (stage / (spec.path + ".vcs-incomplete")).write_bytes(DATA[:3])
        started.set()
        while not cancel.is_set():
            time.sleep(0.005)
        stopped.set()
        raise InterruptedError("private detail")

    relay = manager(tmp_path, server, downloader=partial)
    task = asyncio.create_task(relay.transfer_model("voxcpm2"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
        assert next(iter(relay.root.rglob("*.vcs-incomplete"))).read_bytes() == DATA[:3]
        assert relay.status()["models"]["voxcpm2"]["state"] == "paused"
        assert not server.objects
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_existing_partial_resumes_with_actual_hub_range_downloader(tmp_path):
    server = S3()
    headers = []

    def hub(request):
        headers.append(request.headers.get("range"))
        return httpx.Response(206, content=DATA[3:], headers={"content-range": "bytes 3-9/10"})

    def downloader(repo, rev, spec, stage, **kwargs):
        partial = stage / (spec.path + ".vcs-incomplete")
        partial.write_bytes(DATA[:3])
        return transfer_file(repo, rev, spec, stage, transport=httpx.MockTransport(hub), **kwargs)

    relay = manager(tmp_path, server, downloader=downloader)
    try:
        await relay.transfer_model("voxcpm2")
        assert headers == ["bytes=3-"]
        assert server.objects[KEY] == DATA
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_process_lock_prevents_second_manager_writing_same_storage(tmp_path):
    server = S3()
    first = manager(tmp_path, server)
    second = manager(tmp_path, server)
    try:
        with first._process_lock():
            with pytest.raises(StorageTransferError, match="already running"):
                await second.transfer_model("voxcpm2")
        assert not server.calls
        await second.transfer_model("voxcpm2")
        assert server.objects[KEY] == DATA
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_shared_volume_prefix_and_existing_manifest_path_preserved(tmp_path):
    server = S3()
    relay = manager(tmp_path, server, prefix="voice-clone/hf-cache/hub/")
    try:
        await relay.transfer_model("voxcpm2")
        assert all(
            key.startswith("voice-clone/hf-cache/hub/models--Example--voice/snapshots/")
            for _, key, _, _ in server.calls
        )
        assert KEY not in server.objects
    finally:
        await relay.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"single_put_bytes": 500_000_000},
        {"part_bytes": 500_000_001},
        {"single_put_bytes": 0},
        {"part_bytes": 0},
    ],
)
def test_limits_obey_runpod_500mb_constraints(changes):
    with pytest.raises(ValueError):
        TransferLimits(**changes)


def test_small_parts_cannot_be_enabled_on_a_real_network_transport(tmp_path):
    selected = StorageBinding("a" * 64, "vol_test", "US-NE-1")
    with pytest.raises(ValueError, match="offline"):
        ModelStorageTransfer(
            StorageCredentials("user_testing", "rps_test_secret"),
            selected,
            tmp_path,
            limits=TransferLimits(part_bytes=4, test_only_small_parts=True),
        )
    with pytest.raises(ValueError):
        TransferLimits(part_bytes=4)


@pytest.mark.asyncio
async def test_close_cancels_metadata_wait_without_stale_progress_writes(tmp_path):
    server = S3()
    relay = manager(tmp_path, server)
    entered = threading.Event()
    released = threading.Event()

    def metadata(repo, rev):
        entered.set()
        released.wait(2)
        return [entry()]

    relay.manifest_loader = metadata
    task = asyncio.create_task(relay.transfer_model("voxcpm2"))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        before = time.perf_counter()
        await relay.close()
        assert time.perf_counter() - before < 1
        assert task.cancelled()
        stopped = (relay.root / "progress.json").read_bytes()
        released.set()
        await asyncio.sleep(0.05)
        assert (relay.root / "progress.json").read_bytes() == stopped
        assert not server.calls
    finally:
        released.set()


@pytest.mark.asyncio
async def test_close_joins_downloader_before_returning_and_releases_lock(tmp_path):
    server = S3()
    started = threading.Event()
    stopped = threading.Event()

    def downloader(repo, rev, spec, stage, *, progress, cancel, cache_root):
        started.set()
        while not cancel.is_set():
            time.sleep(0.005)
        stopped.set()
        raise InterruptedError("private detail")

    relay = manager(tmp_path, server, downloader=downloader)
    task = asyncio.create_task(relay.transfer_model("voxcpm2"))
    assert await asyncio.to_thread(started.wait, 1)
    await relay.close()
    assert stopped.is_set() and task.cancelled()
    other = manager(tmp_path, server)
    try:
        await other.transfer_model("voxcpm2")
        assert server.objects[KEY] == DATA
    finally:
        await other.close()


@pytest.mark.asyncio
async def test_insufficient_pc_space_blocks_download_and_provider_writes(tmp_path, monkeypatch):
    from app.runpod import storage_transfer

    monkeypatch.setattr(storage_transfer.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    server = S3()

    def forbidden(*args, **kwargs):
        pytest.fail("No download may start when local staging space is insufficient")

    relay = manager(tmp_path, server, downloader=forbidden)
    try:
        with pytest.raises(StorageTransferError, match="PC needs more free space"):
            await relay.transfer_model("voxcpm2")
        assert [method for method, *_ in server.calls] == ["HEAD"]
        assert not server.objects
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_valid_complete_staging_reuses_file_even_when_no_more_space(tmp_path, monkeypatch):
    from app.runpod import storage_transfer

    monkeypatch.setattr(storage_transfer.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    server = S3()

    def forbidden(*args, **kwargs):
        pytest.fail("A valid staged file must be reused")

    relay = manager(tmp_path, server, downloader=forbidden)
    _journal, stage, _record = relay._journal(REPO, REV, entry(), KEY)
    stage.mkdir()
    (stage / "model.bin").write_bytes(DATA)
    try:
        await relay.transfer_model("voxcpm2")
        assert server.objects[KEY] == DATA
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_close_prevents_queued_transfer_from_starting_after_cancel(tmp_path):
    server = S3()
    started = threading.Event()

    def downloader(repo, rev, spec, stage, *, progress, cancel, cache_root):
        started.set()
        while not cancel.is_set():
            time.sleep(0.005)
        raise InterruptedError("paused")

    relay = manager(tmp_path, server, downloader=downloader)
    active = asyncio.create_task(relay.transfer_model("voxcpm2"))
    assert await asyncio.to_thread(started.wait, 1)
    queued = asyncio.create_task(relay.transfer_model("chatterbox_ml_v3"))
    await asyncio.sleep(0)
    await relay.close()
    assert active.cancelled()
    with pytest.raises(StorageTransferError, match="stopping"):
        await queued
    assert not server.objects


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flag",
    [
        "",
        "<IsTruncated></IsTruncated>",
        "<IsTruncated>garbage</IsTruncated>",
        "<IsTruncated>False</IsTruncated>",
        "<IsTruncated>false</IsTruncated><IsTruncated>true</IsTruncated>",
    ],
)
async def test_invalid_list_parts_truncation_cannot_resume_upload(tmp_path, flag):
    server = S3()
    server.fail_part = 2
    relay = manager(
        tmp_path,
        server,
        limits=TransferLimits(single_put_bytes=3, part_bytes=4, test_only_small_parts=True),
    )
    try:
        with pytest.raises(StorageTransferError):
            await relay.transfer_model("voxcpm2")
        server.fail_part = None
        server.truncation_xml = flag
        before = len(server.calls)
        with pytest.raises(StorageTransferError, match="list upload progress"):
            await relay.transfer_model("voxcpm2")
        assert [method for method, *_ in server.calls[before:]] == ["HEAD", "GET"]
        assert not server.objects and not relay.status()["ready"]
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_part_beyond_staged_file_count_cannot_resume_upload(tmp_path):
    server = S3()
    server.fail_part = 2
    relay = manager(
        tmp_path,
        server,
        limits=TransferLimits(single_put_bytes=3, part_bytes=4, test_only_small_parts=True),
    )
    try:
        with pytest.raises(StorageTransferError):
            await relay.transfer_model("voxcpm2")
        server.fail_part = None
        server.uploads["upload1"]["parts"][4] = b"foreign"
        before = len(server.calls)
        with pytest.raises(StorageTransferError, match="invalid part details"):
            await relay.transfer_model("voxcpm2")
        assert [method for method, *_ in server.calls[before:]] == ["HEAD", "GET"]
        assert not server.objects and not relay.status()["ready"]
    finally:
        await relay.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("publish_corrupt", [False, True])
async def test_uncertain_completion_without_valid_final_object_is_fenced(tmp_path, publish_corrupt):
    server = S3()
    server.fail_complete_no_publish = True
    limits = TransferLimits(single_put_bytes=3, part_bytes=4, test_only_small_parts=True)
    relay = manager(tmp_path, server, limits=limits)
    try:
        with pytest.raises(StorageTransferError):
            await relay.transfer_model("voxcpm2")
    finally:
        await relay.close()
    server.fail_complete_no_publish = False
    if publish_corrupt:
        server.objects[KEY] = b"x" * len(DATA)
    before = len(server.calls)
    relay = manager(tmp_path, server, limits=limits)
    try:
        with pytest.raises(StorageTransferError, match="completion needs checking"):
            await relay.transfer_model("voxcpm2")
        assert [method for method, *_ in server.calls[before:]] == (
            ["HEAD", "GET"] if publish_corrupt else ["HEAD"]
        )
        assert (
            sum(method == "POST" and "uploadId" in query for method, _, query, _ in server.calls)
            == 1
        )
        assert len(server.uploads["upload1"]["parts"]) == 3
        assert list(relay.root.rglob("model.bin")) and not relay.status()["ready"]
    finally:
        await relay.close()
