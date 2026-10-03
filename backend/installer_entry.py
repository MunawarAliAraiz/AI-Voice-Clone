"""Standalone stdlib-only installer guard; no API, MCP or user profile imports."""

from app.install_guard import main

if __name__ == "__main__":
    raise SystemExit(main())
