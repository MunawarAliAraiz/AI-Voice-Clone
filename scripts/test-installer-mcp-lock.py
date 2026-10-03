"""Disposable NSIS hook test; never installs the app or writes its registry/profile.

Uses the production hooks and frozen guard/MCP. A real old MCP image stays
mapped during the probe, alongside an unrelated same-name binary. No cloud calls.
Requires packaging Python (MCP v2), Windows and the existing NSIS compiler.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.install_guard import WindowsPlatform, canonical_path


def mapped_pids(path: Path) -> list[int]:
    platform = WindowsPlatform()
    expected = canonical_path(str(path))
    found = []
    for pid in platform.processes():
        handle = platform.open_process(pid, terminate=False)
        if handle is None:
            continue
        try:
            image = platform.image(handle)
            if image and canonical_path(image) == expected:
                found.append(pid)
        finally:
            platform.close(handle)
    return found


async def handshake(path: Path, env: dict[str, str]) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async with (
        stdio_client(StdioServerParameters(command=str(path), args=[], env=env)) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        assert len((await session.list_tools()).tools) == 10


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--makensis", type=Path, required=True)
    parser.add_argument("--old-mcp", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    assert os.name == "nt"
    current = ROOT / "build/desktop/dist/voice-clone-mcp.exe"
    new_sha = hashlib.sha256(current.read_bytes()).hexdigest()
    sandbox = Path(tempfile.mkdtemp(prefix="vcs-installer-lock-"))
    target, unrelated = sandbox / "target", sandbox / "unrelated"
    target.mkdir()
    unrelated.mkdir()
    for folder in (target, unrelated):
        shutil.copyfile(args.old_mcp, folder / "voice-clone-mcp.exe")
    sentinel = target / "saved-voice.txt"
    sentinel.write_bytes(b"disposable local data must remain unchanged")
    probe = sandbox / "probe.nsi"
    probe_exe = sandbox / "probe.exe"
    probe.write_text(f'''Unicode true
OutFile "{probe_exe}"
RequestExecutionLevel user
SilentInstall silent
!include "{ROOT / 'frontend/src-tauri/installer-hooks.nsh'}"
Section
  SetOutPath $INSTDIR
  !insertmacro NSIS_HOOK_PREINSTALL
  File "/oname=voice-clone-mcp.exe" "{current}"
  !insertmacro NSIS_HOOK_POSTINSTALL
  FileOpen $0 "$INSTDIR\\completed.txt" w
  FileWrite $0 "verified"
  FileClose $0
SectionEnd
''', encoding="utf-8")
    subprocess.run([str(args.makensis), "-V2", str(probe)], check=True, capture_output=True)
    env = dict(os.environ)
    env.update(VCS_DATA_DIR=str(sandbox / "profile"),
               VCS_MCP_SESSION_FILE=str(sandbox / "profile/secrets/mcp-session.dpapi"))
    for name in ("VCS_MCP_API_BASE", "VCS_MCP_API_KEY", "VCS_REMOTE_WORKER_URL", "VCS_REMOTE_WORKER_TOKEN"):
        env.pop(name, None)
    children: list[subprocess.Popen] = []
    retained_handles = []
    checks = []
    try:
        for folder in (target, unrelated):
            children.append(subprocess.Popen([str(folder / "voice-clone-mcp.exe")],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=env, creationflags=subprocess.CREATE_NO_WINDOW))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if len(mapped_pids(target / "voice-clone-mcp.exe")) >= 2 and len(mapped_pids(unrelated / "voice-clone-mcp.exe")) >= 2:
                break
            assert all(child.poll() is None for child in children)
            time.sleep(0.2)
        else:
            raise AssertionError("Frozen MCP parent/child did not start")
        assert not WindowsPlatform().file_ready(str(target / "voice-clone-mcp.exe"))
        checks.append("reproduced real frozen MCP parent/child file lock")
        result = subprocess.run([str(probe_exe), "/S", f"/D={target}"], timeout=45, check=False,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0
        assert not mapped_pids(target / "voice-clone-mcp.exe")
        assert len(mapped_pids(unrelated / "voice-clone-mcp.exe")) >= 2
        assert children[1].poll() is None
        assert hashlib.sha256((target / "voice-clone-mcp.exe").read_bytes()).hexdigest() == new_sha
        assert (target / "completed.txt").read_text() == "verified"
        assert sentinel.read_bytes() == b"disposable local data must remain unchanged"
        checks.extend(["production NSIS preinstall stops exact frozen image and replaces it",
                       "unrelated same-name MCP parent/child remain running",
                       "postinstall exact MCP integrity check and local sentinel preservation"])
        asyncio.run(handshake(target / "voice-clone-mcp.exe", env))
        checks.append("replaced MCP initializes and exposes ten tools")

        # An unrelated file lock must cause a bounded failure before extraction.
        blocked = sandbox / "blocked"
        blocked.mkdir()
        blocked_file = blocked / "voice-clone-mcp.exe"
        blocked_file.write_bytes(b"locked existing file")
        platform = WindowsPlatform()
        lock = platform.api.CreateFileW(str(blocked_file), 0x80000000 | 0x40000000, 0, None, 3, 0x80, None)
        assert lock != platform.invalid_handle
        retained_handles.append((platform, lock))
        result = subprocess.run([str(probe_exe), "/S", f"/D={blocked}"], timeout=45, check=False,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 2 and not (blocked / "completed.txt").exists()
        platform.close(lock)
        retained_handles.clear()
        assert blocked_file.read_bytes() == b"locked existing file"
        checks.append("persistent unknown lock blocks before extraction; no success or file changes")

        # A skipped required file must not reach installer success/restart.
        skipped = sandbox / "skipped"
        skipped.mkdir()
        (skipped / "voice-clone-mcp.exe").write_bytes(b"old file skipped during extraction")
        no_copy_probe = sandbox / "probe-skipped.nsi"
        no_copy_exe = sandbox / "probe-skipped.exe"
        no_copy_probe.write_text(probe.read_text().replace(str(probe_exe), str(no_copy_exe)).replace(
            f'  File "/oname=voice-clone-mcp.exe" "{current}"', "  ; Simulate a skipped extraction."), encoding="utf-8")
        subprocess.run([str(args.makensis), "-V2", str(no_copy_probe)], check=True, capture_output=True)
        result = subprocess.run([str(no_copy_exe), "/S", f"/D={skipped}"], timeout=45, check=False,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 2 and not (skipped / "completed.txt").exists()
        checks.append("skipped or mismatched MCP extraction blocks postinstall success")
    finally:
        for platform, handle in retained_handles:
            platform.close(handle)
        # Cleanup only these two exact temporary MCP images, never by basename.
        for folder in (target, unrelated):
            platform = WindowsPlatform()
            for pid in mapped_pids(folder / "voice-clone-mcp.exe"):
                handle = platform.open_process(pid, terminate=True)
                if handle is None:
                    continue
                try:
                    image = platform.image(handle)
                    if image and canonical_path(image) == canonical_path(str(folder / "voice-clone-mcp.exe")):
                        platform.terminate(handle)
                        platform.wait(handle, 3)
                finally:
                    platform.close(handle)
        for child in children:
            if child.stdin:
                child.stdin.close()
            child.wait(timeout=10)
    receipt = {"version": "0.1.8", "scope": "production NSIS hooks in disposable probe; no app install/registry/profile/cloud changes",
               "checks": checks, "mcp_sha256": new_sha, "sandbox": str(sandbox)}
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
