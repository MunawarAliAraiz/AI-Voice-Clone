"""First-run audio runtime setup never executes or distributes unverified files."""

from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.audio_tools as audio
from app.api.routers.audio_tools import router
from app.audio_tools import AudioToolsController, require_audio_tools


@pytest.fixture
def archive(monkeypatch):
    entries = {
        "bin/ffmpeg.exe": ("ffmpeg.exe", b"test-runtime-not-executable"),
        "LICENSE": ("LICENSE.txt", b"publisher license"),
        "README.txt": ("README.txt", b"publisher readme"),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zip_file:
        for name, (_, content) in entries.items():
            zip_file.writestr(audio._PREFIX + name, content)
        zip_file.writestr("../../escape.txt", b"must not be extracted")
    data = buffer.getvalue()
    monkeypatch.setattr(
        audio,
        "FILES",
        {
            name: (out, hashlib.sha256(value).hexdigest(), len(value))
            for name, (out, value) in entries.items()
        },
    )
    monkeypatch.setattr(audio, "ARCHIVE_BYTES", len(data))
    monkeypatch.setattr(audio, "ARCHIVE_SHA256", hashlib.sha256(data).hexdigest())
    return data, entries


def controller(tmp_path, handler):
    return AudioToolsController(
        tmp_path, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


@pytest.mark.asyncio
async def test_download_hash_extract_exact_files_and_cached_adoption(tmp_path, archive):
    data, entries = archive
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=data)

    setup = controller(tmp_path, respond)
    assert (await setup.start())["ready"] is False
    await setup.task
    assert setup.snapshot()["ready"] is True
    assert setup.snapshot()["bytes_completed"] == len(data)
    for _, (name, expected) in entries.items():
        assert (setup.root / name).read_bytes() == expected
    assert not (tmp_path / "escape.txt").exists()
    assert len(calls) == 1
    adopted = controller(tmp_path, lambda _: pytest.fail("Verified cached tools must not download"))
    await adopted.start()
    await adopted.task
    assert adopted.snapshot()["ready"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("range_supported", [True, False])
async def test_resume_real_offset_or_reset_when_range_ignored(tmp_path, archive, range_supported):
    data, _ = archive

    def respond(request):
        assert request.headers["range"] == "bytes=20-"
        return (
            httpx.Response(
                206,
                headers={"content-range": f"bytes 20-{len(data) - 1}/{len(data)}"},
                content=data[20:],
            )
            if range_supported
            else httpx.Response(200, content=data)
        )

    setup = controller(tmp_path, respond)
    setup.root.mkdir(parents=True)
    (setup.root / "vendor.zip.part").write_bytes(data[:20])
    await setup.start()
    await setup.task
    assert setup.snapshot()["ready"] is True
    assert setup.snapshot()["bytes_completed"] == len(data)


@pytest.mark.asyncio
async def test_bad_hash_never_marks_ready_or_keeps_corrupt_partial(tmp_path, archive):
    data, _ = archive
    setup = controller(tmp_path, lambda _: httpx.Response(200, content=b"x" * len(data)))
    await setup.start()
    await setup.task
    assert setup.snapshot()["stage"] == "failed"
    assert not setup.snapshot()["ready"]
    assert not (setup.root / "ffmpeg.exe").exists()
    assert not (setup.root / "vendor.zip.part").exists()


@pytest.mark.asyncio
async def test_bad_range_and_redirect_are_refused(tmp_path, archive):
    data, _ = archive
    for response in [
        httpx.Response(302, headers={"location": "https://127.0.0.1/private"}),
        httpx.Response(206, headers={"content-range": "bytes 8-100/900"}, content=data),
    ]:
        setup = controller(tmp_path, lambda _: response)
        await setup.start()
        await setup.task
        assert not setup.snapshot()["ready"]
        assert not (setup.root / "ffmpeg.exe").exists()


@pytest.mark.asyncio
async def test_incorrect_extracted_binary_hash_is_refused(tmp_path, archive, monkeypatch):
    data, _ = archive
    files = dict(audio.FILES)
    output, _, size = files["bin/ffmpeg.exe"]
    files["bin/ffmpeg.exe"] = (output, "0" * 64, size)
    monkeypatch.setattr(audio, "FILES", files)
    setup = controller(tmp_path, lambda _: httpx.Response(200, content=data))
    await setup.start()
    await setup.task
    assert not setup.snapshot()["ready"]
    assert not (setup.root / "ffmpeg.exe").exists()
    assert not list(setup.root.glob("*.pending"))


@pytest.mark.asyncio
async def test_network_retry_preserves_partial_download(tmp_path, archive):
    data, _ = archive
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        if count == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, content=data)

    setup = controller(tmp_path, respond)
    await setup.start()
    await setup.task
    assert setup.snapshot()["ready"] and count == 2


@pytest.mark.asyncio
async def test_shutdown_cancels_download_and_cannot_run_two_writers(tmp_path, archive):
    entered = asyncio.Event()

    async def respond(request):
        entered.set()
        await asyncio.Event().wait()

    setup = controller(tmp_path, respond)
    await setup.start()
    original_task = setup.task
    await setup.start()
    assert setup.task is original_task
    await entered.wait()
    await setup.shutdown()
    assert setup.task.done()
    assert setup.snapshot()["stage"] == "cancelled"


def test_admission_is_explicit_and_web_is_unchanged():
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=SimpleNamespace(desktop_static_dir=None))
        )
    )
    require_audio_tools(request)
    request.app.state.settings.desktop_static_dir = Path("desktop")
    with pytest.raises(Exception) as error:
        require_audio_tools(request)
    assert error.value.status_code == 409
    request.app.state.audio_tools = SimpleNamespace(snapshot=lambda: {"ready": True})
    require_audio_tools(request)


def test_status_router_and_missing_web_controller(tmp_path):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        assert client.get("/api/audio-tools/status").status_code == 404
        app.state.audio_tools = AudioToolsController(tmp_path)
        response = client.get("/api/audio-tools/status")
        assert response.status_code == 200
        assert response.json()["ready"] is False


@pytest.mark.asyncio
async def test_download_timeout_reaches_terminal_failed_state(tmp_path, archive, monkeypatch):
    setup = controller(tmp_path, lambda _: pytest.fail("No network expected"))

    async def timeout(_archive):
        raise TimeoutError("deadline")

    monkeypatch.setattr(setup, "_download", timeout)
    await setup.start()
    await setup.task
    assert setup.snapshot()["stage"] == "failed"
    assert "timed out" in setup.snapshot()["detail"]
