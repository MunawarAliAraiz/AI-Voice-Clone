# Desktop updates and local release preparation

## Latest published update - 0.1.8

The MCP file-lock fix is published with exact approval: 74,225,580 bytes,
SHA256 `4fcfef0182b50d44fe068fdc3835a851902958a4bfe57e20b6983ae010cb5e78`.
The signed feed and full public installer download matched the prepared bytes.
Real production-hook file-lock tests and frozen API/MCP checks passed;
a complete installed-app update/restart cycle remains unverified.
See [Desktop build evidence](DESKTOP.md).

## Earlier published update - 0.1.7

The exact 65,831,146-byte installer was approved and published with its signed
feed and notes. SHA256:
`5ca5ed5dad8eeeae1b5e023a6a84dabfa4802a10f2e26f03d85eba53ecb31230`.
Anonymous latest-feed and full-installer verification passed. The fixed feed
offered 0.1.7 at that publication; the signature uses the existing trusted update identity.
See [Desktop build evidence](DESKTOP.md) for scope and untested native/GPU paths.

## What the app does

### MCP file replacement guard (0.1.8)

Codex/Claude can keep the installed MCP executable running after the desktop
API stops. NSIS now runs a standalone guard before copying application files,
including when launched by older updaters. The guard stops only processes with
the exact target executable path, verifies exclusive file readiness and blocks
after bounded retries if the lock cannot be released. Agent hosts and other
MCP binaries are not terminated. A postinstall SHA256 check blocks success and
restart if the required MCP file was skipped or has different bytes.

47 mocked guard tests and real frozen-MCP production-hook tests passed in a
disposable silent NSIS probe. The tests preserved an unrelated same-name MCP
process and local sentinel files, verified reconnecting the new MCP, and proved
failure before extraction for a persistent lock plus postinstall failure for
a skipped file. This is an actual file-lock regression, not a full app install
or previous-version app restart cycle. The user's installer was left for them
to click Retry after its six exact-path MCP processes were stopped.

From 0.1.5, normal window close also asks the authenticated API to stop owned
cloud work before the sidecar exits. Confirmed app-exit pauses resume on reopening;
manual Pause remains paused. This does not override updater admission: applying
an update still requires confirmed idle cloud/queue state. Failure to confirm
cleanup keeps the window open with retry/return actions. This path was compiled
and checked through isolated API tests; an installed-window cycle is unverified.

The desktop **Updates** control checks the fixed public GitHub release feed.
An unpublished/unreachable feed is reported as unavailable; it does not
confirm that the installed version is current. Download progress is real
transferred bytes. The embedded updater public key validates the installer
before installation, including the signed version. Only a verified installer
is eligible to install.

### Download recovery and cancellation (0.1.4)

The update dialog offers **Cancel download** while an installer transfers.
Cancellation aborts the HTTP request, including a stalled response, then
shows the saved byte count. Closing the dialog leaves the download running;
cancel explicitly to pause it. These controls are separate from canceling
Runpod model setup or a generation.

Installer bytes and metadata live under `update-cache` inside the local
desktop data directory. A completed installer survives an app close/reopen
and a failed restart check. Checking the fixed release feed restores the
**Restart and update** action for the matching cached release. Every restored
installer is cryptographically verified again; cache flags alone cannot
grant installation. Installation repeats verification immediately before use.

Partial files resume only when their metadata matches the checked feed's
version, URL and signature, and the server supplies a strong ETag and known
size. The next request uses `Range` and `If-Range`. Appending requires a valid
206 response with the exact offset, remaining length, total size and same
ETag. A changed file, ignored Range, missing entity validator or unsupported
resume response triggers a fresh full download. A fully saved partial file
is checked and promoted locally without transferring it again. Invalid
signatures are rejected and their partial bytes discarded.

The public key is embedded in the app, and both the artifact signature and
the global signature over the trusted comment must pass before the signed
version is read. This reproduces the pinned updater's verification rules;
the update's unsigned feed version cannot substitute a different installer.
Only the configured GitHub repository's HTTPS release asset location is
accepted. No URL, filename, key or signature can be supplied through IPC.
Downloads are bounded to 250 MiB and metadata to 64 KiB.

Older installed versions through 0.1.3 kept installers in process memory.
Bytes already lost by closing those versions cannot be recovered by this
change. Network access to check the release feed is still required to
restore an update's install action after reopening the app.

Focused Rust tests use an independent test signing key and real loopback
HTTP responses to check stalled-response cancellation, byte-range resume,
changed ETags, ignored ranges, completed-cache reuse after reconstructing
state, complete-partial recovery, and rejection of modified bytes/version/
global comment. These tests do not launch the user's installed app or prove
a live previous-version installer/restart cycle.

Verification for this change: 10 native tests passed, the frontend production
build passed, and the updater SSR checks passed with native calls prohibited.
Run the native checks with `cargo test --locked --offline --jobs 2 --target
x86_64-pc-windows-msvc --bin voice-clone-desktop updates::cache::tests --
--nocapture` and the view checks with `node
frontend/scripts/test-desktop-updates-view.mjs`.

Installing quiesces new API mutations, checks that local jobs and cloud
compute are idle, stops the owned API process tree, starts the per-user NSIS
installer and restarts the app. Failed preparation/installer launch restores
normal app admission. Keep the private signing identity protected; it is
never bundled or passed to agent configuration.

Updater signatures are separate from Windows Authenticode/code signing.
A successful updater signature check does not establish an Authenticode
certificate or SmartScreen reputation.

## Prepare a release locally

1. Bump the SemVer in `frontend/src-tauri/tauri.conf.json` and build with the
   pinned Tauri CLI. The build includes a cryptographically signed version.
   Use the existing DPAPI-protected signing identity; do not regenerate the
   trusted public key for a routine update.
2. Run the disposable installed smoke test, including normal shutdown,
   authenticated API, local audio setup, MCP and update failure recovery.
   Record the exact installer SHA256 and byte size in its receipt.
3. Use a dedicated output directory for this release and the packaging Python:

   ```powershell
   python scripts/prepare-desktop-release.py --installer "path/AI Voice Clone Studio_0.1.1_x64-setup.exe" --signature "path/AI Voice Clone Studio_0.1.1_x64-setup.exe.sig" --receipt "path/native-receipt.json" --output-dir "dist/desktop-release/0.1.1"
   ```

   Optional: `--notes-file path/release-notes.md`. A build receipt containing
   only artifact hashes also requires the independently checked
   `--expected-size BYTES`. The tool reads version and public key from the
   Tauri config; it accepts no private key or arbitrary repository URL.

The preparation script checks the installer/receipt names, Windows x64
target, exact size and SHA256, Windows PE/NSIS launcher, signed filename and
signed SemVer. NSIS's launcher can be x86 while its application payload is
x64; the tool accepts that normal packaging structure.

It cryptographically verifies both Minisign signatures using the embedded
Ed25519 public key, including the Blake2b prehash where applicable and the
trusted comment's global signature. The packaging Python already provides
`cryptography`; this is a release-tool requirement, not an app runtime
dependency. If the verifier is absent, preparation fails and produces no
new feed. It never falls back to a structural-only signature check.

Outputs:

- `latest.json`: fixed HTTPS asset URL in
  `MunawarAliAraiz/AI-Voice-Clone`, tag `vVERSION`, with
  `windows-x86_64` and `windows-x86_64-nsis` aliases.
- `release-evidence.json`: actual hash, bytes, expected published asset
  name, verified signature/version flags and `published: false`.

The tool does not copy, upload or publish an installer. For a reviewed release,
the exact verified installer must be uploaded under the evidence's
`asset_name` (for example `AI-Voice-Clone-Studio_0.1.1_x64-setup.exe`);
renaming the asset does not change its bytes or signature. The original
signed filename remains bound in the trusted comment.

## Publication gate and qualification

Public source/workflow permission does not automatically authorize uploading
a release binary. Obtain explicit authorization for the reviewed installer,
feed and release notes before publishing. Keep the receipt and version bound
to that exact installer hash. After authorized publication:

1. Verify the public asset hash/size, feed and anonymous download access.
2. Test a real previous-version installation updating to this release.
3. Confirm signature rejection on tampered bytes/version and that failed
   checks/downloads leave the existing app usable.
4. Record the receipt and observed result in the handoff. Mock signature
   fixtures and local feed preparation do not prove an installed update.

Primary references:

- [Tauri updater](https://v2.tauri.app/plugin/updater/): updater artifacts,
  mandatory signatures and static feed fields.
- [Tauri CLI 2.12 release](https://v2.tauri.app/release/tauri-cli/v2.12.0/):
  signed version in the trusted comment and `--app-version` for manual signing.
- The pinned `minisign-verify 0.2.5` and `tauri-plugin-updater 2.13.1`
  source establishes the binary signature format, global comment validation
  and version comparison used by this app. Tests include an independent
  upstream Minisign reference vector plus valid/tampered artifact cases.
