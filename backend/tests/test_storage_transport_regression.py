"""Live-discovered transport failures reproduced without storage credentials."""

import gzip
import zlib

import httpx
import pytest

from app.runpod.storage_access import StorageBinding, StorageCredentials
from app.runpod.storage_transfer import ModelStorageTransfer


def relay(tmp_path, transport):
    return ModelStorageTransfer(
        StorageCredentials("user_test_access", "rps_test_secret_123"),
        StorageBinding("a" * 64, "vol_test", "US-NE-1"),
        tmp_path,
        transport=httpx.MockTransport(transport),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding,encode", [("gzip", gzip.compress), ("deflate", zlib.compress)])
async def test_compressed_upload_ack_is_decoded_exactly_once(tmp_path, encoding, encode):
    xml = (
        b"<InitiateMultipartUploadResult><UploadId>confirmed</UploadId>"
        b"</InitiateMultipartUploadResult>"
    )

    def transport(request):
        data = encode(xml)
        return httpx.Response(
            200,
            headers={
                "content-encoding": encoding,
                "content-length": str(len(data)),
                "etag": "preserved",
            },
            stream=httpx.ByteStream(data),
        )

    manager = relay(tmp_path, transport)
    try:
        response = await manager._request("POST", "model.bin", params=(("uploads", ""),))
        assert response.content == xml
        assert "content-encoding" not in response.headers
        assert response.headers["content-length"] == str(len(xml))
        assert response.headers["etag"] == "preserved"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_multipart_inventory_uses_bucket_root_without_object_slash(tmp_path):
    def transport(request):
        assert request.url.path == "/vol_test"
        assert request.url.params["uploads"] == ""
        return httpx.Response(200, text="<ListMultipartUploadsResult/>")

    manager = relay(tmp_path, transport)
    try:
        await manager._request("GET", "", params=(("uploads", ""),))
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_head_preserves_remote_object_size(tmp_path):
    manager = relay(
        tmp_path, lambda request: httpx.Response(200, headers={"content-length": "123456"})
    )
    try:
        response = await manager._request("HEAD", "model.bin")
        assert response.headers["content-length"] == "123456"
    finally:
        await manager.close()


def test_key_normalization_does_not_accept_other_objects():
    from app.runpod.storage_transfer import _matches_key

    assert _matches_key("hf-cache/a", "hf-cache/a")
    assert _matches_key("/hf-cache/a", "hf-cache/a")
    for value in ("//hf-cache/a", "hf-cache/../a", "%2Fhf-cache/a", "other/hf-cache/a", None):
        assert not _matches_key(value, "hf-cache/a")


def test_opaque_etags_are_not_md5_or_arbitrary_xml():
    from app.runpod.storage_transfer import _valid_etag

    assert _valid_etag("77777777-1234-5678-9012-123456789012")
    assert _valid_etag("a" * 32)
    for value in ("", "<Error>", "white space", "bad\nvalue", "a" * 129):
        assert not _valid_etag(value)


@pytest.mark.asyncio
async def test_runpod_leading_keys_compressed_replies_and_uuid_etags(tmp_path):
    import hashlib

    from app.remote_worker.model_manifest import ManifestFile
    from app.runpod.storage_transfer import TransferLimits

    data = b"abcdefghij"
    key = "hf-cache/hub/probe.bin"
    calls = []

    def transport(request):
        calls.append((request.method, dict(request.url.params)))
        if request.method == "PUT":
            return httpx.Response(200, headers={"etag": "77777777-1234-5678-9012-123456789012"})
        tag = (
            "InitiateMultipartUploadResult"
            if "uploads" in request.url.params
            else "CompleteMultipartUploadResult"
        )
        xml = (
            f"<{tag}><Bucket>vol_test</Bucket><Key>/{key}</Key>"
            f"<UploadId>confirmed</UploadId></{tag}>"
        ).encode()
        return httpx.Response(
            200, headers={"content-encoding": "gzip"}, stream=httpx.ByteStream(gzip.compress(xml))
        )

    manager = relay(tmp_path, transport)
    manager.limits = TransferLimits(single_put_bytes=3, part_bytes=6, test_only_small_parts=True)
    target = tmp_path / "probe.bin"
    target.write_bytes(data)
    record = {"state": "staging", "upload_id": None}
    try:
        await manager._multipart(
            "voxcpm2",
            key,
            target,
            ManifestFile("probe.bin", len(data), "sha256", hashlib.sha256(data).hexdigest()),
            tmp_path / "probe.json",
            record,
        )
        assert len(calls) == 4
        assert record["parts"]["1"]["etag"] == "77777777-1234-5678-9012-123456789012"
        assert record["upload_id"] == "confirmed"
    finally:
        await manager.close()


def test_weak_http_cache_etag_is_not_an_upload_part_acknowledgment():
    from app.runpod.storage_transfer import StorageTransferError, _part_etag

    part = "a" * 32
    response = httpx.Response(
        200,
        headers={"etag": 'W/"response-cache-tag"'},
        text=f'<UploadPartResult><ETag>"{part}"</ETag></UploadPartResult>',
    )
    assert _part_etag(response) == part
    with pytest.raises(StorageTransferError):
        _part_etag(httpx.Response(200, headers={"etag": 'W/"response-cache-tag"'}))
    with pytest.raises(StorageTransferError, match="conflicting"):
        _part_etag(httpx.Response(200, headers={"etag": "b" * 32}, text=response.text))
    with pytest.raises(StorageTransferError):
        _part_etag(httpx.Response(200, text="<Error><ETag>wrong</ETag></Error>"))
