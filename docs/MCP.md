# Local MCP server

`python -m app.mcp` exposes the running studio to Codex and Claude over MCP
stdio. It uses the [official Python MCP SDK v2](https://github.com/modelcontextprotocol/python-sdk)
and calls the local FastAPI service; it does not hold a second database or GPU
scheduler. Install `backend/requirements-mcp.txt` alongside the backend API
dependencies, then configure an MCP host to launch the absolute Python path
with module `app.mcp` and working directory `backend`.

Set `VCS_MCP_API_BASE` to the local desktop sidecar URL and `VCS_MCP_API_KEY`
to its current session key. The desktop shell chooses a new port and key on
each start. Automatic discovery and a packaged `voice-clone-mcp.exe` are still
required before this is convenient for nondeveloper installations. Do not put
the key into a checked-in MCP config file.

Tools provided: health, list voices, list models, queue TTS generation, get or
cancel a job, list recent jobs, and list completed history. `generate_speech`
returns a queued job ID; clients must poll `get_job`. Results include the API's
signed media URL. Current MCP tools do not provision Runpod resources or
download models.

The stdio process writes protocol messages to stdout only. Errors from the
local API are returned as tool errors without credentials or tracebacks.
