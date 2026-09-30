# Local MCP server

`python -m app.mcp` exposes the running studio to Codex and Claude over MCP
stdio. It uses the [official Python MCP SDK v2](https://github.com/modelcontextprotocol/python-sdk)
and calls the local FastAPI service; it does not hold a second database or GPU
scheduler. Install `backend/requirements-mcp.txt` alongside the backend API
dependencies, then configure an MCP host to launch the absolute Python path
with module `app.mcp` and working directory `backend`.

The desktop saves its port and session key in a Windows DPAPI-encrypted
descriptor at `%APPDATA%/studio.voiceclone.desktop/secrets/mcp-session.dpapi`.
MCP reads it on every request, so it follows desktop restarts without copying
keys into host configuration. Open the desktop studio before invoking tools.
For development only, set both `VCS_MCP_API_BASE` and `VCS_MCP_API_KEY`; the base
must be an HTTP loopback address with an explicit port. `VCS_MCP_SESSION_FILE`
can select a development descriptor. Remote hosts, embedded credentials and
redirects are refused.

The Windows build bundles `voice-clone-mcp.exe` beside the desktop/API
executables. Configure your MCP host to launch its absolute installed path,
with no arguments or environment secrets. A standalone executable was built
and tested using MCP SDK 2.2.0/PyInstaller 6.19.0. Real stdio tests covered
initialization, all eight tools, authenticated loopback requests, DPAPI
discovery, session replacement and missing-app errors. The test generation
endpoint was an HTTP fixture, not GPU inference.

Tools provided: health, list voices, list models, queue TTS generation, get or
cancel a job, list recent jobs, and list completed history. `generate_speech`
returns a queued job ID; clients must poll `get_job`. Results include the API's
signed media URL. Current MCP tools do not provision Runpod resources or
download models.

The stdio process writes protocol messages to stdout only. Errors from the
local API are returned as tool errors without credentials or tracebacks.
