# Windows desktop build

## Windows 0.1.8 MCP installer guard - 2026-10-03

The new NSIS preinstall/postinstall hooks passed a real disposable frozen-MCP
file-lock regression. Both parent/child processes for the exact target were
stopped and replaced, an unrelated same-name copy stayed running, and the new
MCP initialized with ten tools. Persistent unknown locks block before copying;
skipped/mismatched MCP files block installer success. 47 focused mocked tests,
lint, isolated frozen API/MCP and exact embedded UI/manifest checks passed.
The full Windows package compiled with both hooks and the tested MCP SHA256.

Local signed installer: `D:/Projects/AI-Voice-Clone/dist/desktop/AI-Voice-Clone-Studio-0.1.8-setup.exe`;
74,225,580 bytes; SHA256 `4fcfef0182b50d44fe068fdc3835a851902958a4bfe57e20b6983ae010cb5e78`.
Artifact/global-comment signature, signed filename/version and PE/NSIS checks
passed. Exact publication approval is pending; public latest remains 0.1.7.
Evidence and native lock-test receipts are beside the installer.
No complete installed-app update/restart or live GPU success is claimed.

## Windows 0.1.7 verification - 2026-10-03

The premium workspace and original liquid orb passed an isolated browser review
at 375, 768, 1024 and 1440 px. Keyboard section navigation, retained drafts,
actual WebGL states and narrow tooltip/voice-panel layout were checked. The
fixtures were explicitly simulated; no Runpod downloads or jobs were started.

The rebuilt Windows x64 installer contains the exact reviewed frontend assets
and the qualified worker manifest for source `5e2d5c0556622475b8e308c851b40d48fd0613a7`.
Isolated frozen API/MCP checks passed, including setup validation, authenticated
requests, generation admission, update preparation and app-exit acknowledgement.
Artifact/global-comment Ed25519 signatures and signed version/filename passed.
This does not establish Windows Authenticode signing, live GPU generation,
listening acceptance or an installed app update/restart cycle.

Local installer: `D:/Projects/AI-Voice-Clone/dist/desktop/AI-Voice-Clone-Studio-0.1.7-setup.exe`;
65,831,146 bytes; SHA256 `5ca5ed5dad8eeeae1b5e023a6a84dabfa4802a10f2e26f03d85eba53ecb31230`.
Exact publication was approved and completed:
[v0.1.7](https://github.com/MunawarAliAraiz/AI-Voice-Clone/releases/tag/v0.1.7).
Anonymous latest-feed and full-installer downloads matched the prepared bytes
and approved size/SHA256. Public latest is 0.1.7. Evidence, publication receipt
and release notes are beside the local installer. Live GPU and native restart
limitations above remain.

## Earlier 0.1.1 release — 2026-10-02

The installer is available in the shared project `dist/desktop/` and the
[approved public release](https://github.com/MunawarAliAraiz/AI-Voice-Clone/releases/tag/v0.1.1).
Actual isolated installation/startup/auth/SQLite, audio readiness admission,
agent status, ten MCP tools, update preparation/cancel, single-instance and
normal-close listener cleanup passed. It contains the status/alignment fixes,
blocked-generation explanations, Agents screen, Convert changes and updater.

65,740,268 bytes; SHA256
`367fa3d3ffc1dc83ddedced34443cdeae40ff25ad634ff4b54dbd676fab088d5`.
The updater signature and signed version verified; Authenticode is absent.
Actual previous-version update/apply/restart and live Runpod GPU audio/cost
qualification are incomplete. See [Desktop updates](DESKTOP_UPDATES.md).

## Build instructions

A native installer has passed the packaging checks below. It is a **test build**,
not the completed Runpod release: its sidecars must be rebuilt after the automatic
cloud setup implementation is complete. The desktop shell uses
[Tauri 2 NSIS](https://v2.tauri.app/distribute/windows-installer/) and a
PyInstaller-bundled local FastAPI sidecar. Rust/Cargo, Node.js/npm, Python
3.12, PyInstaller, and backend dependencies are needed on the Windows build
machine, plus MSVC C++ Build Tools/Windows SDK. The end user will not need
Python or Rust installed. Audio tools are downloaded directly from the pinned
publisher on first launch and verified before audio operations unlock.

Run `scripts/build-desktop.ps1` with `-UpdaterKeyFile` pointing to the DPAPI
protected signing identity (see the script's parameters). It builds React, includes
`frontend/dist` and `schema.sql` in the sidecar, places the sidecar under
`frontend/src-tauri/binaries`, then invokes Tauri's NSIS build. The output is
under `frontend/src-tauri/target/release/bundle/nsis`.
When `--target x86_64-pc-windows-msvc` is used, the actual directory is
`frontend/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis`.
`-BuildJobs` defaults to two to keep the build machine responsive. Optional
`-TauriCli` accepts a locally cached `@tauri-apps/cli/tauri.js`; the script checks
that it is the pinned CLI version before using it, avoiding npm refresh delays.

At runtime the shell chooses a free loopback port, generates an API session
key, sets AppData as `VCS_DATA_DIR`, starts the sidecar, waits for `/api/health`,
and opens the UI. The API key is injected only into the local HTML response;
script/CSS files and the installer do not contain it. All `/api/*` routes
except the existing health/media exemptions require it.
The web API-key settings prompt is hidden in desktop mode; local authentication
is managed automatically by the shell.

The desktop-only Runpod tab validates and saves the owner's management API
key with Windows DPAPI, compares live GPU rate estimates (48 GB and larger),
and shows Pod/volume billing returned by Runpod. The pricing list includes only
NVIDIA GPUs because the runtimes use CUDA. Internal worker credentials are
managed by the cloud controller. Deployment is described in
[POD_WORKER.md](POD_WORKER.md).

The desktop source now uses a Runpod-key-only setup panel: credit/storage
discovery, reviewed volume quote, pinned checksum verification/download progress,
automatic regional GPU choice and queue-drain release. Legacy pairing routes
remain for development. Paid operations remain locked until a reviewed published
worker release is included. See [CLOUD_LIFECYCLE.md](CLOUD_LIFECYCLE.md) and
[POD_IMAGE_RELEASE.md](POD_IMAGE_RELEASE.md); the source adapters are not yet
qualified by actual provider provisioning/generation.

Speech Direction and script conversion use remote helper adapters. Windows
does not load their GPU libraries. The Dialogue tab enqueues existing durable
speech jobs per assigned voice/line and assembles only completed real clips.
Drafts are saved under AppData `projects/`, because the desktop's changing port
means browser localStorage cannot provide persistence across restarts.

The standalone MCP executable passed real stdio and DPAPI tests. Real Runpod
audio qualification and a clean Windows machine check remain release gates;
check [HANDOFF.md](HANDOFF.md) for current cloud results.

## Final local development installer — 2026-10-01

The latest cloud source/UI and backend fixes were frozen and bundled. A fresh
disposable installation passed native startup, frontend/auth/SQLite, cloud setup
status and generation admission gating, installed MCP, single-instance behavior,
normal close API cleanup and uninstall. Final artifact:

```text
Bytes: 87,190,893
SHA256: 5257E69307627431BA5BBC30AE0849AD09C4315D281CDAF7DB16F76E5C3C48F6
Signature: NotSigned
```

`native-final-receipt.json` beside the build artifact records hashes of the actual
installed shell/API/MCP/FFmpeg executables. A development copy and receipt are at
`D:/Projects/AI-Voice-Clone/dist/desktop/`. This proves local packaging integration;
it does not prove live Runpod generation, model loading performance or billing.
The worker release configuration is absent and paid provisioning stays locked.
FFmpeg notices are included, but complete corresponding source/source-offer
preparation remains required before public redistribution. A separate clean
Windows-machine test and code signing are also not yet completed.

## Earlier native packaging baseline — 2026-10-01

The NSIS build succeeded with cached Rust MSVC, Tauri CLI 2.12.0, two build jobs,
the existing frozen API/MCP sidecars, and verified FFmpeg 9.0.2. Artifact:

```text
frontend/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/AI Voice Clone Studio_0.1.0_x64-setup.exe
Bytes: 87,166,590
SHA256: E8E50980A0F4AFBF8779FFD64AB797882AE169434E2756D5C7BF1467031EBCC5
Signature: NotSigned
```

`native-baseline-receipt.json` beside the installer records hashes for the actual
installed shell, API, MCP and FFmpeg executables and the native smoke result.
The installer was silently installed into a disposable TEMP directory, upgraded
after a shutdown fix, and uninstalled successfully. The installed binary tests
passed on the development Windows machine:

- Native hidden WebView/API startup, static HTML, session authentication and SQLite.
- Installed MCP stdio initialization, eight tools and authenticated live API calls.
- A second app launch exits without replacing the first API session.
- Closing the main window exits the shell and stops the actual API PID/listener.

The initial shutdown test found a onefile API child left behind and a shell close
timeout. The shell now stops the exact owned process tree on `CloseRequested`
before the runtime exits; startup failure uses the same cleanup. This is local
process cleanup, not a substitute for the cloud provider's termination deadline.

Repeat the installed test using the Python packaging environment with MCP SDK v2:

```powershell
python scripts/test-desktop-installed.py `
  --install-dir C:\absolute\disposable-install `
  --data-dir C:\absolute\fresh-test-data `
  --receipt C:\absolute\native-smoke-result.json
```

The data directory must not exist. The helper tests only its own hidden main
window, retains a handle to the verified API executable for cleanup, and prints
no session keys. `VCS_DESKTOP_TEST_HIDE` and the absolute
`VCS_DESKTOP_DATA_DIR` override are test-only controls; normal launches show the
app and use its normal AppData location.

This proof covers packaging and local integration, not actual voice generation,
cloud setup, GPU costs or listening quality. The final source freeze and repeated
native smoke checks are recorded above. The public release still needs the
redistribution source and notices required by the bundled FFmpeg build.
Code signing is not configured.

