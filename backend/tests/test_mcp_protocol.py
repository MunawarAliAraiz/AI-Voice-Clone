"""Optional real stdio protocol check. Install requirements-mcp.txt to run it."""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_stdio_tools_and_authenticated_api_bridge() -> None:
    key = "test-only-mcp-key"
    requests: list[tuple[str, dict]] = []

    class Api(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            assert self.headers["X-API-Key"] == key
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"id":123,"status":"queued","test_only":true}')

        def do_GET(self) -> None:
            assert self.headers["X-API-Key"] == key
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail":"Test studio offline"}')

        def log_message(self, *_args: object) -> None:
            pass

    api = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    thread = threading.Thread(target=api.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["VCS_MCP_API_BASE"] = f"http://127.0.0.1:{api.server_port}"
    env["VCS_MCP_API_KEY"] = key
    try:
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "app.mcp"], env=env
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "studio_health", "list_voices", "list_models", "generate_speech",
                    "get_job", "cancel_job", "list_recent_jobs", "list_history",
                }
                result = await session.call_tool(
                    "generate_speech", {"text": "Hello", "profile_id": 2, "language": "en"}
                )
                assert not result.is_error
                assert requests == [
                    ("/api/generate", {"text": "Hello", "profile_id": 2, "language": "en"})
                ]
                failed = await session.call_tool("studio_health", {})
                assert failed.is_error
                assert "Test studio offline" in str(failed)
    finally:
        api.shutdown()
        api.server_close()
        thread.join(timeout=2)
