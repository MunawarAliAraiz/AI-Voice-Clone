# Windows desktop build

This is a source checkpoint, not a released installer. The desktop shell uses
[Tauri 2 NSIS](https://v2.tauri.app/distribute/windows-installer/) and a
PyInstaller-bundled local FastAPI sidecar. Rust/Cargo, Node.js/npm, Python
3.12, PyInstaller, and backend dependencies are needed on the Windows build
machine. The end user will not need Python or Rust installed.

Run `scripts/build-desktop.ps1` from PowerShell. It builds React, includes
`frontend/dist` and `schema.sql` in the sidecar, places the sidecar under
`frontend/src-tauri/binaries`, then invokes Tauri's NSIS build. The output is
under `frontend/src-tauri/target/release/bundle/nsis`.

At runtime the shell chooses a free loopback port, generates an API session
key, sets AppData as `VCS_DATA_DIR`, starts the sidecar, waits for `/api/health`,
and opens the UI. The API key is injected only into the local HTML response;
script/CSS files and the installer do not contain it. All `/api/*` routes
except the existing health/media exemptions require it.

The desktop-only Runpod tab can validate and save the owner's management API
key with Windows DPAPI, compare live GPU rate estimates (48 GB and larger),
and show Pod/volume billing returned by Runpod. These are read-only account
operations. Remote TTS requires `VCS_REMOTE_WORKER_URL` and
`VCS_REMOTE_WORKER_TOKEN` at sidecar startup today; connecting a Runpod account
in the tab does not configure them yet. The Pod model-install API downloads
catalog-pinned revisions into `HF_HOME/hub` when deployed with
`huggingface_hub`; no user-facing install trigger is wired yet.

Build and clean-install validation remain open because this workstation does
not currently have Rust/Cargo or PyInstaller. Do not distribute the output
until that validation and real audio generation pass.
