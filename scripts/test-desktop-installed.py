import argparse
import asyncio
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.desktop_session import DesktopSessionStore

parser = argparse.ArgumentParser(
    description="Test an installed desktop app in a fresh isolated data directory."
)
parser.add_argument("--install-dir", type=Path, required=True)
parser.add_argument("--data-dir", type=Path, required=True)
parser.add_argument("--receipt", type=Path, required=True)
options = parser.parse_args()
install = options.install_dir.resolve()
data = options.data_dir.resolve()
descriptor = data / "secrets" / "mcp-session.dpapi"
shell = install / "voice-clone-desktop.exe"
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [CALLBACK, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
user32.PostMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]


def alive(pid):
    handle = kernel32.OpenProcess(0x100000, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x102
    finally:
        kernel32.CloseHandle(handle)


def windows(pid):
    found = []

    @CALLBACK
    def collect(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, len(title))
        if owner.value == pid and title.value == "AI Voice Clone Studio":
            found.append(hwnd)
        return True

    user32.EnumWindows(collect, 0)
    return found


async def check_mcp(env):
    async with (
        stdio_client(
            StdioServerParameters(command=str(install / "voice-clone-mcp.exe"), args=[], env=env)
        ) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        listing = await session.list_tools()
        assert len(listing.tools) == 10
        assert {"preview_speech_direction", "wait_for_job"} <= {tool.name for tool in listing.tools}
        health = await session.call_tool("studio_health", {})
        assert not health.is_error, health
        voices = await session.call_tool("list_voices", {})
        assert not voices.is_error, voices
    print(
        "PASS: installed MCP initializes and authenticates through DPAPI to native app",
        flush=True,
    )


def main():
    assert shell.is_file(), shell
    assert not data.exists(), "Use a fresh isolated test data directory"
    assert not (install / "ffmpeg.exe").exists(), (
        "Audio tools must not be bundled in this installer"
    )
    env = dict(os.environ)
    env.update(
        VCS_DESKTOP_DATA_DIR=str(data),
        VCS_DESKTOP_TEST_HIDE="1",
        VCS_MCP_SESSION_FILE=str(descriptor),
        VCS_DESKTOP_AUDIO_TOOLS_AUTOSTART="false",
    )
    env.pop("VCS_MCP_API_BASE", None)
    env.pop("VCS_MCP_API_KEY", None)
    env.pop("VCS_API_KEY", None)
    proc = subprocess.Popen([str(shell)], env=env)  # noqa: S603 -- exact test install executable
    discovered = None
    api_handle = None
    api_verified = False
    try:
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            assert proc.poll() is None, f"Native shell exited {proc.returncode}"
            if descriptor.is_file():
                discovered = DesktopSessionStore(descriptor).load()
                try:
                    if httpx.get(
                        discovered.base_url + "/api/health", timeout=2
                    ).status_code == 200 and windows(proc.pid):
                        break
                except httpx.HTTPError:
                    pass
            time.sleep(0.2)
        else:
            raise AssertionError("Native API/WebView startup timeout")
        api_handle = kernel32.OpenProcess(0x100000 | 0x1000 | 0x0001, False, discovered.pid)
        assert api_handle, "Cannot retain the API process handle for test cleanup"
        api_image = ctypes.create_unicode_buffer(32768)
        image_length = wintypes.DWORD(len(api_image))
        assert kernel32.QueryFullProcessImageNameW(
            api_handle, 0, api_image, ctypes.byref(image_length)
        )
        assert Path(api_image.value).resolve() == (install / "voice-clone-api.exe").resolve()
        api_verified = True
        with httpx.Client(base_url=discovered.base_url, timeout=5) as client:
            assert client.get("/api/voices").status_code == 401
            html = client.get("/")
            assert html.status_code == 200 and "__VCS_DESKTOP_KEY__" in html.text
            client.headers["X-API-Key"] = discovered.api_key
            assert client.get("/api/voices").status_code == 200
            assert client.get("/api/agents").status_code == 200
            audio = client.get("/api/audio-tools/status")
            assert audio.status_code == 200
            assert audio.json()["ready"] is False and audio.json()["stage"] == "idle"
            assert audio.json()["bytes_completed"] == 0
            setup = client.get("/api/runpod/setup")
            assert setup.status_code == 200
            assert setup.json()["connected"] is False
            assert setup.json()["ready"] is False
            assert setup.json()["compute"] is None
            generation = client.post("/api/generate", json={"text": "Setup gate test"})
            assert generation.status_code == 409
            assert "audio tools" in generation.json()["detail"].lower()
            # Quiesce only this disposable test profile; cancellation restores
            # normal API mutation admission without starting an installer.
            assert client.post("/api/desktop/updates/prepare").json() == {"prepared": True}
            assert client.post("/api/desktop/updates/prepare").json() == {"prepared": True}
            assert client.get("/api/voices").status_code == 200
            assert client.post("/api/generate", json={"text": "Fenced"}).status_code == 409
            assert client.post("/api/desktop/updates/cancel").json() == {"prepared": False}
        assert (data / "voiceclone.db").is_file()
        assert (data / "webview").is_dir()
        print(
            "PASS: installed shell starts hidden isolated WebView/API; frontend, auth, SQLite work",
            flush=True,
        )
        asyncio.run(check_mcp(env))
        second = subprocess.Popen([str(shell)], env=env)  # noqa: S603 -- exact test install executable
        assert second.wait(timeout=20) == 0
        assert DesktopSessionStore(descriptor).load() == discovered
        assert proc.poll() is None
        print(
            "PASS: second desktop launch exits without replacing the API session",
            flush=True,
        )
        owned = windows(proc.pid)
        assert owned
        for hwnd in owned:
            assert user32.PostMessageW(hwnd, 0x10, 0, 0)
        assert proc.wait(timeout=30) == 0
        deadline = time.monotonic() + 15
        while alive(discovered.pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not alive(discovered.pid), "API child remained alive after shell closed"
        try:
            httpx.get(discovered.base_url + "/api/health", timeout=1)
        except httpx.HTTPError:
            pass
        else:
            raise AssertionError("API still served health after desktop close")
        print(
            "PASS: native window close exits shell and stops API process/listener",
            flush=True,
        )
        options.receipt.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "installed_app": str(shell),
                    "isolated_data": str(data),
                    "checks": [
                        "native startup",
                        "frontend/auth/sqlite",
                        "agent status and missing audio tools admission gate",
                        "cloud setup status and generation admission gate",
                        "idle updater quiesce, idempotent prepare and cancel",
                        "installed MCP bridge",
                        "single-instance session",
                        "window close API cleanup",
                    ],
                }
            ),
            encoding="utf-8",
        )
    finally:
        if proc.poll() is None:
            for hwnd in windows(proc.pid):
                user32.PostMessageW(hwnd, 0x10, 0, 0)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=10)
        if api_handle:
            if api_verified and kernel32.WaitForSingleObject(api_handle, 0) == 0x102:
                kernel32.TerminateProcess(api_handle, 1)
            kernel32.CloseHandle(api_handle)


if __name__ == "__main__":
    main()
