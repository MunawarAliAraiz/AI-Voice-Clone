"""Bounded, exact-image Windows installer guard. No studio or agent credentials."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import ntpath
import os
import re
import sys
import time
from ctypes import wintypes
from typing import Protocol

BLOCKED_MESSAGE = (
    "Close the Voice Clone Studio connection in your agent, then retry the update. "
    "The installer could not safely replace its agent connection file."
)
VERIFY_BLOCKED_MESSAGE = (
    "The agent connection file did not install correctly. "
    "Close its connection in your agent and retry the update."
)


def canonical_path(path: str) -> str:
    """Resolve Windows links/case, including extended-length process image paths."""
    value = os.path.realpath(path)
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normcase(ntpath.normpath(value))


class Platform(Protocol):
    def processes(self) -> list[int]: ...
    def open_process(self, pid: int, *, terminate: bool) -> object | None: ...
    def image(self, handle: object) -> str | None: ...
    def terminate(self, handle: object) -> bool: ...
    def wait(self, handle: object, seconds: float) -> bool: ...
    def close(self, handle: object) -> None: ...
    def file_ready(self, path: str) -> bool: ...


def installed_mcp_path(install_dir: str) -> str:
    drive, _ = ntpath.splitdrive(install_dir)
    if not drive or not ntpath.isabs(install_dir):
        raise ValueError("Absolute installation directory required")
    return canonical_path(ntpath.join(install_dir, "voice-clone-mcp.exe"))


def verify_mcp(install_dir: str, expected_sha256: str) -> bool:
    """Check installed bytes only: never constructs the process-management API."""
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        return False
    try:
        digest = hashlib.sha256()
        with open(installed_mcp_path(install_dir), "rb") as stream:
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
        return digest.hexdigest() == expected_sha256
    except Exception:
        return False


class WindowsPlatform:
    """Query and stop through the same retained handle, never a later PID lookup."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows required")
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)

        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
            ]

        self.entry_type = ProcessEntry
        declarations = {
            "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "Process32FirstW": ([wintypes.HANDLE, ctypes.POINTER(ProcessEntry)], wintypes.BOOL),
            "Process32NextW": ([wintypes.HANDLE, ctypes.POINTER(ProcessEntry)], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "QueryFullProcessImageNameW": (
                [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)],
                wintypes.BOOL,
            ),
            "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "CreateFileW": (
                [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE], wintypes.HANDLE,
            ),
        }
        for name, (arguments, result) in declarations.items():
            function = getattr(self.api, name)
            function.argtypes = arguments
            function.restype = result
        self.invalid_handle = ctypes.c_void_p(-1).value

    def processes(self) -> list[int]:
        snapshot = self.api.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == self.invalid_handle:
            raise OSError("Process inspection unavailable")
        try:
            entry = self.entry_type()
            entry.dwSize = ctypes.sizeof(entry)
            found = self.api.Process32FirstW(snapshot, ctypes.byref(entry))
            pids = []
            while found:
                if entry.th32ProcessID:
                    pids.append(int(entry.th32ProcessID))
                found = self.api.Process32NextW(snapshot, ctypes.byref(entry))
            # ERROR_NO_MORE_FILES is the only normal end of a snapshot.
            if ctypes.get_last_error() != 18:
                raise OSError("Process inspection incomplete")
            return pids
        finally:
            self.close(snapshot)

    def open_process(self, pid: int, *, terminate: bool) -> object | None:
        rights = 0x1000 | 0x00100000  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
        if terminate:
            rights |= 0x0001
        return self.api.OpenProcess(rights, False, pid) or None

    def image(self, handle: object) -> str | None:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not self.api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value

    def terminate(self, handle: object) -> bool:
        return bool(self.api.TerminateProcess(handle, 0))

    def wait(self, handle: object, seconds: float) -> bool:
        return self.api.WaitForSingleObject(handle, max(0, int(seconds * 1000))) == 0

    def close(self, handle: object) -> None:
        if not self.api.CloseHandle(handle):
            raise OSError("Handle cleanup failed")

    def file_ready(self, path: str) -> bool:
        # OPEN_EXISTING: the probe neither creates nor writes/truncates this file.
        handle = self.api.CreateFileW(path, 0x80000000 | 0x40000000, 0, None, 3, 0x80, None)
        if handle == self.invalid_handle:
            return ctypes.get_last_error() in {2, 3}  # absent file/directory only
        self.close(handle)
        return True


def ensure_ready(
    install_dir: str,
    platform: Platform,
    *,
    check_only: bool = False,
    clock=time.monotonic,
    sleep=time.sleep,
) -> bool:
    """Release only this installed MCP image, then require a quiet unlocked interval."""
    try:
        target = installed_mcp_path(install_dir)
    except (ValueError, OSError):
        return False
    deadline = clock() + 15.0
    quiet_since = None
    stop_rounds = 0
    try:
        for _ in range(75):  # Time and iteration bounds also cover persistent respawn.
            if clock() >= deadline:
                return False
            matched = False
            for pid in platform.processes():
                if clock() >= deadline:
                    return False
                handle = platform.open_process(pid, terminate=not check_only)
                if handle is None:
                    # Protected/unqueryable unrelated processes are common. They
                    # cannot grant readiness: the exclusive file probe still must pass.
                    continue
                try:
                    image = platform.image(handle)
                    if image is None or canonical_path(image) != target:
                        continue
                    matched = True
                    if check_only or stop_rounds >= 3:
                        return False
                    # Keep this verified handle through termination/wait. PID reuse
                    # cannot change its identity to an unrelated process.
                    if not platform.terminate(handle):
                        return False
                    if not platform.wait(handle, min(0.25, max(0, deadline - clock()))):
                        return False
                finally:
                    platform.close(handle)
            if matched:
                stop_rounds += 1
                quiet_since = None
            ready = platform.file_ready(target)
            if check_only:
                return ready
            if ready and not matched:
                if quiet_since is None:
                    quiet_since = clock()
                elif clock() - quiet_since >= 0.75:
                    return True
            else:
                quiet_since = None
            sleep(min(0.2, max(0, deadline - clock())))
    except Exception:
        # This isolated installer process must fail closed without leaking any
        # exception text, process paths or command-line values to NSIS logs.
        return False
    return False


def main(argv: list[str] | None = None) -> int:
    class QuietParser(argparse.ArgumentParser):
        def error(self, message: str) -> None:
            raise ValueError("Invalid installer arguments")

    parser = QuietParser(add_help=False)
    parser.add_argument("--install-dir", required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-only", action="store_true")
    modes.add_argument("--verify-mcp-sha256")
    blocked_message = BLOCKED_MESSAGE
    try:
        options = parser.parse_args(argv)
        if options.verify_mcp_sha256 is not None:
            blocked_message = VERIFY_BLOCKED_MESSAGE
            ready = os.name == "nt" and verify_mcp(
                options.install_dir, options.verify_mcp_sha256
            )
        else:
            ready = ensure_ready(
                options.install_dir, WindowsPlatform(), check_only=options.check_only
            )
    except Exception:
        ready = False
    print(json.dumps({
        "ready": ready, "message": "Ready to install." if ready else blocked_message,
    }))
    return 0 if ready else 2


if __name__ == "__main__":
    sys.exit(main())
