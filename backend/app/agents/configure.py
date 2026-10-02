"""Edit one requested MCP entry without exposing other host settings."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import tomllib
from datetime import UTC, datetime
from pathlib import Path

SERVER_NAME = "voice_clone_studio"
_LOCK = threading.Lock()
_MAX_CONFIG_BYTES = 8 * 1024 * 1024


class AgentConfigError(ValueError):
    pass


class AgentConnections:
    def __init__(
        self,
        data_dir: Path,
        *,
        home: Path | None = None,
        appdata: Path | None = None,
        mcp_executable: Path | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.home = home or Path.home()
        self.appdata = appdata or Path(os.environ.get("APPDATA", self.home / "AppData/Roaming"))
        codex_home = (
            Path(os.environ.get("CODEX_HOME", self.home / ".codex"))
            if home is None
            else self.home / ".codex"
        )
        self.paths = {
            "codex": codex_home / "config.toml",
            "claude_desktop": self.appdata / "Claude/claude_desktop_config.json",
            "claude_code": self.home / ".claude.json",
        }
        self.labels = {
            "codex": "Codex",
            "claude_desktop": "Claude Desktop",
            "claude_code": "Claude Code",
        }
        if mcp_executable is None and getattr(sys, "frozen", False):
            mcp_executable = Path(sys.executable).parent / "voice-clone-mcp.exe"
        self.executable = mcp_executable

    def launch_entry(self, client: str) -> dict | None:
        executable = self.executable
        if (
            executable is None
            or not executable.is_absolute()
            or not executable.is_file()
            or executable.is_symlink()
        ):
            return None
        entry = {"command": str(executable), "args": []}
        if client == "codex":
            entry.update(enabled=True, tool_timeout_sec=60)
        elif client == "claude_code":
            entry["type"] = "stdio"
        return entry

    @staticmethod
    def _read(path: Path, *, toml: bool) -> tuple[bytes, dict]:
        if any(p.is_symlink() or p.is_junction() for p in [path, *path.parents]):
            raise AgentConfigError(
                "Agent configuration uses a symbolic link; configure this server manually"
            )
        if not path.exists():
            return b"", {}
        if not path.is_file() or path.stat().st_size > _MAX_CONFIG_BYTES:
            raise AgentConfigError("Agent configuration cannot be safely read")
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
            value = tomllib.loads(text) if toml else json.loads(text)
        except (UnicodeError, ValueError) as exc:
            raise AgentConfigError(
                "Agent configuration is invalid; repair it before connecting"
            ) from exc
        if not isinstance(value, dict):
            raise AgentConfigError("Agent configuration must be an object")
        return raw, value

    def status(self) -> dict:
        clients = []
        for client, path in self.paths.items():
            error = None
            configured = False
            conflict = False
            try:
                _, config = self._read(path, toml=client == "codex")
                servers = config.get("mcp_servers" if client == "codex" else "mcpServers", {})
                if not isinstance(servers, dict):
                    raise AgentConfigError("Agent MCP configuration must be an object")
                expected = self.launch_entry(client)
                entry = servers.get(SERVER_NAME)
                configured = bool(expected and entry == expected)
                conflict = entry is not None and not configured
            except (AgentConfigError, OSError) as exc:
                error = (
                    str(exc)
                    if isinstance(exc, AgentConfigError)
                    else "Cannot read agent configuration"
                )
            detected = path.exists() or (
                path.parent.is_dir()
                if client != "claude_code"
                else (self.home / ".claude").is_dir()
            )
            if client in {"codex", "claude_code"}:
                detected = (
                    detected or shutil.which("codex" if client == "codex" else "claude") is not None
                )
            clients.append(
                {
                    "id": client,
                    "name": self.labels[client],
                    "detected": detected,
                    "configured": configured,
                    "conflict": conflict,
                    "config_path": str(path),
                    "can_configure": detected
                    and self.launch_entry(client) is not None
                    and error is None,
                    "error": error,
                }
            )
        activity = None
        try:
            value = json.loads((self.data_dir / "agents/activity.json").read_text(encoding="utf-8"))
            if isinstance(value, dict):
                activity = {k: value.get(k) for k in ("last_tool_call_at", "tool")}
        except (OSError, ValueError):
            pass
        return {
            "clients": clients,
            "mcp_available": self.launch_entry("codex") is not None,
            "activity": activity,
        }

    def configure(self, client: str, *, replace_existing: bool = False) -> dict:
        if client not in self.paths:
            raise AgentConfigError("Unknown agent client")
        with _LOCK:
            status = next(c for c in self.status()["clients"] if c["id"] == client)
            if not status["can_configure"]:
                raise AgentConfigError(
                    status["error"]
                    or "Install and open the agent and desktop studio before connecting"
                )
            entry = self.launch_entry(client)
            path = self.paths[client]
            original, value = self._read(path, toml=client == "codex")
            field = "mcp_servers" if client == "codex" else "mcpServers"
            servers = value.get(field, {})
            existing = servers.get(SERVER_NAME)
            if existing == entry:
                return {"configured": True, "changed": False, "restart_required": True}
            if existing is not None and not replace_existing:
                raise AgentConfigError(
                    "This server name already exists; explicitly approve replacing its entry"
                )
            if client == "codex":
                import tomlkit

                document = tomlkit.parse(original.decode("utf-8-sig"))
                if field not in document:
                    document[field] = tomlkit.table()
                document[field][SERVER_NAME] = entry
                output = tomlkit.dumps(document).encode("utf-8")
            else:
                value.setdefault(field, {})[SERVER_NAME] = entry
                output = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".voice-clone-", dir=path.parent)
            backup = None
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(output)
                    stream.flush()
                    os.fsync(stream.fileno())
                current, _ = self._read(path, toml=client == "codex")
                if current != original:
                    raise AgentConfigError("Agent configuration changed; refresh and try again")
                if original:
                    backup_fd, backup_name = tempfile.mkstemp(
                        prefix=f"{path.name}.voice-clone-backup-", dir=path.parent
                    )
                    backup = Path(backup_name)
                    with os.fdopen(backup_fd, "wb") as stream:
                        stream.write(original)
                    shutil.copystat(path, temporary)
                    shutil.copystat(path, backup)
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return {
                "configured": True,
                "changed": True,
                "restart_required": True,
                "backup_created": backup is not None,
            }

    def record_activity(self, tool: str) -> None:
        if tool not in {
            "/api/health",
            "/api/voices",
            "/api/models",
            "/api/generate",
            "/api/direction/analyze",
            "/api/jobs",
            "/api/history",
        }:
            tool = "/api/jobs"
        path = self.data_dir / "agents/activity.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix="activity-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(
                    {"last_tool_call_at": datetime.now(UTC).isoformat(), "tool": tool}, stream
                )
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
