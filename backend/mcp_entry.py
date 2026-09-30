"""Standalone stdio MCP entry point for the Windows installer.

Keep console mode enabled in PyInstaller: MCP hosts communicate over stdin and
stdout. Application diagnostics belong on stderr, never on protocol stdout.
"""

from app.mcp.__main__ import main

if __name__ == "__main__":
    main()
