# Windows desktop build

This is a source checkpoint, not a released installer. The desktop shell uses
[Tauri 2 NSIS](https://v2.tauri.app/distribute/windows-installer/) and a
PyInstaller-bundled local FastAPI sidecar. Rust/Cargo, Node.js/npm, Python
3.12, PyInstaller, and backend dependencies are needed on the Windows build
machine, plus MSVC C++ Build Tools/Windows SDK and a redistributable FFmpeg
executable with its license. The end user will not need Python or Rust installed.

Run `scripts/build-desktop.ps1` with its FFmpeg path/license arguments from
PowerShell (see the script's parameters). It builds React, includes
`frontend/dist` and `schema.sql` in the sidecar, places the sidecar under
`frontend/src-tauri/binaries`, then invokes Tauri's NSIS build. The output is
under `frontend/src-tauri/target/release/bundle/nsis`.

At runtime the shell chooses a free loopback port, generates an API session
key, sets AppData as `VCS_DATA_DIR`, starts the sidecar, waits for `/api/health`,
and opens the UI. The API key is injected only into the local HTML response;
script/CSS files and the installer do not contain it. All `/api/*` routes
except the existing health/media exemptions require it.
The web API-key settings prompt is hidden in desktop mode; local authentication
is managed automatically by the shell.

The desktop-only Runpod tab can validate and save the owner's management API
key with Windows DPAPI, compare live GPU rate estimates (48 GB and larger),
and show Pod/volume billing returned by Runpod. The pricing list includes only
NVIDIA GPUs because the runtimes use CUDA. Pair a deployed worker using its
Pod ID and distinct inference token; authentication/protocol are checked
before saving the pairing with DPAPI. Pairing takes effect without restarting.
Download buttons request the pinned catalog and Qwen/Gemma helper snapshots
into the Pod volume; partial snapshots are not reported installed. Deployment
is described in [POD_WORKER.md](POD_WORKER.md).

Manual pairing is the current development flow. The accepted desktop UX now
requires Runpod-key-only setup, persistent-volume selection/creation, verified
model downloads with progress, automatic eligible GPU choice and compute release
after the queued batch. These provisioning/verification adapters are still open;
see [CLOUD_LIFECYCLE.md](CLOUD_LIFECYCLE.md). No installer should present the manual
flow as the completed product.

Speech Direction and script conversion use remote helper adapters. Windows
does not load their GPU libraries. The Dialogue tab enqueues existing durable
speech jobs per assigned voice/line and assembles only completed real clips.
Drafts are saved under AppData `projects/`, because the desktop's changing port
means browser localStorage cannot provide persistence across restarts.

The standalone MCP executable passed real stdio and DPAPI tests. The full
NSIS installer, clean-install validation and real Runpod audio qualification
remain separate release gates; check [HANDOFF.md](HANDOFF.md) for current results.
