"""Optional Cloudflare connections with mock HTTP and disposable profiles only."""

from __future__ import annotations

import base64
import json
import os

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import integrations as routes
from app.config import Settings
from app.integrations import cloudflare as cf

ACCOUNT = "a" * 32
TOKEN = "example_token_private_not_live_1234567890"
PATH = "/api/integrations/cloudflare"


def credentials():
    return cf.CloudflareCredentials(ACCOUNT, TOKEN)


def provider(request):
    return httpx.Response(
        200,
        json={
            "success": True,
            "result": [] if request.url.path.endswith("/scripts") else {"status": "active"},
        },
    )


@pytest.fixture
def mock_crypt(monkeypatch):
    monkeypatch.setattr(
        cf,
        "_crypt",
        lambda data, *, protect: base64.b64encode(data) if protect else base64.b64decode(data),
    )


@pytest.fixture
def api(tmp_path, mock_crypt):
    app = FastAPI()
    app.state.settings = Settings(data_dir=tmp_path, desktop_static_dir=tmp_path)
    app.include_router(routes.router, prefix="/api")
    return TestClient(app)


def install_provider(monkeypatch, handler):
    original = cf.CloudflareClient
    monkeypatch.setattr(
        routes,
        "CloudflareClient",
        lambda creds: original(creds, transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize(
    "account,token",
    [
        (ACCOUNT + "/evil", TOKEN),
        (ACCOUNT + "\n", TOKEN),
        ("", TOKEN),
        (None, TOKEN),
        (ACCOUNT, TOKEN + "\r\nHeader: bad"),
        (ACCOUNT, TOKEN + " "),
        (ACCOUNT, "x" * 513),
        (ACCOUNT, 3),
        (ACCOUNT, ""),
    ],
)
def test_credentials_strict_and_private(account, token):
    with pytest.raises(ValueError) as raised:
        cf.CloudflareCredentials(account, token)
    assert TOKEN not in str(raised.value) and ACCOUNT not in str(raised.value)


def test_repr_hides_account_and_token():
    assert TOKEN not in repr(credentials()) and ACCOUNT not in repr(credentials())
    assert cf.CloudflareCredentials(ACCOUNT.upper(), TOKEN).account_id == ACCOUNT


@pytest.mark.asyncio
async def test_account_token_uses_fixed_get_paths_only():
    calls = []

    def handle(request):
        calls.append(request)
        return provider(request)

    client = cf.CloudflareClient(credentials(), transport=httpx.MockTransport(handle))
    try:
        await client.validate()
        with pytest.raises(ValueError):
            await client._get("https://evil.example/")
    finally:
        await client.close()
    assert [r.url.path for r in calls] == [
        f"/client/v4/accounts/{ACCOUNT}/tokens/verify",
        f"/client/v4/accounts/{ACCOUNT}/workers/scripts",
    ]
    assert all(
        r.method == "GET" and r.url.host == "api.cloudflare.com" and r.url.scheme == "https"
        for r in calls
    )
    assert all(r.headers["authorization"] == "Bearer " + TOKEN for r in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_user_token_fallback_still_checks_selected_account(status):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path.endswith(f"/accounts/{ACCOUNT}/tokens/verify"):
            return httpx.Response(status, text=TOKEN)
        return provider(request)

    client = cf.CloudflareClient(credentials(), transport=httpx.MockTransport(handle))
    try:
        await client.validate()
    finally:
        await client.close()
    assert paths == [
        f"/client/v4/accounts/{ACCOUNT}/tokens/verify",
        "/client/v4/user/tokens/verify",
        f"/client/v4/accounts/{ACCOUNT}/workers/scripts",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(307, headers={"location": "https://evil.example"}, text=TOKEN),
        httpx.Response(200, text=TOKEN),
        httpx.Response(200, json={"success": "true", "result": {"status": "active"}}),
        httpx.Response(200, json={"success": True, "result": {"status": "expired"}}),
        httpx.Response(200, content=b"x" * (cf.MAX_RESPONSE + 1)),
        httpx.Response(429, text=TOKEN),
        httpx.Response(503, text=TOKEN),
    ],
)
async def test_provider_failures_no_redirect_retry_or_private_echo(response):
    calls = []

    def handle(request):
        calls.append(request)
        return response

    client = cf.CloudflareClient(credentials(), transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(cf.CloudflareConnectionError) as raised:
            await client.validate()
        assert TOKEN not in str(raised.value) and len(calls) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_transport_failure_is_private():
    def fail(request):
        raise httpx.ReadTimeout(TOKEN)

    client = cf.CloudflareClient(credentials(), transport=httpx.MockTransport(fail))
    try:
        with pytest.raises(cf.CloudflareConnectionError, match="Cannot reach") as raised:
            await client.validate()
        assert TOKEN not in str(raised.value)
    finally:
        await client.close()


def test_atomic_encryption_roundtrip_and_profile_separation(tmp_path, mock_crypt, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    store = cf.CloudflareConnectionStore(tmp_path)
    assert not store.status()["connected"]
    store.set(credentials())
    assert store.get() == credentials()
    assert (
        TOKEN.encode() not in store.path.read_bytes()
        and ACCOUNT.encode() not in store.path.read_bytes()
    )
    assert not list(store.path.parent.glob(".cloudflare-*"))
    assert not cf.CloudflareConnectionStore(tmp_path / "other-profile").status()["connected"]
    assert store.status()["protection_ready"] is False
    store.clear()
    assert not store.status()["connected"]


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_real_dpapi_disposable_profile(tmp_path):
    store = cf.CloudflareConnectionStore(tmp_path)
    store.set(credentials())
    assert store.get() == credentials()
    assert TOKEN.encode() not in store.path.read_bytes()
    store.clear()


def test_encryption_failure_writes_nothing(tmp_path, monkeypatch):
    def fail(data, *, protect):
        raise OSError(TOKEN)

    monkeypatch.setattr(cf, "_crypt", fail)
    store = cf.CloudflareConnectionStore(tmp_path)
    with pytest.raises(OSError):
        store.set(credentials())
    assert not store.path.exists() and not store.path.parent.exists()


def test_connect_validates_then_saves_and_disconnects(api, monkeypatch):
    store = cf.CloudflareConnectionStore(api.app.state.settings.data_dir)
    calls = []

    def handle(request):
        assert not store.path.exists()
        calls.append(request.method)
        return provider(request)

    install_provider(monkeypatch, handle)
    response = api.put(PATH, json={"account_id": ACCOUNT, "api_token": TOKEN})
    assert response.status_code == 200 and calls == ["GET", "GET"]
    assert response.json() == {
        "connected": True,
        "account_id_masked": "****aaaa",
        "protection_ready": False,
        "detail": cf.CONNECTED_DETAIL,
    }
    assert TOKEN not in response.text and ACCOUNT not in response.text
    assert api.get(PATH).json() == response.json()
    assert api.delete(PATH).status_code == 204
    assert api.get(PATH).json() == {
        "connected": False,
        "account_id_masked": None,
        "protection_ready": False,
        "detail": None,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"account_id": ACCOUNT, "api_token": {"raw": TOKEN}},
        {"account_id": ACCOUNT, "api_token": TOKEN, "url": "https://evil.example"},
        {"account_id": ACCOUNT, "api_token": TOKEN + "\n"},
        {"account_id": ACCOUNT, "api_token": TOKEN * 100},
        {"account_id": ACCOUNT},
        [TOKEN],
    ],
)
def test_bad_request_has_no_secret_echo(api, payload):
    response = api.put(PATH, json=payload)
    assert (
        response.status_code == 400 and TOKEN not in response.text and ACCOUNT not in response.text
    )
    assert not cf.CloudflareConnectionStore(api.app.state.settings.data_dir).path.exists()


def test_invalid_raw_json_is_private(api):
    response = api.put(PATH, content=TOKEN)
    assert response.status_code == 400 and TOKEN not in response.text


def test_denied_account_workers_does_not_replace_existing_pair(api, monkeypatch):
    store = cf.CloudflareConnectionStore(api.app.state.settings.data_dir)
    store.set(credentials())
    before = store.path.read_bytes()
    install_provider(
        monkeypatch,
        lambda request: (
            httpx.Response(403, text=TOKEN)
            if request.url.path.endswith("/scripts")
            else provider(request)
        ),
    )
    response = api.put(PATH, json={"account_id": "b" * 32, "api_token": TOKEN})
    assert response.status_code == 502 and TOKEN not in response.text
    assert store.path.read_bytes() == before and store.get() == credentials()


def test_initial_denied_connection_saves_nothing(api, monkeypatch):
    install_provider(monkeypatch, lambda request: httpx.Response(403, text=TOKEN))
    response = api.put(PATH, json={"account_id": ACCOUNT, "api_token": TOKEN})
    assert response.status_code == 502 and TOKEN not in response.text
    assert not cf.CloudflareConnectionStore(api.app.state.settings.data_dir).path.exists()


def test_corrupt_saved_record_safe_status_and_clear(api):
    store = cf.CloudflareConnectionStore(api.app.state.settings.data_dir)
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(
        base64.b64encode(json.dumps({"version": 1, "api_token": TOKEN}).encode())
    )
    response = api.get(PATH)
    assert response.status_code == 200 and response.json()["connected"] is False
    assert response.json()["detail"] == cf.SAVED_UNREADABLE and TOKEN not in response.text
    assert api.delete(PATH).status_code == 204


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_desktop_only(api, method):
    api.app.state.settings.desktop_static_dir = None
    assert getattr(api, method)(PATH).status_code == 404
