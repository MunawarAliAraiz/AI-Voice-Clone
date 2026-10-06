"""Offline storage-key connection contracts; no Runpod or AWS calls."""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import storage_access as routes
from app.config import Settings
from app.runpod import storage_access as storage

ACCESS = "user_sample_only"
SECRET = "rps_test_secret_only"
ACCOUNT = "a" * 64


def binding(**changes):
    return storage.StorageBinding(
        **{"account": ACCOUNT, "volume_id": "vol_test", "region": "US-NE-1", **changes}
    )


def credentials():
    return storage.StorageCredentials(ACCESS, SECRET)


def xml(bucket="vol_test"):
    return (
        f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Name>{bucket}</Name><KeyCount>0</KeyCount></ListBucketResult>"
    )


@pytest.fixture
def mock_encryption(monkeypatch):
    def fake_crypt(data, *, protect):
        return base64.b64encode(data) if protect else base64.b64decode(data)

    monkeypatch.setattr(storage, "_crypt", fake_crypt)


@pytest.fixture
def api(tmp_path, monkeypatch, mock_encryption):
    app = FastAPI()
    app.state.settings = Settings(data_dir=tmp_path, desktop_static_dir=tmp_path)
    app.include_router(routes.router, prefix="/api")
    monkeypatch.setattr(routes, "_binding", lambda settings: binding())
    return TestClient(app)


def install_provider(monkeypatch, handle):
    original = storage.StorageAccessClient
    monkeypatch.setattr(
        routes,
        "StorageAccessClient",
        lambda key, selected: original(key, selected, transport=httpx.MockTransport(handle)),
    )


def test_aws_published_get_object_signature_vector():
    # Official AWS S3 developer guide GET Object example, public dummy keys.
    headers = storage.signature_headers(
        method="GET",
        host="examplebucket.s3.amazonaws.com",
        path="/test.txt",
        parameters=(),
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        now=datetime(2013, 5, 24, tzinfo=UTC),
        extra_headers={"range": "bytes=0-9"},
    )
    assert headers["authorization"].endswith(
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_aws_published_list_signature_vector():
    headers = storage.signature_headers(
        method="GET",
        host="examplebucket.s3.amazonaws.com",
        path="/",
        parameters=(("prefix", "J"), ("max-keys", "2")),
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        now=datetime(2013, 5, 24, tzinfo=UTC),
    )
    assert headers["authorization"].endswith(
        "Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"
    )


def test_query_encodes_then_sorts_without_form_plus_or_deduplication():
    assert storage.canonical_query((("prefix", "a b/#?\u00e9"), ("a", "+"), ("a", "/"))) == (
        "a=%2B&a=%2F&prefix=a%20b%2F%23%3F%C3%A9"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"region": "https://evil.example"},
        {"region": "US-NE-1/evil"},
        {"volume_id": "../elsewhere"},
        {"volume_id": "vol?token=secret"},
        {"volume_id": "vol\nHeader"},
        {"prefix": "another-app/"},
        {"account": "wrong"},
    ],
)
def test_binding_rejects_arbitrary_endpoint_and_paths(changes):
    with pytest.raises(ValueError):
        binding(**changes)


@pytest.mark.parametrize(
    "access,secret",
    [
        ("management-token", SECRET),
        (ACCESS, "management-token"),
        (ACCESS + "\nHeader", SECRET),
        (ACCESS, SECRET + " "),
        ("", ""),
    ],
)
def test_credentials_reject_malformed_values_without_echo(access, secret):
    with pytest.raises(ValueError) as raised:
        storage.StorageCredentials(access, secret)
    assert SECRET not in str(raised.value)
    assert ACCESS not in str(raised.value)


def test_credentials_repr_hides_both_keys():
    assert ACCESS not in repr(credentials()) and SECRET not in repr(credentials())


@pytest.mark.asyncio
async def test_validation_uses_only_fixed_endpoint_read_operations():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, content=xml() if request.method == "GET" else "")

    client = storage.StorageAccessClient(
        credentials(), binding(), transport=httpx.MockTransport(handle)
    )
    try:
        await client.validate()
    finally:
        await client.close()
    assert [item.method for item in calls] == ["HEAD", "GET"]
    assert all(item.url.host == "s3api-us-ne-1.runpod.io" for item in calls)
    assert calls[1].url.params["max-keys"] == "1"
    assert calls[1].url.params["prefix"] == "hf-cache/hub/"
    assert calls[1].headers["authorization"].startswith("AWS4-HMAC-SHA256 ")
    assert SECRET not in str(calls[1].url)
    assert all(not item.content for item in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 307, 401, 403, 404, 500])
async def test_provider_error_and_redirect_are_private_and_never_followed(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status, headers={"Location": "https://evil.example"}, text=SECRET + ACCESS
        )

    client = storage.StorageAccessClient(
        credentials(), binding(), transport=httpx.MockTransport(handle)
    )
    try:
        with pytest.raises(storage.StorageAccessError) as raised:
            await client.validate()
        assert SECRET not in str(raised.value) and ACCESS not in str(raised.value)
        assert len(calls) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        "<html>Not S3</html>",
        xml("another_volume"),
        '<!DOCTYPE x [<!ENTITY y "bad">]><x>&y;</x>',
        "x" * 65537,
    ],
    ids=["html", "wrong-volume", "entity", "oversized"],
)
async def test_validation_rejects_invalid_or_unbounded_listing(body):
    client = storage.StorageAccessClient(
        credentials(),
        binding(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=body if request.method == "GET" else "")
        ),
    )
    try:
        with pytest.raises(storage.StorageAccessError):
            await client.validate()
    finally:
        await client.close()


def test_store_is_bound_to_account_volume_region_and_namespace(tmp_path, mock_encryption):
    store = storage.StorageAccessStore(tmp_path)
    store.set(credentials(), binding())
    assert store.get(binding()) == credentials()
    assert SECRET.encode() not in store.path.read_bytes()
    for other in (
        binding(account="b" * 64),
        binding(volume_id="vol_other"),
        binding(region="US-NC-1"),
        binding(prefix="voice-clone/hf-cache/hub/"),
    ):
        assert store.get(other) is None
    status = store.status(binding())
    assert status == {
        "connected": True,
        "access_key_masked": "****only",
        "region": "US-NE-1",
        "download_supported": False,
    }
    assert SECRET not in json.dumps(status) and ACCESS not in json.dumps(status)
    store.clear()
    assert store.get(binding()) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI required")
def test_real_windows_dpapi_roundtrip_uses_disposable_profile(tmp_path):
    store = storage.StorageAccessStore(tmp_path)
    store.set(credentials(), binding())
    assert store.get(binding()) == credentials()
    assert ACCESS.encode() not in store.path.read_bytes()
    assert SECRET.encode() not in store.path.read_bytes()
    assert not list(store.path.parent.glob(".storage-access-*"))
    store.clear()


def test_encryption_failure_writes_nothing(tmp_path, monkeypatch):
    def fail(data, *, protect):
        raise OSError("private")

    monkeypatch.setattr(storage, "_crypt", fail)
    store = storage.StorageAccessStore(tmp_path)
    with pytest.raises(OSError):
        store.set(credentials(), binding())
    assert not store.path.exists()
    assert not store.path.parent.exists()


def test_connect_checks_before_save_and_response_masks_credentials(api, monkeypatch):
    calls = []

    def handle(request):
        assert not storage.StorageAccessStore(api.app.state.settings.data_dir).path.exists()
        calls.append(request.method)
        return httpx.Response(200, text=xml() if request.method == "GET" else "")

    install_provider(monkeypatch, handle)
    response = api.put(
        "/api/runpod/storage-access", json={"access_key": ACCESS, "secret_key": SECRET}
    )
    assert response.status_code == 200
    assert calls == ["HEAD", "GET"]
    assert response.json()["connected"]
    assert ACCESS not in response.text and SECRET not in response.text
    assert api.get("/api/runpod/storage-access").json()["connected"]
    assert api.delete("/api/runpod/storage-access").status_code == 204
    assert not api.get("/api/runpod/storage-access").json()["connected"]


def test_denied_read_saves_nothing_and_no_secret_echo(api, monkeypatch):
    install_provider(monkeypatch, lambda request: httpx.Response(403, text=SECRET))
    response = api.put(
        "/api/runpod/storage-access", json={"access_key": ACCESS, "secret_key": SECRET}
    )
    assert response.status_code == 502 and SECRET not in response.text
    assert not storage.StorageAccessStore(api.app.state.settings.data_dir).path.exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"access_key": ACCESS, "secret_key": SECRET, "endpoint_url": "https://evil.example"},
        {"access_key": ACCESS, "secret_key": {"raw": SECRET}},
        {"access_key": SECRET},
        {"access_key": ACCESS, "secret_key": SECRET + "\n"},
        {"access_key": ACCESS, "secret_key": SECRET * 300},
    ],
)
def test_secret_validation_errors_do_not_echo_payload(api, payload):
    response = api.put("/api/runpod/storage-access", json=payload)
    assert response.status_code == 400
    assert ACCESS not in response.text and SECRET not in response.text


def test_account_or_volume_switch_during_read_does_not_save(api, monkeypatch):
    count = 0

    def selected(settings):
        nonlocal count
        count += 1
        return binding() if count == 1 else binding(volume_id="vol_other")

    monkeypatch.setattr(routes, "_binding", selected)
    install_provider(
        monkeypatch,
        lambda request: httpx.Response(200, text=xml() if request.method == "GET" else ""),
    )
    response = api.put(
        "/api/runpod/storage-access", json={"access_key": ACCESS, "secret_key": SECRET}
    )
    assert response.status_code == 502 and "changed" in response.text
    assert not storage.StorageAccessStore(api.app.state.settings.data_dir).path.exists()


def test_desktop_only_route(api):
    api.app.state.settings.desktop_static_dir = None
    assert api.get("/api/runpod/storage-access").status_code == 404


def test_recheck_reads_only_and_does_not_rewrite_credentials(api, monkeypatch):
    store = storage.StorageAccessStore(api.app.state.settings.data_dir)
    store.set(credentials(), binding())
    original = store.path.read_bytes()
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, text=xml() if request.method == "GET" else "")

    install_provider(monkeypatch, handle)
    response = api.post("/api/runpod/storage-access/check")
    assert response.status_code == 200 and response.json()["connected"]
    assert calls == ["HEAD", "GET"]
    assert store.path.read_bytes() == original
    assert ACCESS not in response.text and SECRET not in response.text


def test_recheck_denied_preserves_saved_keys_for_reconnect_controls(api, monkeypatch):
    store = storage.StorageAccessStore(api.app.state.settings.data_dir)
    store.set(credentials(), binding())
    install_provider(monkeypatch, lambda request: httpx.Response(403, text=SECRET))
    response = api.post("/api/runpod/storage-access/check")
    assert response.status_code == 502 and SECRET not in response.text
    assert store.get(binding()) == credentials()


def test_recheck_different_volume_cannot_use_saved_keys(api, monkeypatch):
    store = storage.StorageAccessStore(api.app.state.settings.data_dir)
    store.set(credentials(), binding())
    monkeypatch.setattr(routes, "_binding", lambda settings: binding(volume_id="different"))
    response = api.post("/api/runpod/storage-access/check")
    assert response.status_code == 409
    assert not api.get("/api/runpod/storage-access").json()["connected"]


@pytest.mark.asyncio
async def test_transport_error_hides_private_exception_details():
    def fail(request):
        raise httpx.ReadError(SECRET + ACCESS)

    client = storage.StorageAccessClient(
        credentials(), binding(), transport=httpx.MockTransport(fail)
    )
    try:
        with pytest.raises(storage.StorageAccessError) as raised:
            await client.validate()
        assert SECRET not in str(raised.value) and ACCESS not in str(raised.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_utf16_entity_response_rejected_before_xml_parse():
    body = (
        '<!DOCTYPE x [<!ENTITY y "bad">]><ListBucketResult><Name>vol_test</Name></ListBucketResult>'
    ).encode("utf-16")
    client = storage.StorageAccessClient(
        credentials(),
        binding(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body if request.method == "GET" else b"")
        ),
    )
    try:
        with pytest.raises(storage.StorageAccessError):
            await client.validate()
    finally:
        await client.close()


@pytest.mark.parametrize(
    "headers",
    [{"host": "evil.example"}, {"bad\r\nheader": "value"}, {"range": "bytes=0-9\nprivate"}],
)
def test_signer_rejects_header_override_and_injection(headers):
    with pytest.raises(ValueError):
        storage.signature_headers(
            method="GET",
            host="s3api-us-ne-1.runpod.io",
            path="/vol_test",
            parameters=(),
            access_key=ACCESS,
            secret_key=SECRET,
            region="US-NE-1",
            now=datetime.now(UTC),
            extra_headers=headers,
        )
