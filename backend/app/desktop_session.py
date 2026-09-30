"""User-bound discovery of the current desktop API session for stdio MCP."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .runpod.secrets import _crypt


@dataclass(frozen=True)
class DesktopSession:
    port: int
    api_key: str
    pid: int

    def __post_init__(self) -> None:
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("Invalid desktop session port")
        if not isinstance(self.api_key, str) or len(self.api_key) < 32:
            raise ValueError("Invalid desktop session key")
        if type(self.pid) is not int or self.pid < 1:
            raise ValueError("Invalid desktop session process")

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def default_session_path() -> Path:
    override = os.environ.get("VCS_MCP_SESSION_FILE")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("Desktop session discovery requires Windows APPDATA")
    return Path(appdata) / "studio.voiceclone.desktop" / "secrets" / "mcp-session.dpapi"


class DesktopSessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> DesktopSession:
        value = json.loads(_crypt(self.path.read_bytes(), protect=False))
        if not isinstance(value, dict):
            raise ValueError("Invalid desktop session descriptor")
        return DesktopSession(port=value["port"], api_key=value["api_key"], pid=value["pid"])

    def save(self, session: DesktopSession) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"port": session.port, "api_key": session.api_key, "pid": session.pid}
        ).encode("utf-8")
        temporary = self.path.with_suffix(f".{session.pid}.tmp")
        temporary.write_bytes(_crypt(payload, protect=True))
        temporary.replace(self.path)

    def clear_if_current(self, session: DesktopSession) -> None:
        try:
            current = self.load()
        except (OSError, ValueError, KeyError, RuntimeError):
            return
        if current == session:
            self.path.unlink(missing_ok=True)
