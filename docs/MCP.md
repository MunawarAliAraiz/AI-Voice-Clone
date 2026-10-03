# Local MCP server

## App updates and agent connections

Normal desktop restart keeps MCP clients alive and reloads the new encrypted
API session. Installing an update replaces the MCP executable, so from 0.1.8
the installer first stops connections using that exact installed file. Other
agent tools and the Codex/Claude app stay running. If your agent shows this
server as disconnected afterward, reconnect Voice Clone Studio in the agent.
The installer blocks with Retry/Cancel if it cannot unlock the file.

## Connect from the desktop app

Open **Agents**, select Codex, Claude Desktop or Claude Code, then click
**Connect**. Discovery checks known configuration locations and CLI availability;
it does not claim a client is running. The installed MCP executable must exist.
Preview builds cannot configure clients.

The app writes only the named `voice_clone_studio` server entry, preserving
other settings and servers. An existing different entry requires the explicit
replacement checkbox. Valid configuration is backed up beside its original
file before atomic replacement; concurrent changes are checked first. Backups
can contain that client's existing credentials, so keep them private locally.
Malformed or redirected configuration is refused. No API keys are added.

Restart the selected client and ask it to call `studio_health`. **Configured**
means the setting was saved, not that a live handshake succeeded. The Agents
screen shows the last actual MCP tool call across clients; it cannot attribute
that shared activity to one client.

Verified host configuration references:

- [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli):
  `~/.codex/config.toml`, `mcp_servers.voice_clone_studio`, absolute stdio
  command with no secret environment. A custom
  [CODEX_HOME](https://learn.chatgpt.com/docs/config-file/environment-variables)
  is honored when inherited by the app.
- [Claude Desktop official MCP SDK guidance](https://py.sdk.modelcontextprotocol.io/get-started/real-host/):
  `%APPDATA%/Claude/claude_desktop_config.json`, top-level `mcpServers`.
- [Claude Code user scope](https://code.claude.com/docs/en/mcp):
  `~/.claude.json`, top-level `mcpServers`, `type: stdio`.

## Agent generation workflow

The server initialization instructions ask the agent to obtain a script,
declared language, chosen reference voice and delivery before queueing.
For text generation, the enrolled reference profile shapes new generated
speech; source-recording conversion has separate source and target inputs.
Agents must list voices and use real IDs, then preview supported delivery
controls before promising them.

`generate_speech` accepts speed and the existing Speech Direction plan
(segment index, emotion, intensity, energy, rate and pauses). The normal API
validates the exact script and supported model capabilities; this tool cannot
rewrite segment text. Generation remains subject to the desktop model
readiness gate and approved compute limits.

`preview_speech_direction` is a preview, not a generation. `wait_for_job`
waits at most 25 seconds, bounds stalled HTTP calls, and returns the actual
job state. Timeout does not cancel or duplicate a job; use `get_job` later.
Failed and cancelled results remain distinct from successful audio.

Desktop job polling also continues while idle, allowing externally queued
MCP jobs to appear in the studio within a few seconds. New jobs and
settlements trigger the existing in-app toast path; completed output remains
in Recent. Native notification support is handled by the desktop shell.

Isolated profile tests cover preserved TOML comments, other JSON settings and
servers, private metadata, explicit conflict replacement, backups, failed
atomic replacement, missing executable and authenticated desktop routes.
Actual user configurations are not modified during qualification.

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
initialization, the original eight tools, authenticated loopback requests, DPAPI
discovery, session replacement and missing-app errors. The test generation
endpoint was an HTTP fixture, not GPU inference.

Ten tools are now provided: health, list voices, list models, queue TTS
generation, preview Speech Direction, bounded wait, get or cancel a job,
list recent jobs and list completed history. `generate_speech` returns a
queued job ID. Results include the API's signed media URL. There are no
storage-purchase or model-download MCP tools; normal generation can start
managed compute under the user's approved app limits.

The stdio process writes protocol messages to stdout only. Errors from the
local API are returned as tool errors without credentials or tracebacks.
