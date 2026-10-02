# Desktop updates and local release preparation

## What the app does

The desktop **Updates** control checks the fixed public GitHub release feed.
An unpublished/unreachable feed is reported as unavailable; it does not
confirm that the installed version is current. Download progress is real
transferred bytes. The embedded updater public key validates the installer
before installation, including the signed version. Only a verified installer
is eligible to install.

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
