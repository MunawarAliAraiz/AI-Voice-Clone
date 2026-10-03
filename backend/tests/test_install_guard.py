"""Exact-image process/file guard checks; never inspect or stop real processes."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import Mock

import pytest

from app import install_guard as guard

INSTALL = r"C:\Apps\Voice Studio"
TARGET = INSTALL + r"\voice-clone-mcp.exe"


@dataclass
class Handle:
    pid: int
    image: str


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        assert 0 <= seconds <= 0.2
        self.sleeps.append(seconds)
        self.now += seconds


class FakePlatform:
    def __init__(self, images=None, *, unlocked=True, respawn=False):
        self.images = dict(images or {})
        self.unlocked = unlocked
        self.respawn = respawn
        self.opened = []
        self.queried = []
        self.stopped = []
        self.waited = []
        self.closed = []
        self.probed = []
        self.inaccessible = set()
        self.unqueryable = set()
        self.enumerations = 0
        self.wait_result = True
        self.terminate_result = True

    def processes(self):
        self.enumerations += 1
        return list(self.images)

    def open_process(self, pid, *, terminate):
        self.opened.append((pid, terminate))
        if pid in self.inaccessible:
            return None
        return Handle(pid, self.images[pid])

    def image(self, handle):
        self.queried.append(handle)
        return None if handle.pid in self.unqueryable else handle.image

    def terminate(self, handle):
        self.stopped.append(handle)
        assert handle in self.queried
        if not self.terminate_result:
            return False
        if not self.respawn and self.images.get(handle.pid) == handle.image:
            del self.images[handle.pid]
        return True

    def wait(self, handle, seconds):
        assert handle is self.stopped[-1]
        assert 0 <= seconds <= 0.25
        self.waited.append(handle)
        return self.wait_result

    def close(self, handle):
        self.closed.append(handle)

    def file_ready(self, path):
        self.probed.append(path)
        return self.unlocked


@pytest.fixture(autouse=True)
def lexical_canonicalization(monkeypatch):
    # Fixture paths need no filesystem and run on Windows/Linux test machines.
    monkeypatch.setattr(guard.os.path, "realpath", lambda value: value)


def run(platform, *, check_only=False):
    clock = Clock()
    result = guard.ensure_ready(
        INSTALL, platform, check_only=check_only, clock=clock, sleep=clock.sleep
    )
    return result, clock


@pytest.mark.parametrize("image", [
    TARGET, r"c:\APPS\Voice Studio\VOICE-CLONE-MCP.EXE",
    r"\\?\C:\Apps\Voice Studio\voice-clone-mcp.exe",
    r"C:/Apps/Voice Studio/voice-clone-mcp.exe",
    r"C:\Apps\Voice Studio\subdir\..\voice-clone-mcp.exe",
])
def test_canonical_exact_image_is_stopped_then_quiet(image):
    platform = FakePlatform({31: image})
    ready, clock = run(platform)
    assert ready
    assert [h.pid for h in platform.stopped] == [31]
    assert 0.75 <= clock.now < 15
    assert platform.probed == [guard.canonical_path(TARGET)] * len(platform.probed)


def test_parent_child_both_stopped_but_same_name_elsewhere_and_agent_preserved():
    platform = FakePlatform({
        10: TARGET, 11: TARGET,
        12: r"C:\Other Studio\voice-clone-mcp.exe",
        13: r"C:\Apps\Codex\Codex.exe",
        14: r"C:\Apps\Claude\Claude.exe",
    })
    assert run(platform)[0]
    assert [h.pid for h in platform.stopped] == [10, 11]
    assert set(platform.images) == {12, 13, 14}
    assert all(any(h is closed for closed in platform.closed) for h in platform.queried)


def test_same_verified_handle_survives_pid_reuse():
    class ReusingPlatform(FakePlatform):
        def image(self, handle):
            result = super().image(handle)
            self.images[handle.pid] = r"C:\Unrelated\editor.exe"
            return result

    platform = ReusingPlatform({20: TARGET})
    assert run(platform)[0]
    assert platform.stopped[0] is platform.queried[0]
    assert platform.waited[0] is platform.stopped[0]
    assert platform.images[20] == r"C:\Unrelated\editor.exe"


@pytest.mark.parametrize("unknown_type", ["inaccessible", "unqueryable"])
@pytest.mark.parametrize("unlocked", [True, False])
def test_unknown_process_never_stopped_and_cannot_bypass_file_lock(unknown_type, unlocked):
    platform = FakePlatform({21: TARGET}, unlocked=unlocked)
    getattr(platform, unknown_type).add(21)
    assert run(platform)[0] is unlocked
    assert not platform.stopped


@pytest.mark.parametrize("unlocked", [True, False])
def test_check_only_probes_without_termination_rights_or_stops(unlocked):
    platform = FakePlatform({10: r"C:\Other\voice-clone-mcp.exe"}, unlocked=unlocked)
    result, clock = run(platform, check_only=True)
    assert result is unlocked
    assert platform.opened == [(10, False)]
    assert not platform.stopped and not platform.waited and clock.now == 0


def test_check_only_blocks_known_live_target_without_stopping_it():
    platform = FakePlatform({10: TARGET})
    assert not run(platform, check_only=True)[0]
    assert not platform.stopped
    assert platform.opened == [(10, False)]
    assert platform.closed[0] is platform.queried[0]


def test_absent_or_unlocked_file_without_process_requires_quiet_interval():
    platform = FakePlatform()
    ready, clock = run(platform)
    assert ready and clock.now >= 0.75
    assert platform.enumerations >= 5


def test_locked_or_access_denied_file_has_time_and_retry_bounds():
    platform = FakePlatform(unlocked=False)
    ready, clock = run(platform)
    assert not ready
    assert clock.now <= 15
    assert platform.enumerations <= 75
    assert not platform.stopped


def test_host_respawn_is_bounded_and_does_not_report_ready():
    platform = FakePlatform({10: TARGET}, respawn=True)
    ready, clock = run(platform)
    assert not ready
    assert len(platform.stopped) == 3
    assert platform.enumerations == 4
    assert clock.now < 15


@pytest.mark.parametrize("failure", ["terminate_result", "wait_result"])
def test_failed_stop_or_timeout_blocks_and_closes_same_handle(failure):
    platform = FakePlatform({10: TARGET})
    setattr(platform, failure, False)
    assert not run(platform)[0]
    assert platform.closed[0] is platform.queried[0]


def test_enumeration_failure_blocks():
    class BrokenPlatform(FakePlatform):
        def processes(self):
            raise OSError("private user path must not leak")

    assert not run(BrokenPlatform())[0]


@pytest.mark.parametrize("path", ["relative", "", r"C:relative", r"\rooted"])
def test_invalid_install_directory_is_not_inspected(path):
    platform = FakePlatform()
    assert not guard.ensure_ready(path, platform)
    assert platform.enumerations == 0


def test_guard_cli_failure_is_fixed_json_without_exception_or_argument_leak(monkeypatch, capsys):
    def fail():
        raise OSError("PRIVATE_TOKEN and user path")

    monkeypatch.setattr(guard, "WindowsPlatform", fail)
    assert guard.main(["--install-dir", INSTALL]) == 2
    result = capsys.readouterr()
    assert json.loads(result.out) == {"ready": False, "message": guard.BLOCKED_MESSAGE}
    assert not result.err
    assert "PRIVATE_TOKEN" not in result.out and INSTALL not in result.out
    assert guard.main(["--install-dir", INSTALL, "--PRIVATE_TOKEN"]) == 2
    assert not capsys.readouterr().err


def test_off_windows_fails_closed(monkeypatch):
    monkeypatch.setattr(guard.os, "name", "posix")
    with pytest.raises(OSError, match="Windows required"):
        guard.WindowsPlatform()


@pytest.mark.parametrize("error,ready", [(2, True), (3, True), (5, False), (32, False)])
def test_native_file_probe_missing_only_is_allowed_and_does_not_create(monkeypatch, error, ready):
    platform = object.__new__(guard.WindowsPlatform)
    platform.invalid_handle = -1
    platform.api = SimpleNamespace(CreateFileW=Mock(return_value=-1), CloseHandle=Mock())
    monkeypatch.setattr(guard.ctypes, "get_last_error", lambda: error)
    assert platform.file_ready(TARGET) is ready
    arguments = platform.api.CreateFileW.call_args.args
    assert arguments == (TARGET, 0xC0000000, 0, None, 3, 0x80, None)
    assert not platform.api.CloseHandle.called


def test_native_successful_exclusive_probe_closes_without_writing():
    platform = object.__new__(guard.WindowsPlatform)
    platform.invalid_handle = -1
    platform.api = SimpleNamespace(
        CreateFileW=Mock(return_value=991), CloseHandle=Mock(return_value=1),
    )
    assert platform.file_ready(TARGET)
    platform.api.CloseHandle.assert_called_once_with(991)


def test_native_open_requests_only_query_sync_and_optional_terminate():
    platform = object.__new__(guard.WindowsPlatform)
    platform.api = SimpleNamespace(OpenProcess=Mock(return_value=111))
    assert platform.open_process(99, terminate=True) == 111
    platform.api.OpenProcess.assert_called_with(0x00101001, False, 99)
    assert platform.open_process(99, terminate=False) == 111
    platform.api.OpenProcess.assert_called_with(0x00101000, False, 99)


@pytest.mark.parametrize("end_error,success", [(18, True), (5, False)])
def test_native_toolhelp_closes_snapshot_and_rejects_incomplete_enumeration(
    monkeypatch, end_error, success,
):
    class Entry(guard.ctypes.Structure):
        _fields_: ClassVar = [
            ("dwSize", guard.wintypes.DWORD), ("th32ProcessID", guard.wintypes.DWORD),
        ]

    def first(_snapshot, pointer):
        pointer._obj.th32ProcessID = 123
        return 1

    platform = object.__new__(guard.WindowsPlatform)
    platform.invalid_handle = -1
    platform.entry_type = Entry
    platform.api = SimpleNamespace(
        CreateToolhelp32Snapshot=Mock(return_value=777),
        Process32FirstW=Mock(side_effect=first), Process32NextW=Mock(return_value=0),
        CloseHandle=Mock(return_value=1),
    )
    monkeypatch.setattr(guard.ctypes, "get_last_error", lambda: end_error)
    if success:
        assert platform.processes() == [123]
    else:
        with pytest.raises(OSError, match="incomplete"):
            platform.processes()
    platform.api.CloseHandle.assert_called_once_with(777)


def test_integrity_mode_hashes_exact_installed_path_in_chunks_without_platform(
    monkeypatch, capsys,
):
    contents = b"new frozen MCP image" * 12000
    expected = hashlib.sha256(contents).hexdigest()
    reads = []

    class RecordingStream(io.BytesIO):
        def read(self, size):
            reads.append(size)
            return super().read(size)

    def open_image(path, mode):
        assert path == guard.canonical_path(TARGET)
        assert mode == "rb"
        return RecordingStream(contents)

    def forbidden_platform():
        pytest.fail("Verification must not construct process-management API")

    monkeypatch.setattr(guard, "WindowsPlatform", forbidden_platform)
    monkeypatch.setattr(guard, "open", open_image, raising=False)
    monkeypatch.setattr(guard.os, "name", "nt")
    assert guard.main(["--install-dir", INSTALL, "--verify-mcp-sha256", expected]) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True
    assert len(reads) > 2 and set(reads) == {64 * 1024}


@pytest.mark.parametrize("failure", ["missing", "denied", "different", "read_error"])
def test_integrity_mode_blocks_incomplete_install_without_raw_error(
    monkeypatch, capsys, failure,
):
    expected = hashlib.sha256(b"expected MCP image").hexdigest()

    class BrokenStream(io.BytesIO):
        def read(self, size):
            raise OSError("PRIVATE_PATH failed during read")

    def open_image(_path, _mode):
        if failure == "missing":
            raise FileNotFoundError("PRIVATE_PATH")
        if failure == "denied":
            raise PermissionError("PRIVATE_PATH")
        if failure == "read_error":
            return BrokenStream()
        return io.BytesIO(b"old image that NSIS Ignore left behind")

    monkeypatch.setattr(guard, "open", open_image, raising=False)
    monkeypatch.setattr(guard.os, "name", "nt")
    monkeypatch.setattr(guard, "WindowsPlatform", lambda: pytest.fail("Process API invoked"))
    assert guard.main(["--install-dir", INSTALL, "--verify-mcp-sha256", expected]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ready": False, "message": guard.VERIFY_BLOCKED_MESSAGE}
    assert not output.err and "PRIVATE_PATH" not in output.out


@pytest.mark.parametrize("sha", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, " " + "a" * 64])
def test_invalid_integrity_hash_never_opens_file(monkeypatch, sha):
    monkeypatch.setattr(guard, "open", lambda *_: pytest.fail("File opened"), raising=False)
    assert not guard.verify_mcp(INSTALL, sha)


def test_integrity_and_check_only_are_mutually_exclusive(monkeypatch, capsys):
    monkeypatch.setattr(guard, "WindowsPlatform", lambda: pytest.fail("Process API invoked"))
    monkeypatch.setattr(guard, "open", lambda *_: pytest.fail("File opened"), raising=False)
    assert guard.main([
        "--install-dir", INSTALL, "--check-only", "--verify-mcp-sha256", "a" * 64,
    ]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["ready"] is False and not output.err


def test_integrity_mode_off_windows_fails_without_platform_or_file(monkeypatch, capsys):
    monkeypatch.setattr(guard.os, "name", "posix")
    monkeypatch.setattr(guard, "WindowsPlatform", lambda: pytest.fail("Process API invoked"))
    monkeypatch.setattr(guard, "open", lambda *_: pytest.fail("File opened"), raising=False)
    assert guard.main(["--install-dir", INSTALL, "--verify-mcp-sha256", "a" * 64]) == 2
    assert json.loads(capsys.readouterr().out)["ready"] is False
