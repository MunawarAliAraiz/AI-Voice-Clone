"""Loopback-only entry point for the packaged Windows desktop sidecar."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

from .config import Settings
from .desktop_session import DesktopSession, DesktopSessionStore
from .main import create_app


def main() -> None:
    settings = Settings()
    if settings.desktop_static_dir is None:
        packaged = Path(getattr(sys, "_MEIPASS", "")) / "web"
        if packaged.is_dir():
            settings.desktop_static_dir = packaged
        else:
            raise SystemExit("VCS_DESKTOP_STATIC_DIR must point to built frontend assets")
    if not settings.api_key:
        raise SystemExit("VCS_API_KEY must be a fresh desktop session key")
    raw_port = os.environ.get("VCS_DESKTOP_PORT", "")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit("VCS_DESKTOP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("VCS_DESKTOP_PORT must be between 1 and 65535")
    session = DesktopSession(port=port, api_key=settings.api_key, pid=os.getpid())
    store = DesktopSessionStore(settings.data_dir / "secrets" / "mcp-session.dpapi")
    store.save(session)
    try:
        uvicorn.run(
            create_app(settings=settings), host="127.0.0.1", port=port, workers=1, access_log=False
        )
    finally:
        store.clear_if_current(session)


if __name__ == "__main__":
    main()
