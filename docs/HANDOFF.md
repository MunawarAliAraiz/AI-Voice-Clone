# Handoff — current state

## Premium workspace and generation error investigation - 2026-10-03

The requested premium UI is implemented: neutral ink surfaces, a script-first
editor, supporting voice tools, full-width Runpod wizard, compact navigation,
accessible contrast and an original small liquid orb. The orb reflects actual
submission/job/cloud state, including unconfirmed status and missing output;
it never substitutes for measured model progress. Browser rendering and draft
retention passed in an isolated preview. Narrow width/tooltip overflow found
there was fixed. See [Design](UI_DESIGN.md) and [Orb](ORB.md) for evidence.

Frontend production build and the existing setup/gate/update checks passed;
15 new status checks and orb lifecycle checks passed. This redesign has now
been packaged and verified in Windows 0.1.7. Exact publication was approved and
completed; 0.1.7 is the latest public desktop release.

User then supplied a screenshot of VoxCPM2 HTTP500 and unavailable Pod script
conversion. Read-only profile inspection confirmed failures; the old GPU Pod
was already removed, so its stacktrace is unavailable. Backend correction of
an evidenced VoxCPM keyword/path defect, safe worker error propagation and a
separate-volume cache path defect passed 79 focused backend and 42 Pod tests;
touched backend/startup files passed lint. See the two investigation docs.
The old constructor fallback could succeed and the installer writes pinned
refs/main, so neither establishes the observed failure's cause. No real
successful generation is claimed. Worker-only corrections/tests were published
as `5e2d5c0556622475b8e308c851b40d48fd0613a7`; both CI images built successfully.
Anonymous registry qualification passed for their immutable digests, which are
now recorded in `backend/app/runpod/release.json`. This verifies image identity
and build evidence, not live CUDA generation.
No desktop UI or user data was pushed. Runtime/package version is now 0.1.7;
the new installer is built. Exact archived UI assets and worker manifest,
isolated frozen API/MCP, session authentication, setup/update/exit gates and
cryptographic artifact/global-comment signatures passed. Native installed UI,
update/restart, paid GPU generation and listening remain untested.

Installer: `D:/Projects/AI-Voice-Clone/dist/desktop/AI-Voice-Clone-Studio-0.1.7-setup.exe`.
Size: 65,831,146 bytes. SHA256:
`5ca5ed5dad8eeeae1b5e023a6a84dabfa4802a10f2e26f03d85eba53ecb31230`.
Feed, build/signature evidence, frozen sidecar/embedded asset receipts and notes
are alongside it. The approved
[v0.1.7 release](https://github.com/MunawarAliAraiz/AI-Voice-Clone/releases/tag/v0.1.7)
is published. Anonymous latest-feed and full-installer downloads matched the
prepared bytes and approved size/SHA256. Receipt:
`D:/Projects/AI-Voice-Clone/dist/desktop/publication-0.1.7-receipt.json`.
Next: the user updates and retries a short generation/conversion. If either
fails, use the new safe failure category; the original removed Pod's traceback
cannot be recovered. Do not claim live GPU success from these package checks.
Never push the whole app
branch or operate the user's installed app/profile/cloud.

## Automatic model-download request fix - 2026-10-03

The user reported "The request body failed validation" during model setup in
0.1.5. A read-only live snapshot confirmed 0.1.5, selected storage, cancelled
setup, automatic intent unset and no compute or cleanup pending. The frontend
cloudAutoSetup sent JSON text without a JSON Content-Type. Browser fetch defaults
that body to text/plain, which FastAPI rejects before the controller executes.

Fixed the client header. Regression checks execute the real frontend API client
and enable/pause/resume requests; actual FastAPI route tests reproduce the exact
422 for text/plain and accept application/json without any paid controller call.
7 focused backend tests, request/wizard/gate/header/updater frontend checks and
the frontend build passed. Other Runpod JSON methods were audited and already
have the header. No worker image change is required. Source checkpoint: `f33d30a`.
Windows 0.1.6 build/signature, isolated frozen API/MCP, and extraction of the
packaged frontend passed. The packaged client contains the corrected JSON header;
the frozen API reproduces text-body rejection and admits the corrected JSON
request to its normal readiness gate. No paid setup was started.

Installer: `D:/Projects/AI-Voice-Clone/dist/desktop/AI-Voice-Clone-Studio-0.1.6-setup.exe`,
65,823,197 bytes; SHA256
`e186d41d38391ddff58810c5e5cdb93bb31cdc5a30fc18b6006755ebe62ad805`.
Ed25519 artifact/global-comment, signed version/filename and PE/NSIS checks
passed. Exact installer/feed/notes publication was approved and completed:
[v0.1.6](https://github.com/MunawarAliAraiz/AI-Voice-Clone/releases/tag/v0.1.6).
The anonymous latest feed and full installer match the prepared bytes and
approved size/SHA256. Public latest is 0.1.6; receipt:
`D:/Projects/AI-Voice-Clone/dist/desktop/publication-0.1.6-receipt.json`. Evidence is alongside
the installer. Paid downloads/generation and native update cycle remain untested.
Next: implement the user's new colourful liquid orb and premium UI request.
Do not mutate the live profile or start paid downloads for these checks.

## Guided setup revision - installer verified, 2026-10-03

The user confirmed the choices in [GUIDED_SETUP.md](GUIDED_SETUP.md). Work is
split across storage/capacity backend, graceful native app exit, and frontend
wizard. No paid cloud operation or user-app manipulation is authorized by these
implementation tests. The running 0.1.4 was checked read-only: storage exists,
setup phase cancelled, auto_setup_enabled null, no compute or cleanup pending.
That legacy unset state explains the missing normal Resume action.

Implemented: Account/Storage/Models/Ready pages, Back/Next, real Advanced volume
selection and creation, 60 GB minimum (pinned files plus 10 GB), measured
per-model progress, local audio-tools readiness and graceful exit/reopening.
The worker scans actual free space and verified/partial files before transfer.
A legacy purchase migration fix prevents an old 200 GB volume being adopted
when creating a new 60 GB volume. Pending purchase attempts remain fenced.

142 integrated backend tests passed; two optional worker HTTP tests skipped.
Frontend build, wizard SSR, 32 generation-gate and 15 header checks passed;
native compilation and all 11 updater/close-acknowledgement tests passed. Source
checkpoint: `9259402`. These are isolated
implementation checks, not paid downloads or installed-window tests.

Scoped worker source was published at d066405aee2e9603b519a9aa8b3ecb8c05b54ff9.
Hosted run 37050531973 succeeded for both images. Both immutable artifacts passed
anonymous registry/source/lock/platform checks and are embedded in release.json.
Windows 0.1.5 build and isolated frozen API/MCP checks passed (10 MCP tools,
auth, 60 GB capacity contract, readiness, updater admission and graceful-exit
acknowledgement/fence/return). Ed25519 artifact/global-comment signatures,
signed version/filename and PE/NSIS checks passed. No Authenticode certificate.
Installer: `D:/Projects/AI-Voice-Clone/dist/desktop/AI-Voice-Clone-Studio-0.1.5-setup.exe`,
65,826,185 bytes; SHA256
`4f6ed9466aa184e45ab0e3903c2e38d298fc342b552a053797075fad12f5d6a6`.
Build/sidecar/release evidence, feed and notes are alongside it. The frozen API's
embedded worker manifest was extracted and matched source byte-for-byte (SHA256
`f14cc8bebf134bb675196072a6a1b93365627abe91e27c5701837c7bbd4a43d4`).
Exact 0.1.5 installer/feed/notes publication was approved and completed:
[v0.1.5](https://github.com/MunawarAliAraiz/AI-Voice-Clone/releases/tag/v0.1.5).
Anonymous latest-feed bytes matched the verified feed; the full public installer
matched the approved size/SHA256. Publication receipt:
`D:/Projects/AI-Voice-Clone/dist/desktop/publication-0.1.5-receipt.json`.
Qualification checkpoint: `2984f1c`. The public latest feed is now 0.1.5.
Paid model download/GPU generation, listening and installed close/reopen/update
remain untested. Next: the user applies 0.1.5; live qualification must use an
approved budget and preserve ambiguous-start/cleanup fences. Broader app-source
publication is unapproved. Do not operate
the user's app or run paid cloud work as part of these packaging checks.

## Cancellation, update recovery and automatic required models — 2026-10-02

The user requested cloud-session termination before applying 0.1.3. The active
API was still 0.1.1 in the normal AppData profile. Its release guard confirmed
no active local work; two successful REST and GraphQL account listings found
no matching Pod for the unconfirmed start. The exact stale compute record was
backed up and cleared under explicit user authorization. Update preparation
then passed and its temporary admission fence was cancelled. Storage, models,
credentials and the native window were preserved. After the user's own 0.1.3
update, a second failed CPU start was similarly reconciled and cleared; restart
admission passed again. The latest backup is
`%APPDATA%/studio.voiceclone.desktop/cloud-state.before-cancel-20261002-voice-clone-install-6fae037b4452a14d.json`.
Neither attempt had a confirmed Pod ID or a matching machine in either list.
No provider DELETE or paid deployment was performed by the agent.

The CPU setup request used the GPU deployment mutation and discarded the
selected CPU configuration. Official SDK evidence and guaranteed-invalid,
non-executing schema probes confirmed the dedicated `deployCpuPod` route,
case-sensitive `deployCpuPodInput!`, and support for the worker image, volume
and atomic termination deadline. See [CPU investigation](CPU_SETUP_INVESTIGATION.md).
Read-only stock showed `cpu3c-2-4` in US-NE-1 at $0.06/hour. Current source uses
the matching CPU instance and limits the installer deadline to two hours as
well as its $1 budget. This request correction is not live worker qualification.

Implemented in 0.1.4: explicit model-setup cancel, durable automatic
setup intent and bounded availability-only retries; no repeated paid/ambiguous
attempt without explicit resume. Required model preparation starts automatically
when selected storage is connected; valid stored files are reused and partial
files resume where supported. Cancel disables automatic setup. CPU installer
stock is independent of generation GPU stock. Setup steps now use a responsive
grid, and the normal manual retry button is removed. Failures requiring action
and cancellation still have an explicit resume path.

Updater source now keeps partial and verified installers on disk, aborts
cancelled network requests, validates Range/strong ETag before appending, and
rechecks artifact/global-comment/version signatures before installing. Completed
cache recovery and cancellation/transport/cryptographic tamper tests passed
(10 Rust tests); frontend/updater SSR checks passed. Older memory-only downloads
cannot be recovered retroactively. 102 backend lifecycle/update/transfer tests
passed with two optional worker HTTP tests skipped, and focused Ruff passed.
Update admission includes active auto/cancel tasks; the error links to Runpod.
Frontend model/gate/updater state checks and the 0.1.4 frontend build passed.
Required model IDs are exposed so queued model names appear before transfer.
Source implementation checkpoint: `66a7bb2`; the Tauri configuration version
is included in the subsequent qualification checkpoint. Signed Windows 0.1.4
packaging passed, along with isolated frozen API/MCP checks (ten MCP tools,
cancel/auto routes, auth, readiness gates and update admission). The embedded
worker manifest matched source SHA256
`bfb2d40350387618b3b09d2275997e683a5d3342e40b4bf13387f2b842ac90c9`.
Installer: `D:/Projects/AI-Voice-Clone/dist/desktop/AI-Voice-Clone-Studio-0.1.4-setup.exe`,
65,802,251 bytes; SHA256
`540862ee538516bac3c7404bca715442e8fbc773497e627fb8f6b80d6ec2097c`.
Ed25519 artifact/global-comment signatures, signed version/filename and PE/NSIS
structure passed. No Authenticode certificate. Exact installer/feed/notes
publication was approved by the user and completed:
[v0.1.4](https://github.com/MunawarAliAraiz/AI-Voice-Clone/releases/tag/v0.1.4).
Anonymous latest-feed bytes matched the prepared feed, and the complete public
installer matched the approved size/SHA256. Receipt:
`D:/Projects/AI-Voice-Clone/dist/desktop/publication-0.1.4-receipt.json`.
Build, sidecar,
release evidence and notes are beside the installer. No native 0.1.4 install,
real update/restart, paid CPU deployment, cloud model download, GPU inference
or listening qualification was performed. The user operates their own app.

Next: after the user applies 0.1.4, verify the CPU
request starts an owned worker, model progress/valid-file reuse, cleanup and
actual generation. Preserve the ambiguous-start and cost guards; never infer
provider deadline enforcement from schema acceptance alone.

## Simple setup and truthful progress — 2026-10-02

The user's screenshots showed an indeterminate progress strip continuing after
a GraphQL setup failure, with an unconfirmed installer creation displayed like
an active rental. A read-only account check found no Pod matching that pending
name. Creation was never confirmed (`pod_id` is null); absence alone does not
clear an ambiguous deployment fence. No existing cloud resource/profile was
changed during investigation. The underlying resolver failure is not diagnosed;
the older generic message incorrectly blamed API-key permissions universally.

0.1.3 source now shows three simple steps: model storage, model setup, voice
generation. It chooses compatible storage/region defaults and the cheapest
eligible available GPU; purchases and generation spending approval remain
explicit. Custom limits, region controls, file details and mode comparisons
live in closed native Advanced settings disclosures. The Voice Studio model
picker is collapsed too, with existing recommended language defaults and
model/license warnings retained. Urdu Arabic cannot safely use the existing
Auto route because it has no verified permissive candidate; routing was not
broadened or silently switched to an experimental/noncommercial model.

Setup exposes durable phases/errors, running state, cleanup pending and actual
creation confirmation. No animated unknown progress remains. Download rows use
friendly model names and actual reported byte percentages. A failed or restarted
attempt shows a terminal explanation and next action. Requested rate/deadline
are labeled estimates for an unconfirmed start. Confirmed rentals retain their
ID during failed termination. Verified files cannot unlock generation until
installer cleanup is confirmed. A definitive GraphQL parse/schema rejection
clears its pre-execution creation record; unknown/transport/resolver errors keep
the reconciliation fence. Provider messages/credentials are not echoed.

40 focused cloud-controller checks and Ruff passed; frontend 0.1.3 build,
27 generation-gate checks, 15 header checks and focused Runpod SSR checks passed.
The rebuilt frozen API/MCP also passed isolated startup/auth/frontend, fresh
idle setup state, worker manifest availability, generation admission, update
preparation/cancel and ten-tool MCP checks; only the test PID tree was stopped.
Receipt: `D:/Projects/AI-Voice-Clone/build/sidecars-013-receipt.json`.
Native packaging initially failed because cached Cargo source files were
missing. Existing crate archives were checked against the committed lockfile
SHA256 before restoring missing files (540 verified archives, 26,037 files).
The resumed native build passed with the rebuilt/tested sidecars and existing
protected updater signing identity. The exact frozen worker manifest was
extracted and matched source SHA256
`bfb2d40350387618b3b09d2275997e683a5d3342e40b4bf13387f2b842ac90c9`.

The finished local installer is
`D:/Projects/AI-Voice-Clone/dist/desktop/AI-Voice-Clone-Studio-0.1.3-setup.exe`,
65,756,839 bytes; SHA256
`545619e15195cc07d6eb3f88295d19ea41c8664792cd98d9381c907dc88e5da8`.
Windows PE/NSIS structure, actual Ed25519 signature and signed version/filename
passed verification. The local feed, release notes, build/sidecar receipts and
release evidence are beside the installer. No native 0.1.3 install or actual
update/restart was performed. The user approved publication of the exact
0.1.3 installer/feed/notes with the hash above. It is published at
https://github.com/MunawarAliAraiz/AI-Voice-Clone/releases/tag/v0.1.3.
Anonymous latest-feed and complete installer downloads passed and match the
approved bytes; receipt:
`D:/Projects/AI-Voice-Clone/dist/desktop/publication-0.1.3-receipt.json`.
The public latest feed is now 0.1.3. Let the user apply Updates when Runpod is
idle; the updater will refuse restart while the ambiguous pending compute
record still exists. The original deployment failure remains undiagnosed.
Do not push broad application source or operate the user's desktop window.
The user continues operating the computer: no native windows are launched,
closed or updated by the agent. Paid provisioning/generation remains untested.

## Desktop 0.1.2 built and verified — 2026-10-02

The signed Windows x64 NSIS installer is ready locally:
`D:/Projects/AI-Voice-Clone/dist/desktop/AI-Voice-Clone-Studio-0.1.2-setup.exe`.
65,745,532 bytes; SHA256
`f553c47a5684e766f6a1230d607b5dfcaab6765a520ee193d03241ba8f4928b4`.
The actual Ed25519 updater signature, signed version/filename and PE/NSIS
structure passed verification. Windows Authenticode is absent.

Both frozen sidecars passed isolated tests: API startup/version, auth/static
frontend, embedded worker availability, honest disconnected state, generation
admission, update preparation/idempotency/fence/cancel and real MCP handshake
with ten tools. Only the created test process tree was stopped; listener cleanup
passed. The exact qualified worker manifest bytes were extracted from the
frozen API and matched source SHA256
`bfb2d40350387618b3b09d2275997e683a5d3342e40b4bf13387f2b842ac90c9`.
Frontend/CSS checks and native release build passed. 24 release qualification
tests and focused Ruff passed. Receipts are beside the installer.

The user's active app/profile was not touched. Native 0.1.2 install and real
update/restart were not run, because the user is operating their computer.
Actual Runpod provisioning, model downloads, GPU audio/cost/shutdown tests
remain pending. The qualified manifest and account-card width fix are included
in this installer. Source is local; broad application-source permission remains
unanswered. The user separately approved exact 0.1.2 binary/feed publication
with the hash above. It is published at
https://github.com/MunawarAliAraiz/AI-Voice-Clone/releases/tag/v0.1.2.
Anonymous latest-feed and complete installer downloads passed; public bytes
match the exact approved SHA256/size. Receipt:
`D:/Projects/AI-Voice-Clone/dist/desktop/publication-0.1.2-receipt.json`.
Do not push additional application source. Let the user apply Updates and
complete setup; preserve their active TEMP profile described below. The
old 0.1.1 app was not closed, restarted or operated during this work.

## Qualified worker images — 2026-10-02

Both jobs in hosted run 36964451430 completed successfully for worker-only
source `e27c1ca87e2d56608c74f50b60bc394b0ddf4ade`. Matching CI artifacts and
anonymous GHCR manifests/configs passed source, lock/base, platform and content
digest verification. `backend/app/runpod/release.json` now contains:

- Installer: `ghcr.io/munawaraliaraiz/ai-voice-clone-installer@sha256:733bbce5f5e6a6954c63229559fecfe05a8fe802cf58c4d82be963d4ac64f62f`;
  280,301,535 compressed layer bytes; authenticated CPU service smoke passed.
- GPU: `ghcr.io/munawaraliaraiz/ai-voice-clone-gpu@sha256:2dd8dcf6cb3ed8df6b2dffb55c3170123e663cedd0d5255b81ac0fbc1a38de52`;
  7,736,397,742 compressed layer bytes; runtime imports passed during build.

Registry qualification did not download entire image layers. CUDA, actual
Runpod model downloads/generation, listening quality, billing and termination
remain untested. Package 0.1.2 with this manifest and the full-width card fix.
The user's running 0.1.1 still lacks the manifest. Leave their app, computer
controls and active TEMP profile untouched. Older checkpoints below describe
the progression to this result and may contain superseded blockers.

## Worker-only rebuild and screenshot correction — 2026-10-02

The user is using the computer themselves. Do not use Computer Use or replace,
restart or close their active app. Their screenshot confirmed the account card
was still narrower than the other Runpod cards. Its 640 px maximum width is
now removed in source so it spans the same grid column; frontend build passes.
The missing-worker explanation now names the app-version limitation and links
to Updates. These fixes await the next installer and are not in published 0.1.1.

The broad application-source push remains unapproved, but a safer, approved
worker-only publication succeeded: exact two-file Dockerfile/lock fix on public
base `aba0594`, commit `41c22a8378307abf6b4525dfe18cae800db089c5`, pushed to
`fork:codex/desktop-runpod-mcp`. This excludes desktop/agents/updater source.
An isolated checkout is at `D:/Projects/AI-Voice-Clone/build/worker-release-publish`.
Worker translation implementation is also being scoped for worker-only
publication. Hosted image builds and anonymous image access are being checked.

The replacement worker-only commit is now
`e27c1ca87e2d56608c74f50b60bc394b0ddf4ade`: two Pod dependency files,
five worker translation files and one worker-only test. No desktop/API/agent
publication scope was added. Corrected hosted build:
https://github.com/MunawarAliAraiz/AI-Voice-Clone/actions/runs/36964451430.
Both jobs started; the redundant earlier 41c build was canceled. Existing CPU
image `sha256:6d2f1aadf5159be2f6fa6561a2b0fc1b100a5ab46f250646bbe0465422487cf2`
passed authenticated service checks and anonymous OCI manifest access;
280,300,375 compressed layer bytes. The replacement source must qualify both
images before writing the release manifest.

The user's current desktop process is the TEMP test installation, PID 700.
Preserve its app/profile: the user has connected Runpod in it. Do not uninstall
or remove `vcs-desktop-011-7dcb27c8597a4379bfb5ead2fd4e142c` as test cleanup.
Any later profile migration to the standard installed location must preserve
their actual saved data and should be coordinated with their manual update.

Prepare 0.1.2 with the full-width fix and qualified worker manifest together.
Do not publish another app claiming Pod setup is usable without successful
image build/digest/public-pull evidence. Real storage/model download/GPU output
and billing/shutdown qualification remain incomplete. The screenshot's $9.30
credit exceeds its displayed $1.47 setup reserve; missing worker release was
the immediate blocker, rather than that screenshot's account funds.

## Installed 0.1.1 and approved publication — 2026-10-02

0.1.1 was built, installed into a disposable TEMP directory and tested with
a separate profile. Native startup, frontend/session auth/SQLite, audio-tool
admission, agent status, ten real MCP stdio tools, idempotent update preparation,
mutation blocking/cancellation, second-instance behavior and normal close
API/listener cleanup passed. 123 integrated backend checks, 29 release verifier
checks, frontend production build and the native release build passed.

Installer: `D:/Projects/AI-Voice-Clone/dist/desktop/AI-Voice-Clone-Studio-0.1.1-setup.exe`.
65,740,268 bytes; SHA256
`367fa3d3ffc1dc83ddedced34443cdeae40ff25ad634ff4b54dbd676fab088d5`.
The actual installer Ed25519 updater signature and signed version/filename
verified. Windows Authenticode is absent. The user explicitly approved this
exact installer and update feed; they were published at
https://github.com/MunawarAliAraiz/AI-Voice-Clone/releases/tag/v0.1.1.
Local signature/feed/native receipts are beside the installer.
Anonymous public feed and installer downloads also passed, with exact byte
size/SHA256 verified in `publication-0.1.1-receipt.json`.

Visible native checks confirmed "Runpod not connected" with no key, aligned
Runpod form controls, gray generation with a visible reason/setup link, and
working native updater IPC with an honest unpublished-feed error. The user
pressed Escape to stop Computer Use; no further UI automation is permitted
in that turn. A local prerelease fixture build was started for a real update
test; that test remains incomplete. Do not claim actual restart/apply proof.

Local source `8ac243d0588211b25268d3ffec12e1c03623f2bd` contains integration.
Automatic approval review rejected pushing the entire branch because earlier
public permission covered worker source/workflow, while the new payload also
contains desktop UI/updater/agent source. Application-source publication
approval is pending; this was not bypassed. Binary/feed publication was
separately approved and succeeded. Worker rebuild and live GPU qualification
remain open. The normal user installation/profile has not been replaced by
the isolated test. The visual test uses a disposable profile.

## Desktop integration checkpoint — 2026-10-02

The user explicitly approved public worker source/workflow publication.
`aba059407951651a59a4163fcc67858771b60c3c` was pushed to
`MunawarAliAraiz/AI-Voice-Clone:codex/desktop-runpod-mcp`. Actions run
36906772717 built the CPU installer image; the GPU image failed on source-only
Python dependencies. The five exact hash-checked source exceptions are now
fixed locally and Linux dependency resolution passes. A new hosted build is
required; immutable release digests and anonymous pulls remain unqualified.
`backend/app/runpod/release.json` remains absent and provisioning is blocked.

Source now fixes the no-key green "online" status and header/form alignment.
Generation controls are gray with focus/hover explanations, visible reasons,
and Runpod/update navigation actions. Header and generation gate checks pass.
The Agents screen detects and explicitly configures Codex, Claude Desktop or
Claude Code without overwriting unrelated settings; ten MCP tools, durable
jobs, idle queue polling and completion/failure notification hooks are present.
33 agent/MCP tests passed with one Windows symlink privilege skip; actual
client handshake and native notification delivery remain to be qualified.

Convert supports pasted English/Hindi/Urdu and public YouTube captions into
editable script parts. Explicit translation drafts target Urdu script or
Roman Urdu. 88 focused tests and one actual English caption fetch passed;
GPU translation quality has not been tested. Videos without available
captions still need the future audio transcription path.

The 0.1.1 native build adds a fixed HTTPS update feed, embedded public signing
key, signed-version binding, byte progress and idle-only restart preparation.
Private signing material is DPAPI protected in the original checkout's ignored
`build/desktop/updater/signing-key.dpapi`; it must never enter Git or logs.
Feed publication and end-to-end update/restart qualification are unfinished.
The updater keeps the app usable if launching its installer fails.

FFmpeg is removed from the installer. First launch downloads the exact pinned
publisher archive, verifies archive and executable/notices hashes and exposes
real progress/retry. A real 114,768,076-byte download and audio processing
check passed in 88.39 seconds; this is not voice-generation evidence. Native
packaging must include the new audio-tools controller and admission guards.

The existing delivered 0.1.0 installer is older than these changes. Rebuild
and isolate-test 0.1.1 before replacing it. No new persistent volume, Pod,
endpoint or paid generation has been created. Model graph is pinned at
49.01 GB but has not been downloaded to Runpod. English/Urdu generation,
listening, provider billing and compute termination qualification are open.
Serverless mode comparison and recorded-audio conversion are unfinished.
Dialogue remains Beta; its generation tests are deferred by the user.

Next: finish native build and API/MCP freezing, test the isolated installer,
publish the already-approved worker fixes and qualify both image artifacts.
Prepare a concrete storage/compute quote for live GPU qualification. Update
this section with exact receipts at the next meaningful checkpoint.

## Local installer and automatic setup checkpoint — 2026-10-01

The Windows NSIS installer is now built. Disposable installation/upgrade,
native API/frontend/auth/SQLite, installed MCP (eight tools), single-instance
behavior, normal close and uninstall passed. A shutdown regression left the
onefile API child running; the shell now terminates its owned process tree.
The final source/UI bundle was rebuilt and independently installed in a fresh
isolated data directory. Native startup, frontend/auth/SQLite, cloud setup and
generation admission gating, installed MCP, single-instance behavior, window
close API cleanup and uninstall all passed. Final installer: 87,190,893 bytes;
SHA256 `5257E69307627431BA5BBC30AE0849AD09C4315D281CDAF7DB16F76E5C3C48F6`;
unsigned. `native-final-receipt.json` records hashes of all installed executables.
The development installer and receipt are copied to
`D:/Projects/AI-Voice-Clone/dist/desktop/`. Read [DESKTOP.md](DESKTOP.md).

Automatic setup is now integrated in source: one Runpod management key;
account credit via authenticated GraphQL; regional GPU/CPU discovery; 200 GB
Standard storage quote and explicit purchase; existing-volume selection;
CPU-only pinned model download/adoption with actual byte progress; complete
graph evidence gating; user-approved per-session/hourly compute limits;
cheapest regional eligible 48 GB NVIDIA GPU; disposable HTTP Pod sessions;
provider-side terminateAfter atomically included in GraphQL creation; queue
drain grace; retained model volume; ownership checks and uncertain-operation
reconciliation. Status/picker requests never create compute. Desktop GPU
requests and text helpers refuse admission until storage is verified and a
compute policy is approved. Manual Pod ID/token controls were removed from
the normal desktop setup panel. Legacy pairing APIs remain for development.

Current CPU installer graph: five model IDs across six pinned repositories,
including OmniVoice's embedded codec and Whisper; 49,014,734,674 bytes of
public pinned repository files. The Arabic VoxCPM alias shares base evidence.
F5 has no runtime and remains unsupported. Generation processes use offline
Hub/Transformers settings; Omni now resolves exact local snapshots and its
embedded Whisper locally. Fresh-worker full checksum verification reads
about 49 GB; reduced startup time has not been qualified. See
[MODEL_STORAGE.md](MODEL_STORAGE.md).

Verified this checkpoint: full backend/Pod regression suite passed (two
Windows symlink privilege skips), plus eleven new cloud controller/provider
tests passed; the final focused Runpod/controller suite passed all 16 tests;
frontend production build passed. Real readonly account/storage
discovery and a 200 GB/$14 monthly quote were observed in the updated browser
preview. No volume, Pod, endpoint or paid generation was created. These tests
do not establish actual GPU audio quality or shutdown billing.

Cloud image build source is ready: pinned bases/Actions, CPU installer + GPU
targets, hash locks, CPU container smoke and immutable digest/size artifacts;
eight deployment tests and actionlint passed. Local Docker is absent. The
GitHub connector reads the public fork but tree writes returned integration
403. Automatic approval review then rejected normal Git publication because
implementation authorization did not explicitly authorize public publishing.
An explicit publication question is pending. Do not bypass that rejection.
No commit/push/image publication occurred in that rejected command. The
controller intentionally refuses paid provisioning while
backend/app/runpod/release.json with reviewed immutable image digests is
absent. The build script now includes that file when a valid release exists.

Local source checkpoint `aa0341c` contains the native installer and automatic
setup implementation. Subsequent credential-account reset, malformed response
guards, installed gate checks and documentation are saved in the final local
checkpoint. Packaging audit found no bundled .env, DPAPI, database or weight
files. Public redistribution still needs complete corresponding FFmpeg source
or a compliant source offer; notices/README alone do not satisfy that gate.

Next: with explicit public publication approval, push
the development source/workflow to MunawarAliAraiz/AI-Voice-Clone, inspect
both hosted builds, qualify anonymous image pulls, and add release digests.
Then obtain concrete bounded storage/compute authorization for a real
English/Urdu voice test, listen to outputs and reconcile provider billing.
Serverless adapter/automatic mode comparison, cold-start optimization and
recorded-audio conversion remain unfinished. Dialogue is Beta and its
generation tests are deferred at the user's request. No production readiness
claim is warranted from the current local installer.

## Desktop continuation checkpoint — 2026-09-30

**Latest priority:** the user requested Dialogue be marked **Beta** and deferred
its generation tests. The tab and panel now carry Beta labels. Complete the
existing desktop app and accepted Runpod setup/lifecycle first; do not make
dialogue listening tests a prerequisite for this checkpoint. Recorded-audio
overlap research remains documented but is not the current implementation focus.

Worktree: `C:/Users/abdus/.codex/worktrees/desktop-runpod-mcp/AI-Voice-Clone`,
branch `codex/desktop-runpod-mcp`. Baseline scaffold commit `25f1187`; validated
source continuation is saved as `1f62386`. Native build/test refinements remain
under verification. Original checkout is preserved. No push or release has occurred.

Implemented since the previous checkpoint: encrypted dynamic Pod pairing,
user-triggered pinned downloads and completion markers; remote Qwen Speech
Direction and Gemma conversion; automatic DPAPI MCP session discovery;
standalone MCP executable packaging; three-voice Dialogue editor and local
draft/assembly API; validated one-hour/three-overlap speech plans and actual
PCM WAV assembly/mixing; Docker worker recipe and isolated hashed dependency
locks. Read [SPEECH_PIPELINE.md](SPEECH_PIPELINE.md), [POD_WORKER.md](POD_WORKER.md)
and [MCP.md](MCP.md).

Verified: **484 backend regression tests passed** with plugin autoload disabled,
the async pytest plugin enabled, a normal Windows profile for DPAPI, and the
verified FFmpeg 9.0.2 on a temporary PATH. Frontend production build passed.
Real stdio MCP SDK and frozen executable protocol tests passed (eight tools),
including encrypted descriptor replacement across sessions. The frozen API
executable passed health, static UI, authenticated API, SQLite and DPAPI checks.
Browser QA caught missing JSON request headers on the new draft/assembly client;
these were fixed and rebuilt. Dialogue drafts now survive reload via local API
storage. Live GPU estimate rows and connected account state were observed.
API fixtures and tone WAVs in tests are not real voice generations.

The user explicitly allowed reading the Runpod key from the video project's
`.env`. Use it in-process without printing or copying it into source/docs.
Read-only live requests succeeded: no Pods, one 50 GB video network volume,
and live GPU pricing (A40 48 GB secure $0.49/hour at this check). Do not reuse
the video volume for this app. No new Pod/volume or paid generation has occurred.

Build progress: MCP `voice-clone-mcp.exe` was built with SDK 2.2.0 and PyInstaller
6.19.0 and tested. Task-local Rust MSVC is installed; Microsoft C++ Build Tools/
Windows SDK installation and the native shell build are underway. API and MCP
sidecars have both been rebuilt and smoke tested against the current source. A complete
NSIS installer has not yet passed a clean-install check. Docker is unavailable;
the Pod image is not built/published. F5 lacks a runtime backend in the current
code. Automatic diarization/separation and production voice conversion remain
open, including real listening/cost tests. A pinned standalone Chatterbox VC
adapter has 11 passing contract tests, but has no worker/queue integration or
real GPU test. The user explicitly requested **alternatives without model
access gates** for speaker separation; do not assume pyannote terms acceptance.

Native build reached Rust build scripts; the first link failed with
`LNK1181: kernel32.lib` because Windows SDK import libraries were absent.
Build Tools then completed successfully without a reboot, and the SDK import
libraries appeared. The packaging task is retrying from cached dependencies with
the latest frontend (including API-key prompt removal) and a disposable data-dir
override. No successful native shell/NSIS result yet.

Next: finish native desktop build/toolchain and clean-install verification,
commit the checkpoint. Research ungated overlapping-speaker models in parallel.
Then build/publish a tested Pod image and implement
safe provisioning, qualify real English/Urdu TTS and voice conversion, and
complete recorded-audio review/chunk/reassembly APIs and UI. Existing full
feature residency requires 43,548 MiB (48 GB tier); 200 GB storage is recommended,
150 GB is provisional and still needs a measured all-model manifest.

The sections below are historical checkpoints, not current readiness claims.

### Latest user steering: one-key setup and automatic GPU lifecycle

The user rejected the web API-key settings prompt in the desktop UI. It is now
hidden when the shell session key is present; desktop authentication remains
automatic and the web build retains its existing control. Production frontend
build and a live browser reload passed after the change. The screenshot proof
is stored in the task's local visualization directory.

The intended flow is now: Runpod key -> discover/select/create persistent model
volume -> verify/adopt existing pinned models or download with actual progress
-> unlock model-dependent operations -> choose the cheapest compatible available
GPU automatically -> run the queued batch -> save output locally -> release
compute while retaining weights. Manual worker pairing in the current UI is a
temporary implementation flow, not the accepted final product. See
[DESKTOP_ARCHITECTURE.md](DESKTOP_ARCHITECTURE.md) and
[CLOUD_LIFECYCLE.md](CLOUD_LIFECYCLE.md).

Serverless flex with zero active workers, explicit FlashBoot, max one worker,
short idle timeout and provider execution timeouts is the proposed default to
qualify. It still bills startup/model loading. Network-volume storage remains
billable without GPU compute and constrains GPU availability to its region.
No production Serverless adapter or endpoint has been created. Current install
status reports states only; complete checksum adoption and real download-byte
progress are still required. Any installer built from an earlier source snapshot
must be rebuilt with this UX change before release.

Pure lifecycle planning contracts now exist in `runpod/lifecycle_plan.py`;
16 focused tests passed, with Ruff clean. They validate complete pinned-file
evidence, setup states, byte/file progress, regional compatible GPU ranking,
budget filtering and safe queue-drain release conditions. They do not perform
transfers/provisioning, and no route or job runner uses them yet.

Ungated overlap research is documented in [SPEECH_MODELS.md](SPEECH_MODELS.md).
Anonymous binary range downloads succeeded for pinned NVIDIA Sortformer,
SpeechBrain Libri3Mix and ECAPA checkpoints. This removes the proposed access
gate, not the qualification work: Libri3Mix is 8 kHz and trained on English
synthetic mixtures; source-count handling, long-recording identity stitching,
Urdu quality, complete dependency graphs and GPU cost/memory remain untested.
Gated pyannote is retained only as excluded historical research.

## Desktop/Runpod implementation checkpoint — 2026-09-29

The new Windows desktop + user-owned Runpod + MCP work is on branch
`codex/desktop-runpod-mcp` in a managed worktree. Read
[DESKTOP_ARCHITECTURE.md](DESKTOP_ARCHITECTURE.md) and [DESKTOP.md](DESKTOP.md)
before continuing. Root `AGENTS.md` is the project index.

Implemented in source: loopback static serving and session-key API, Tauri shell
source, a Windows build script, a stdio MCP adapter, authenticated Pod TTS
worker, remote scheduler, pinned-model install API, Runpod REST v2 client,
Windows DPAPI management-key storage, GPU cost preview, and read-only Pod/volume
analytics in a desktop-only tab. **No installer has been built. No real Runpod
generation, model download, or billing call has been tested.** The current
desktop app does not yet connect its inference scheduler dynamically when a
Runpod account is added.

Checks: frontend `npm run build` passed using a temporary link to existing
dependencies; Python Ruff passed for new modules. The full backend suite ran:
one contract allowlist test failed because the new `remote_scheduler` module
was absent from its explicit list; that list was updated and the failing test
then passed. All other backend tests passed on that full run. DPAPI tests need
a normal Windows user profile; the sandbox profile returned Win32 error 2,
and those tests passed outside it. MCP SDK protocol, Rust/Tauri, PyInstaller,
Pod image, and GPU paths remain untested here.

Next: publish and pin a real Pod worker image, validate its pinned runtime
environments, then add safe Pod/volume provisioning and a persisted Pod
connection that the local scheduler can use. After that, run a real GPU TTS
smoke test, inspect audio, record actual cost/timing, and build a clean Windows
NSIS installer. Scripted dialogue and one-hour three-speaker conversion still
need implementation and listening gates. Existing full-feature capacity is
43,548 MiB, so 48 GB is the first supported tier under current residency.

---

Written so a fresh session (or a fresh pod, or a different person) can resume without
reconstructing anything. **Update this at every checkpoint.** The previous incarnation of this
project lost a day of planning because the only copy lived on a pod that was terminated.

---

## ⚡ Start here (state as of 2026-08-19)

> **If you read only one section, read this one.** Everything below it is history explaining how
> the current state came to be.

**Branch/PRs.** Push to the **`fork`** remote (`MunawarAliAraiz/AI-Voice-Clone`), not `origin`.
Merged into `main`: **#22** (Roman-Urdu Phase A: transcript import, chapters, transliterator,
convert-on-generate) and **#23** (YouTube cookies — now superseded). **OPEN: #25** on
`feat/convert-tab` — replaces YouTube import with a paste-and-convert **Convert** tab (see below).

**Live pod:** `.claude/remote.local.md` (gitignored). Current pod **`194.68.245.87:22052`**, an
**RTX A6000 (46 GB)**, fully bootstrapped, **backend UP**, all six venvs incl. `.venv-gemma`.
- **The A6000's 46 GB changes nothing** — design for a **24 GB** card (`budget_mb = 16000`,
  `max_workers = 2`). A Phase B measurement here is an UPPER bound, not proof it fits on 24 GB.
- **Script conversion is AVAILABLE and verified** — Gemma wired via `VCS_GEMMA_TRANSLITERATOR_PYTHON`
  (the bootstrap now exports it; it used to forget to — that was a real bug). A live transliterate
  job returned correct Perso-Arabic (`"Aap kaise hain…"` → `"آپ کیسے ہیں…"`, ~5 s warm).
- **Secrets** are minted in `/workspace/vcs-secrets.env` (survive restarts). Pod's `VCS_API_KEY`
  differs from any local one — paste it into the frontend settings gear.
- **Lifecycle:** `/workspace/ctl.sh {up|start|restart|stop|status}`. Bootstrap re-run needs no
  `BRANCH=` now (main has everything except #25). Always pass
  `UV_HTTP_TIMEOUT=600 HF_HUB_DOWNLOAD_TIMEOUT=600`.
- **Pods die often** (three in three days earlier). Push at every checkpoint; `/workspace` has
  arrived empty on every pod change.

### What is DONE and what is only WRITTEN

| | State |
|---|---|
| Pronunciation dictionary, generation titles, non-blocking Generate, merged Recent/History | ✅ shipped, browser-verified |
| Clause/sentence/paragraph breaks (Urdu `،`, newlines) | ✅ shipped, tested |
| Phase B transliterator (Gemma-4-31B): scheduler, validator, `/api/text/transliterate`, lifespan | ✅ shipped; **GPU-verified** on the A6000 pod (`latin → perso_arabic` returns correct Urdu) |
| Composer convert-on-generate (Roman Urdu → Urdu script, client-side, with review) | ✅ shipped; backend half GPU-verified. Frontend active path needs the pod API key pasted in the browser to exercise |
| **Convert tab (was YouTube Import)** — paste a script, detect source, convert per-source, review, send to editor | 🟡 **PR #25 open.** YouTube fully removed. Browser-verified UI + degraded state locally; **real Gemma conversion not yet browser-verified on the pod** |
| Devanagari as a source script (prompt, exemplars, detection, echo check) | ✅ wired & tested, ❌ **UNGATED** — the Devanagari listening gate has never been run |
| English → Urdu **translation** (a different operation from transliteration) | ❌ not built — planned follow-up, both targets (Urdu script + Roman Urdu). Needs a source-language declaration (English is indistinguishable from Roman Urdu) and a translate path that does NOT reject a non-echo |

### The next three things, in order

1. **Browser-verify the Convert tab's real conversion on the pod (PR #25).** The UI and degraded
   state are checked, but the actual Gemma run through the tab is not. Deploy `feat/convert-tab` to
   the pod, point the local frontend at it (tunnel `-L 8010:127.0.0.1:8000`, `VITE_PROXY_TARGET`),
   paste the pod key, and confirm: paste Devanagari → Convert all → correct Urdu on the right parts →
   Send to editor. Also finish the same check for Composer convert-on-generate.
2. **The Devanagari listening gate.** The path is built and reports `source_script` on every result
   so an ungated conversion is identifiable, but nothing has *heard* one. Add a Devanagari arm to
   `eval/run_roman_arabic_probe.py` and run the A3 protocol end to end. Synthesis is unseeded —
   sample repeatedly, listen blind; the numbers can only fail a candidate. The Convert tab correctly
   blocks Send on an unconverted Devanagari part — keep that true until the gate passes.
3. **English → Urdu translation** (the follow-up the owner asked for). See the last table row and
   `docs/TRANSCRIPT_IMPORT.md`'s header note. It is TRANSLATION, not transliteration — a new Gemma
   prompt/path whose validator must not reject a non-echo, plus a source-language selector in the
   Convert tab (English can't be auto-detected from Roman Urdu).

### The Roman-draft question — DECIDED 2026-08-18

The owner's proposal — type/import **Roman Urdu**, keep it as the readable editable draft, convert to
Perso-Arabic for OmniVoice (which has no `(ur, LATIN)` cell, and where VoxCPM2's direct Roman
rendering is the A0 finding the owner heard as an English accent) — was **adopted**, in the third
shape: **both kept; editing the Roman marks the Urdu stale and blocks Generate until re-converted.**

The one cost that shape carried (`~78 s` Gemma load per re-convert) is **obsolete**: Gemma is
resident since 2026-08-17, so a re-convert is ~5 s. Implemented two ways:

- **Convert tab** (`useTranscriptParts`): `source` readonly vs editable `draft`/`converted`,
  `outgoing = draft ?? converted ?? source`, edit marks the part stale/edited.
- **Composer** (client-side convert-on-generate): Generate runs the conversion when the selected
  model can't read Latin, shows the Perso-Arabic for review, one tap generates. `resolve()` and
  `TransformKind` are untouched — the sequencing is in the UI so the review step can exist (golden
  rules 4/5).

Still **not exposed**: `generation_history.resolved_text` ("the post-transform string the model
actually received") in `HistoryItem`/`frontend/src/types/api.ts` — deferred, so history can't yet
show the Roman a clip came from.

### Hindi: the fact that governs the whole transcript feature

**No model here renders Hindi, and that is deliberate.** `hi` is not a `LanguageCode`
(`domain/language.py`), no catalog spec declares a Devanagari cell (`f5_indic` was removed), and
`domain/routing.py` raises `NoRouteError` for `(ur, DEVANAGARI)` with a comment saying accepting it
"would quietly make the language field meaningless". OmniVoice claims only `(ur, ARABIC)`;
VoxCPM2 claims `(en, LATIN)` and `(ur, LATIN)`.

Hindi therefore exists here as a **source format only, never a target language** — a Devanagari
transcript is text OmniVoice could speak *if it were Perso-Arabic*, which is what the transliterator
is for. `POST /api/transcript/fetch` already returns `needs_transliteration`, computed server-side
from the catalog so the UI never encodes routing rules.

**The headline: the Roman-Urdu → Perso-Arabic feature PASSED its listening gate on 2026-08-16,
on the third model tried. Phase B is unblocked.** A3 ran three times against the same harness:
Qwen2.5-7B → *"not usable"*; Ministral-3-8B → ten reported defects; **Gemma-4-31B at 4-bit →
*"perfect with the current data… it's best"***. Reasoning per run:
[URDU_BAKEOFF_RESULTS.md §9–§15](URDU_BAKEOFF_RESULTS.md).

Runs 2 and 3 scored the **same contract rate to within one point** and landed on opposite sides of
the gate. Treat that as settled: **the text metrics can only fail a candidate, never approve one**,
and every candidate goes through a listen. Prompt engineering is also not a lever — Gemma's four
arms are within noise of each other because it already holds the constraints, inverting §10a where
Qwen could not hold them at all.

**Two constraints Phase B must design around before writing code:**
1. **Gemma-4-31B is ~19 GB resident** (4-bit, 78.4 s cold load) against a 24 GB card with
   `budget_mb = 16000`. It never needs to be co-resident with OmniVoice — convert, unload, user
   edits, Generate — but a second scheduler that can demand 19 GB while sitting outside the main
   scheduler's GPU-slot semaphore is exactly what golden rule 3 exists to prevent. `AnalyzerScheduler`
   gets away with it only because Qwen2.5-3B's ~6 GB fits in the slack. **Ministral is the named
   fallback, but run 2 is the measured record of how it sounds and it did not pass — do not
   substitute on VRAM grounds without re-running A3 on the substitute.**
2. ~~**The one remaining defect is dictionary work with a new requirement.**~~ **Done — the
   dictionary shipped 2026-08-16/17** (see below). میٹنگ is read as *mating*; gold writes میٹنگ too,
   so it was never the model's doing. Because the text arrives already in Perso-Arabic,
   `_LOANWORD_LEXICON`'s Latin-only keys could never match it — the lexicon is now data-driven and
   `مِیٹِنگ` ships as the first Perso-Arabic-keyed default, which is what proves the either-script
   requirement is satisfiable rather than merely stated.

**Shipped since the gate passed (2026-08-16/17):** the **pronunciation dictionary** end to end
(`pronunciation_entries` table, `/api/pronunciations` CRUD, a `Pronunciation` tab, a pure
`effective_lexicon` merge policy, and a `get_lexicon(db)` dependency that keeps golden rule 4's
`resolve()` free of I/O), plus the Studio workflow changes: analyzer-suggested editable titles
returned in the *same* CLASSIFY response as the prosody rows, a Generate button that enqueues and
re-enables immediately with a queued toast and an In progress strip, Recent+History merged, an
editor toolbar attached to the textarea, adaptive job polling that stops when nothing is in flight,
and startup warm-up that runs a throwaway synthesis per model (weights alone don't remove the
~160 s stall — OmniVoice's embedded Whisper loads on first `synth()`). It also added **the first
schema migration this project has had**: an add-only `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`
pass in `Database.connect()`, because a new `title` column would otherwise have reached a fresh
install and silently missed the pod's real database. `pytest -m "not gpu"` and `npm run build` are
both green; **none of it has been exercised against the pod in a browser yet** — that needs the
API key pasted into Settings, and it is the first thing to do.

**What a fresh session should do first:**

0. **Click through the shipped UI against the pod** (SSH tunnel + `npm run dev`, key pasted into
   Settings): add a dictionary entry and hear it applied, generate twice in a row without the
   button locking, confirm the queued toast and the In progress strip, and check the startup log
   shows both models warmed *and* synthesized. Everything above is verified by tests and a local
   build only.

1. ~~**Design Phase B, starting with the VRAM question above.**~~ **The VRAM question is ANSWERED
   and the answer is built** (2026-08-17): `InferenceScheduler.exclusive_gpu(reason)` holds the
   main GPU slot and evicts EVERY worker, so Gemma gets an empty card and no synthesis can run
   while it is resident. `TransliteratorScheduler` owns nothing between calls — spawn, LOAD,
   TRANSLITERATE, kill, every time — because at ~19 GB it is resident or the audio models are,
   never both. `_make_room_for` could not express this: it evicts until a spec fits a budget
   deliberately sized for co-residency (16 GB), so a 19 GB spec fails its first check.

   **This is a SECOND EVICTION CALL SITE, an explicit amendment to golden rule 3**, argued in
   `scheduler.py`'s module docstring rather than left to be discovered. The property the rule buys
   — unload-during-inference being unrepresentable — is intact: the same semaphore is held across
   the whole body and eviction still goes through the same `_evict` behind the same assertion.
   `tests/test_scheduler.py` asserts both that the card is emptied and that no synthesis completes
   during the window.

   **Wired to the API 2026-08-17, later the same day.** `POST /api/text/transliterate` returns 202
   and enqueues `JobKind.TRANSLITERATE`; the handler owns the ORDER (convert, then validate) and
   nothing else, so "is this a transliteration or an answer" stays provable without a 19 GB
   download. A validator rejection **fails the job** carrying the reason code — the text never
   reaches the client, because returning it with a warning attached is golden rule 5's silent
   substitution one layer above audio. `route=None`, like `analyze_llm`: `resolve()` is never
   called and `TransformKind` is untouched.

   Also fixed then: `GEMMA_TRANSLITERATOR_HF_REVISION` was the literal string `"main"` under a
   comment claiming it was pinned — the exact supply-chain hole golden rule 7 exists to close, made
   worse by a comment that stopped anyone looking. Now `842da3794eaa…`, and the repo id gained its
   **capital B** (`google/gemma-4-31b-it` is a 307 redirect to `google/gemma-4-31B-it`).

   **What still has not happened: any of it running on a GPU.** No pod has ever had `.venv-gemma`.
2. ~~**Build the user-editable pronunciation dictionary.**~~ **Built.** §9e's measurement is why it
   exists — **17.2% of English loanword instances** (11 of 54 distinct words, 32.5% of generations)
   are mispronounced by OmniVoice, and 9 of the 11 fail *every* time, so a respelling genuinely
   fixes them rather than shifting a coin flip. What remains is owner work, not code: the built-in
   defaults are deliberately thin (three entries), and adding a fourth means picking a respelling
   by ear. §15f's tie stands unbroken — five spellings of `meeting` scored equally over 4 blind
   samples each, and the owner's point that hand-picking respellings is exactly the work the
   dictionary exists to hand back to the user is why no second sampling round was run.
   Design notes in §9d/§15d.
3. **One one-click owner action:** accept the licence at huggingface.co/ai4bharat/IndicF5 to unblock
   bake-off arms H/I/J. (`docs/outreach/mavkif-licence-request.md` is now **moot** — it existed only
   to reopen a feature that has since passed its gate on Gemma-4-31B. Don't post it.)
4. **Native review of the 32 new corpus gold strings** (`_meta.authoring_rule_EXCEPTION_phase_a_items`).
   They were drafted by Claude, and any number scored against them is provisional until reviewed.
   §15b showed the corpus gold is itself inconsistent in places — it converts `office`/`file`/`meeting`
   to Urdu script while the contract says to keep English in Latin.

**Three findings from the 2026-08-17 analyzer debugging, all verified on the pod:**

- **`AnalyzerScheduler` must hold one lock across the whole `classify()`, including the wire call.**
  `worker_client.py`'s docstring states the precondition — it needs no locking of its own *because*
  the scheduler holds the slot — and `AnalyzerScheduler` was releasing at start+load. Two concurrent
  callers then wrote two frames onto one stdin; `WorkerProcess.call` caught it by request id and
  killed the worker, so every collision cost a ~30 s reload the next collision destroyed. Latent
  until the debounced title suggestion became a second caller.
- **Qwen2.5-3B ends this prompt's response one `}` short.** It closes the rows array and stops.
  **Byte-identical at `max_new_tokens` 300 and 900 under greedy decoding**, so it is choosing to
  stop, not being cut off — don't "fix" it by raising the ceiling again. `_scan_object` appends
  closers only for brackets it watched open; a genuinely incomplete response still fails, and
  everything still goes through the same strict validation.
- **The direction preview makes zero Qwen calls.** `routers/direction.py`'s preview is the pure
  heuristic `analyze()`. Only the "AI suggest" path calls the LLM, and that one *does* carry the
  title in the same response as the rows. So a title alongside the preview is necessarily a
  separate call — there is no model response to ride along in.

**Two live findings that change how all future evaluation is done here:**

- **Synthesis is unseeded** (§9b). `OmniVoiceBackend.synth()` sets no seed, so a word's pronunciation
  is a random variable and any n=1 listening verdict is a coin flip. This is not theoretical — it
  produced two opposite owner verdicts on one byte-identical sentence an hour apart, and it explains
  `late` passing, failing, then passing across three listens. Sample repeatedly and listen blind;
  `eval/run_loanword_reliability.py` is the pattern.
- **Numeric screens can only fail something, never approve it.** Four demonstrations now: A0's ASR
  screen looked encouraging while the owner heard a plain English accent; §10's contract metric
  passed `owner_01_sick`, whose Urdu is mangled; §14b's four best-scoring items (CER 0.020–0.077,
  contract OK) each contained an error caught by ear; and §15a's runs 2 and 3 scored the same
  contract rate to within a point while landing on opposite sides of the gate.

Historical detail on how the current state came to be follows.

---

## LoRA withdrawn, OmniVoice shipped, code-switch bug fixed (2026-08-15)

Originally branch **`feature/urdu-bakeoff`**, since merged. The bake-off itself (130/130
blind-scored, arms A–E) was already complete as of the previous checkpoint below, and arm D (VoxCPM2
+ LoRA) had been integrated as `voxcpm2_urdu_lora`. **This checkpoint reverses that** based on real
usage, then ships the bake-off's actual best-scoring arm properly.

**1. The LoRA was withdrawn.** Using the real running app (not the eval harness), the owner's verdict
was that **base VoxCPM2 sounds better than the LoRA** — contradicting the blind-listen median (4.0 vs
3.0). Per this project's own "owner listening is authoritative" rule, that overrides the earlier
score. `voxcpm2_urdu_lora` is deleted from the catalog (LoRA runtime plumbing — `lora_local_path` etc.
on `VoxCPMBackend`/`ModelSpec` — is kept; it's generic and free when unused). Replaced by
`voxcpm2_urdu_arabic`: the same base checkpoint, no fine-tune, `experimental_listing=True`,
`verified=False`, so Perso-Arabic Urdu isn't left with zero routes. `docs/URDU_BAKEOFF_RESULTS.md` §5a
records the withdrawal honestly, including that this is consistent with Q5's original finding that the
LoRA's gain may have come from matching arm C's representation rather than the fine-tune itself.

**2. Two real bugs found and fixed while testing in the app:**
   - The model picker rendered `ModelSpec.notes` (a maintainer field — env var names, doc paths) raw
     as a user-facing sentence. Added `ModelSpec.caveat: str` (≤140 chars, tested), the only
     user-facing prose field; `Composer.tsx` now renders one line, not a wall of text. Trimmed further
     after the owner flagged even the first version as "too much text."
   - **Code-switched Urdu was unroutable.** `میں نے GitHub پر ایک نیا pull request بھیجا ہے` (Latin
     loanwords inside Perso-Arabic Urdu — UrduSpeech's own corpus is 57% code-switched) measured under
     the 0.85 script-dominance threshold and was rejected as `AmbiguousScriptError` before routing ever
     ran, despite every model handling code-switching fine once it got there. Fixed in
     `domain/language.py`'s `profile_text()`: for a language with a native non-Latin script, Latin runs
     under `LATIN_ISLAND_CEILING = 0.75` are now treated as loanword islands, not ambiguity.
     `detect_script()` itself is untouched (needed pure elsewhere). **Confirmed live** — see below.

**3. OmniVoice integrated as a new runtime** (`RuntimeKind.OMNIVOICE`,
`backend/app/inference/runtimes/omnivoice.py`) — the bake-off's actual best-scoring arm (5.0/5
pronunciation, the only Urdu cell whose CER+cosine gate passes on both references), CC-BY-NC
licensed. Golden rule 6 in `CLAUDE.md` was amended: NC weights are now allowed in the catalog **for
personal use behind `VCS_API_KEY`**, badged (`ModelSummary.commercial_use`), never for a shipped
product. `RESEARCH_ONLY` (Higgs Audio v3's tier) stays fully banned. Real pod smoke test against the
production `OmniVoiceBackend` (not the eval harness): load 159.3s, synth 17.1s (5.48s audio, peak
0.7573 — non-silent), clean unload. Found a real architectural detail along the way: OmniVoice
lazily loads an embedded Whisper sub-model on the *first* `synth()` call (not during `load()`) when
no `ref_text` is supplied, adding latency beyond the reported load time.

**4. Live end-to-end verification, real pod backend + local frontend (2026-08-15), just completed
before this pod is terminated.** Backend run via `serve.sh` on the pod (port 8000), tunneled to the
local machine over SSH (`ssh -L 8000:127.0.0.1:8000`), local Vite dev server (port 1420) proxying
`/api` through the tunnel — no ngrok needed since the API key requirement was dropped for this
private-tunnel test only (never touched `vcs-secrets.env`). Confirmed in the real running app:
   - The picker shows the new catalog cleanly: `VoxCPM 2`, `VoxCPM 2 (Urdu, اردو script)
     (Experimental)`, `OmniVoice (Urdu) (Experimental, Non-commercial)` — `voxcpm2_urdu_lora` only
     survives in old history rows, not as a selectable model.
   - Manually selecting OmniVoice and generating real Perso-Arabic Urdu against a real enrolled voice
     **worked end-to-end through the actual job queue** — `route.rationale` correctly read "ur in
     arabic script rendered by OmniVoice (Urdu) — EXPERIMENTAL: you explicitly picked this model..."
   - **The code-switch fix is confirmed live, not just in tests**: a fresh sentence with English
     loanwords (`میں نے GitHub پر ایک نیا pull request بھیجا ہے، امید ہے آج ہی review ہو جائے گا۔`)
     resolved to `source_script: arabic` and routed to OmniVoice successfully — previously this would
     have 422'd as `AMBIGUOUS_SCRIPT` before reaching routing at all.
   - **Roman Urdu → OmniVoice does NOT work, by design, not yet by gap.** `omnivoice_urdu`'s catalog
     entry only declares an `(ur, ARABIC)` `LanguageSupport` cell — no `(ur, LATIN)` cell. Per
     `routing.py`'s `resolve()`, an explicit model request is honored or refused, never silently
     swapped, so Roman Urdu text with OmniVoice explicitly selected raises `NoRouteError` (422). This
     is exactly what Phase 2 (the transliteration viability probe, not yet started — see below) exists
     to potentially unlock; it is not a bug in what shipped.

**5. Pod is being terminated by the owner right after this checkpoint.** A fresh pod will be needed
next session — run `pod-bootstrap.sh` against this branch (it now provisions `.venv-omnivoice` too,
steps 10-11). Everything from this checkpoint is committed and pushed to `fork/feature/urdu-bakeoff`
(`2634372` at time of writing) — nothing was left pod-only.

**Still open, unchanged from before:** IndicF5 (arms H/I/J) blocked on `HF_TOKEN`. Also still open:
Phase 3 (IndicF5 arms H/I/J), Phase 4 (fine-tune VoxCPM2 on UrduSpeech, largest, not started).

**Resolved since:** the production CER/cosine gate re-run (arm Eprod) cleared comfortably on both
references, and the owner flipped `OMNIVOICE_URDU`'s `(ur, ARABIC)` cell to `verified=True` on
2026-08-15 on top of that. It is still CC-BY-NC, so `ModelCatalog.candidates()`
(`inference/catalog.py`) now excludes non-permissively-licensed specs even once verified — Auto routing
still never reaches it, only an explicit `model_id=omnivoice_urdu` request does (and no longer needs
`allow_experimental=True` to do so). Phase 2 (transliteration viability probe via Qwen2.5-3B) ran twice
— Devanagari target (§8) and, since OmniVoice's own cell is Perso-Arabic not Devanagari, a Perso-Arabic
target retry (§8b) — both missed the gate. Full detail: `docs/URDU_BAKEOFF_RESULTS.md` §5d/§8/§8b.

**Root-cause finding that reframed the work:** VoxCPM2's published list is 30 languages including
Hindi and Arabic but **not Urdu**, and its card says it infers language from the text. So Roman Urdu
likely gets Hindi phonotactics and Perso-Arabic may get *Arabic* phonology — possibly worse. That is
`[INFER]`, not measured; arms A/B/C exist to settle it.

**Also corrected:** a prior pass dismissed IndicF5 for not listing Urdu. Wrong — Hindi and Urdu are
one spoken language differing mainly in script, so a missing language label constrains the *input
representation*, not necessarily the phonetic capability. `docs/URDU_CLONING_REPORT.md` §2 ruled
transliteration out as a fix for **speaker identity**; it never tested it for **pronunciation**.
Those axes are now scored separately.

| Piece | State |
|---|---|
| `tts.py` transform-path bug | ✅ Fixed + regression test. Called `GenerationError(detail)` against a 2-arg `__init__`, so the "fail loudly" branch raised `TypeError`. Now a clean 422 `NoRouteError`. Reachable via `urdu_strategy="translit"`. 244 tests pass. |
| `eval/fixtures/urdu_corpus.json` | ✅ 13 items — owner's 5 sentences + numbers (ASCII/Eastern as a controlled pair), dates, names, acronyms, technical terms, colloquial Pakistani, long multi-clause. |
| `eval/urdu_represent.py` | ✅ `strip_nuqta` (derives arm I from arm J), `normalize_urdu`, `to_ascii_digits`. |
| `eval/run_urdu_bakeoff.py` | ✅ Synthesis, one arm per invocation. |
| `eval/score_urdu_bakeoff.py` | ✅ Scoring, runs in `.venv-eval`. |
| `eval/build_listen_page.py` | ✅ Blind listening page, verified in-browser. |
| `docs/URDU_MODEL_LICENSING.md` | ✅ Full report, code-vs-weights checked separately. |
| `docs/URDU_BAKEOFF_RESULTS.md` | ✅ Written, **§5 decision table deliberately empty** until the listen. |
| Arms A/B/C/D (VoxCPM2 ×3 repr + LoRA) | ✅ 13 items × 2 references each. Scored. |
| Arm E (OmniVoice) | ✅ 13 items × 2 references. Scored. Lightest arm: 4.5 GB, 0.44 RTF. |
| Arm F (Higgs v3) | ⛔ **could_not_run, recorded with the reason** — see below. |
| Arm G (code-switch) | ✅ Reporting slice over A–E. Numbers are a **metric artifact**, see results §4. |
| Arms H/I/J (IndicF5) | ⏸ **Blocked on an `HF_TOKEN`** + the female transcript. venv pre-built at `/workspace/engines-lab/r1-f5/`. |
| Blind listen | ✅ **130/130 clips scored (arms A–E), one rater.** See `docs/URDU_BAKEOFF_RESULTS.md` §3. |

**Arm F is genuinely impossible on this pod, not skipped.** transformers 5.15.0 has no
`higgs_multimodal_qwen3`; `config.json` has `auto_map: null` so `trust_remote_code` can't rescue it;
the only documented self-hosting path is the `lmsysorg/sglang-omni:dev` Docker image and Docker is
not installed (a RunPod container can't nest one); and mainline pip `sglang` has zero Higgs models
(0 GitHub code-search hits). Boson lists ≥40 GB as known-good, 24 GB as unverified. **Don't retry
this without either Docker or a ≥40 GB card.** `_load_higgs` now preflights and raises with that
whole diagnosis, which the harness records as `could_not_run`.

**Arms H/I/J need one click from the owner.** `ai4bharat/IndicF5` is `gated=auto` — metadata reads
fine anonymously, but `model.safetensors` (1.4 GB) returns `GatedRepoError: 401`. Because the whole
repo is gated, `trust_remote_code` can't fetch the shipped `f5_tts/` modules either. `gated=auto`
means approval is **automatic on accepting the terms**, so this is a token, not an application.
Arm I is the plan's central question, so this is the highest-value unblock available. The `.venv` at
`/workspace/engines-lab/r1-f5/` is already built (f5-tts, vocos, torchdiffeq, transformers 5.15.0,
torch 2.13.0+cu130) — running arm I is one command once the token lands.

**Blind listening is done for arms A–E — 130/130 clips, one rater (the owner).** Full breakdown in
`docs/URDU_BAKEOFF_RESULTS.md` §3–5; headline findings:
- **Devanagari input (arm C) and the LoRA (arm D) both beat Roman/Perso-Arabic VoxCPM2 (A/B) by a
  full point on pronunciation and naturalness** (4.0 vs 3.0), tied on identity and code-switch. This
  is the strongest *commercially clean* result.
- **OmniVoice (arm E) rated highest on pronunciation (5.0)** but worst on code-switch (3.0) — and is
  NC-licensed, so it's evidence about the ceiling, not a deployable answer.
- **The LoRA's `docs/VOXCPM_LORA_POC.md` cosine regression did not survive to the ear** — blind
  identity scores were flat at 4.0 for every arm including D. Trust the listening score over the
  automated cosine here; this is exactly the divergence the two-axis design exists to catch.
- **The corpus's number items read digits as digits, not spoken Urdu number-words** — the owner
  flagged this independently on 4 clips across 3 unrelated arms (A/C/D), which is the signature of a
  shared input problem, not a per-model one. Not fixed; `eval/fixtures/urdu_corpus.json`'s
  `date`/`num_ascii`/`num_eastern` items need spelled-out Urdu numerals before their next use.
- Two clips (`C/female/num_eastern`, `D/owner/owner_02_file`) reported as unplayable in the browser;
  both underlying WAVs checked directly and are **not silent** (peak 0.99 / 0.92) — a page playback
  glitch on those two specific clips, not a synthesis defect. 1.5% of the corpus, unresolved, low
  priority.

**No model has been chosen for integration.** The commercially-clean leaders are arms C and D
(tied). Questions 1, 3, and 6 in the results doc stay open until arm I runs or the owner explicitly
defers it — do not integrate anything off the back of this listening pass alone.

**Devanagari is hand-authored gold, deliberately.** Arms C/I/J are therefore a **ceiling test**: if a
model fails on perfect Devanagari, no converter rescues the route; if it succeeds, a converter is
then worth building. Do not read those arms as "our transliterator works".

**Findings to carry forward:**
- **No commercially-safe open-source Urdu voice-cloning model exists** (as of 2026-08). The two that
  genuinely list Urdu *and* clone — Higgs Audio v3, OmniVoice — are both non-commercial. Every
  permissive cloner omits Urdu, or (Indic Parler-TTS) omits cloning; its maintainer says it always
  will. Full detail + licence classifications: `docs/URDU_MODEL_LICENSING.md`.
- **Weights licences ≠ code licences.** OmniVoice ships Apache-2.0 code with **CC-BY-NC weights**;
  the blogs calling it commercially free read the wrong file. Golden rule 6 was relaxed this session
  to permit NC weights **for personal use behind the API-key gate** — that is not a commercial licence.
- **⚠️ LoRA run 1's `lora_weights.safetensors` is GONE.** Only `lora_config.json` and
  `training_state.json` survive at `eval/results/voxcpm_lora/checkpoint_backup/`. Only run 2
  (`voxcpm_lora_proj`, `enable_proj:true`, 74 MB) still has weights, so **arm D uses run 2**.
  Retraining run 1 is ~15 min if it is ever wanted back.
- **Reference-speaker gap: CLOSED.** The owner supplied a consented female recording; it is
  `eval/fixtures/voice_urdu_female.wav` (`--reference-id female`) and every runnable arm ran against
  both speakers. **It is deliberately not committed** — a real person's voice, and the standing rule
  is no voice data in git without explicit sign-off. It exists on the pod and on local disk only, so
  a pod loss *and* a local loss would require re-recording. **Its transcript is still missing**, and
  IndicF5 needs one (`ref_text`); `_load_indicf5` raises rather than silently Whisper-ing the
  reference, because that would change what is being measured without saying so.
- **Every female cell scores a higher speaker cosine than its owner counterpart, in all five arms**
  (0.689–0.798 vs 0.662–0.737). A uniform offset across unrelated models points at ECAPA or the
  recordings, **not** at model quality. Do not read it as "these models clone women better."
- **The Perso-Arabic→Arabic-phonology hypothesis is NOT confirmed.** Arm B's owner cell has the
  worst CER in the table (0.189), which fits — but arm B's *female* cell is 0.0385, among the best.
  A model applying Arabic phonology to Arabic script would do it regardless of the target voice.
  Still `[INFER]`; only listening settles it.
- **Arm C (Devanagari) has the lowest CER at both references — treat that as a warning, not a win.**
  It is equally consistent with Devanagari producing Hindi-accented speech that Whisper transcribes
  *more* confidently, which is exactly the outcome the owner already rejected by ear.
- **Ladder rung B is untestable on this corpus:** `normalize_urdu` is a measured no-op on 13/13
  items because they were authored with clean Urdu codepoints. A null there means "input was already
  clean", not "normalization does not help".

Last updated: **2026-08-15** (see the checkpoint above — LoRA withdrawn, OmniVoice shipped, code-switch
bug fixed, live-verified end-to-end on a real pod that's now being terminated). Prior to that: Qwen
analyzer backend + frontend wiring are both merged to `main` and
done. The VoxCPM2 LoRA POC's training checkpoint merged earlier (PR #11); the baseline-vs-LoRA eval
comparison that was missing at that point has since run for real and **also merged to `main`**
(PR #12), with a **mixed result** — CER improved sharply, speaker-identity cosine regressed on Urdu
and only marginally improved on Hindi — and the owner has done a first, informal listen to the four
clips (found the LoRA Urdu clip good, the rest okay). Nothing has been shipped or flagged
`verified` off the back of this; see "What landed this session" below for the full picture.

---

## Where things stand

**The product is complete and validated end-to-end on GPU with real cloned audio, including Speech
Direction's multi-segment generation.** Base branch is **`main`**, currently at commit `777a82a`.
Landed since the rewrite: async jobs/mobile/perf, Speech Direction (preview → multi-segment audio →
backend contract for edits → full Advanced per-segment editor UI), Phase 4 Chatterbox — designed,
built, gated, and **concluded not shippable** (see below), a Composer model picker, and client-side
audio extraction. Full detail and forward roadmap: **[docs/ROADMAP.md](ROADMAP.md)**.

| Area | Status |
|---|---|
| **Core rewrite (Waves 0/1/B1-B3/P6/P7)** | ✅ Done, stable. Not touched this session. |
| **Async jobs / mobile / perf** | ✅ Done, merged. |
| **Speech Direction (Phase 2)** | ✅ **Fully landed on `main`.** Heuristic analyzer + capability report + preview UI + multi-segment generation + client-edited per-segment override contract (`direction_plan` on `TTSGenerateRequest`, sparse/index-keyed, re-validated server-side, 422 on stale index) + the full Advanced per-segment IR editor UI (editable emotion/intensity/energy/rate/pause per segment). **Real-audio pod validation done 2026-08-12**: hit `POST /api/generate` with `apply_direction: true` against a live VoxCPM2 worker, downloaded the actual output, automated waveform check found zero click-threshold discontinuities and silence runs landing exactly at the expected segment boundaries — objectively sound, human listen still open. Clip at `eval/results/direction/pod_directed_hi.wav`. |
| **Qwen2.5-Instruct LLM analyzer** | ✅ **Production backend merged to `main` (2026-08-12)**. `QwenAnalyzerBackend`, `AnalyzerScheduler`, `JobKind.ANALYZE_LLM`, `POST /api/direction/analyze-llm`. Real pod-verified: direct backend, full worker-subprocess path, and the real HTTP path all passed clean on a fresh pod (0 problems, en/ur/hi) after a genuine bug (`load_time_sec` not threaded through) was found and fixed. 239 backend tests, ruff clean. **Frontend wiring not built** — no UI calls the endpoint yet. Known open risk: idle-unload timer is the only VRAM-contention mitigation vs. the audio scheduler, documented in `analyzer_scheduler.py`, not resolved. |
| **Phase 4 (Chatterbox)** | 🔴 **Designed, built, gated, and concluded NOT shippable.** Real `ChatterboxBackend`, real Phase-A gate run, real human listen. Owner's verdict: "not that good... identity is matched around 60%". Same failure shape as the Urdu investigation below — a speaker-encoder ceiling, not a tunable parameter. Not planned to be revisited without a LoRA fine-tune (see next row). |
| **VoxCPM2 LoRA POC** | 🚧 **Training (PR #11) and eval + human listen (PR #12) both merged to `main`.** Baseline-vs-LoRA comparison: CER improved sharply on Urdu (0.0818 → 0.0091) but speaker cosine regressed on Urdu (0.7226 → 0.6859, pass → fail) and only marginally improved on Hindi (0.6863 → 0.6986, still under gate) — a mixed result, not a clean win. The owner listened to all four clips informally and found the LoRA Urdu clip good, the rest okay — consistent with this project's established finding that the ECAPA speaker-cosine metric is out-of-distribution for this voice, not a contradiction of the numbers. **Not a rigorous (blind) listen, and no `LanguageSupport.verified` flag touched — merging the eval numbers is not a ship decision.** The actual `.safetensors` checkpoint weights are NOT in git (`.gitignore` excludes them project-wide) and exist only on the local Windows machine right now. The 36-clip training dataset (`eval/training/`) remains untracked/uncommitted, that consent decision is still open. Next step, still the owner's call: a rigorous blind listen, retrying with `enable_proj: true`, or deciding this is enough signal either way. Full detail: `docs/VOXCPM_LORA_POC.md`. |
| **Composer model picker** | ✅ Done, merged. Explicit model override + "(Recommended)" hint, no Tone control (confirmed no-op). |
| **Client-side audio extraction (Phase 3)** | ✅ Done, merged. |

**256 backend tests passing** as of the last full run this session (`feature/phase2-advanced-direction`
before its merge), ruff clean on everything touched. `gh` CLI is now installed locally (`winget install
GitHub.cli`), so PRs can be opened directly going forward instead of handed over as links.

## What landed this session (2026-08-12) — all three background agents resolved

All three pieces of parallel work from this session are now resolved — nothing is still running.

1. **Qwen2.5 LLM analyzer production backend — DONE, merged to `main` (commit `2be3759`).** Built
   `WireOp.CLASSIFY`, `QwenAnalyzerBackend` under `inference/runtimes/`, `AnalyzerScheduler` (a
   torch-free sibling to `InferenceScheduler`, not a `scheduler.py` edit), `JobKind.ANALYZE_LLM`,
   `POST /api/direction/analyze-llm`, an idle-unload timer. Pinned HF revision
   `aa8e72537993ba99e69dfaafa59ed015b17504d1`. Verified for real on a fresh pod: direct backend, full
   worker-subprocess path, and the real HTTP path all passed with 0 problems across en/ur/hi. Found and
   fixed a real bug (`load_time_sec` wasn't threaded from LOAD into the following classify call) during
   verification. 239 backend tests passing (reverified independently after merge), ruff clean.
   **Open risk, not resolved**: the idle-unload timer is the only VRAM mitigation between this
   scheduler and the audio one; they don't share a real budget.
2. **Qwen analyzer frontend wiring — DONE, merged to `main` (commit `1ae4e9f`).** An "AI suggest"
   button inside the existing Advanced per-segment editor calls the endpoint above, polls the job, and
   feeds the LLM's emotion/intensity/energy/rate classifications into the *same* `directionEdits` state
   a manual edit already uses (suggest-then-edit, not silent auto-apply) — segment text and
   `pause_after_ms` still come from the existing heuristic segment at that index, never from the LLM.
   The agent that built this stalled (a stream watchdog, not a real failure) right before committing —
   the work itself was complete and correct in its worktree; reviewed every diff by hand, verified the
   build, then committed/merged it manually. Frontend build green, backend untouched (239 still
   passing). Not click-tested against a live pod-backed backend (none was available) — TypeScript
   correctness and a mocked-response check were the extent of verification, noted explicitly as a gap.
3. **VoxCPM2 LoRA POC — training merged to `main` via PR #11; eval + human listen also merged via
   PR #12.** Training succeeded with real numbers (300 steps, ~15 min wall clock,
   ~2.3-2.4s/step steady-state, no OOM, `loss/stop` converged cleanly 0.039 → ~0.0001). The trained
   checkpoint was rescued off the pod before a scheduled shutdown — config/state committed at
   `eval/results/voxcpm_lora/checkpoint_backup/`, but the actual `.safetensors` weights are **not** in
   git (`.gitignore` excludes them project-wide, same as every other model's weights) and exist only
   on the local Windows machine right now. **The baseline-vs-LoRA eval comparison ran on a later pod
   (2026-08-13)**: CER improved sharply on the trained language (Urdu 0.0818 → 0.0091) but
   speaker-identity cosine — the actual thing this POC exists to move — regressed on Urdu
   (0.7226 → 0.6859, pass → fail) and only marginally improved on Hindi (0.6863 → 0.6986, still under
   gate). **The owner then listened to all four clips informally** and found the LoRA Urdu clip good,
   the rest okay — not a blind test, but consistent with this project's established finding
   (`docs/URDU_CLONING_REPORT.md`) that the ECAPA cosine metric is out-of-distribution for this voice,
   so a favorable human verdict on the one cell where cosine regressed is not a contradiction. **Still
   not shipped, no `LanguageSupport.verified` flag touched** — a rigorous blind listen is the
   recommended next step before any ship/no-ship call. Full detail: `docs/VOXCPM_LORA_POC.md`.

## Resuming on a new pod

The repo is **public for read** — the pod clones anonymously, no token needed. `main` now carries
everything (Speech Direction, the plain-language UI pass, and the Phase 4 IR taxonomy), so the
bootstrap's default branch is correct — no `BRANCH=...` override needed:

```bash
ssh -p <PORT> root@<HOST> "bash -s" < scripts/pod-bootstrap.sh
```

`GH_USER`/`GH_TOKEN` are only needed for pushing commits *from* the pod, not for this clone — see
[POD_SETUP.md](POD_SETUP.md) for the rare anonymous-clone-rejected case.

Rebuilds caches, both venvs (API without torch, runtime **with torch pinned to cu128** — the default
cu130 wheel silently reports `cuda.is_available() == False`), and re-downloads the ~7 GB of weights if
`/workspace` did not carry over. Full runbook: **[POD_SETUP.md](POD_SETUP.md)**.

Note your enrolled voices and history live in `VCS_DATA_DIR` on the pod — they are lost with the
volume, not with the pod.

Connection details for the current pod are in `.claude/remote.local.md` (gitignored — endpoints
change). Note SSH must use Windows `ssh.exe`; Git Bash cannot see the ssh-agent holding the key.

## 🔴 Urdu voice cloning — investigation concluded

Full report: `docs/URDU_CLONING_REPORT.md`. All runs: `docs/PHASE_A_RESULTS.md`. Verdict from the
owner (native Urdu speaker) listening to real output:

**No permissively-licensed zero-shot model clones the owner's voice.** F5, VoxCPM2, and Chatterbox
all produce intelligible Urdu in a *generic* voice. The reason is the finding that matters most:

> **Intelligibility and speaker-identity are independent failures.** Intelligibility was solved
> (Perso-Arabic → Devanagari input fixed CER 0.96 → 0.07). Identity comes from the reference *audio*
> encoder, **not the text** — so transliteration is NOT the voice bottleneck, and no amount of text
> or knob tuning fixes it. Root cause: out-of-distribution speaker encoding (encoders are
> English-trained; a 7 s Pakistani-Urdu voice is off-distribution).

**Path forward:** ship the VoxCPM2 intelligibility pipeline as "a natural Urdu voice" (honest, works,
Apache-2.0); for real cloning, **LoRA fine-tune VoxCPM2** on 2–10 min of the owner's audio. Do NOT
try more zero-shot models or samplers — the ceiling is the encoder.

**Closed — do not re-investigate:** F5 vocab (Devanagari, 0 Arabic chars), EMA, nuqta-folding,
zero-shot knobs, transliteration-as-voice-cause. All ruled out with evidence in the report.

Ranked best→worst by ear: `out_voxcpm_urdu_deva.wav` > `out_chatterbox_standard.wav` >
`out_chatterbox_maxref.wav`. All in `C:\Users\abdus\Downloads\Voices\`.

### Reusable durable assets (now in git, not on a pod)

- `eval/eval_harness.py` — Whisper large-v3 CER + ECAPA-TDNN cosine + RTF. **The gate is a SCREEN,
  not a verdict** (VoxCPM2 passed CER, nearly passed cosine, still sounded like a stranger). Needs a
  torch venv: `uv pip install torch torchaudio transformers speechbrain jiwer soundfile`.
- `eval/fixtures/voice_urdu.wav` — the owner's reference (6.67 s), with transcript + Devanagari
  transliteration + standard target sentence in `eval/fixtures/README.md`.

### Lessons carried forward

**1. Verification means execution.** R2 produced a report with three ❌ in its own summary table and
concluded "READY TO SHIP: All critical deliverables verified" — having never loaded the model.
Treat any research result claiming verification without a command transcript as unverified. The
plan's top-listed bad idea is Wave 3 implementing against documentation instead of verified
snippets, and this is exactly how that happens.

**2. `df` DOES NOT SHOW THE VOLUME QUOTA. Use `du -sh /workspace`.**

R2 reported "disk quota exceeded". That was dismissed on the basis of `df -h /workspace` reporting
164 TB free — but `/workspace` is a MooseFS mount, and `df` reports the whole **cluster**, not this
volume's quota. The volume was 50 GB and actual usage was ~49.3 GB:

```
23.0 GB  uv-cache
 5.0 GB  hf-cache
 1.2 GB  pip-cache
20.1 GB  engines-lab venvs (r1 4.5 + r2 3.3 + r3 8.9 + r4 3.4)
-------
49.3 GB  vs a 50 GB volume
```

R2's diagnosis was correct and the dismissal was wrong. The volume has since been raised to 200 GB.

**Check capacity with `du -sh /workspace` against the volume size shown in the RunPod console.**
Never with `df`. A wrong reading here sends you debugging dependency resolution for an hour when the
actual failure is "out of space".

Budget note: the uv cache alone reached 23 GB across four runtimes. Four ML stacks plus weights fit
in 200 GB, but not comfortably in 50 GB — `uv cache prune` is worth running between waves.

## Measured pod facts (2026-08-04)

- RTX A5000, 24564 MiB, sm_86, driver 580.159.04 · Ubuntu 24.04.3 · Python 3.12.3 · torch 2.8.0+cu128
- `/` = 30 GB **ephemeral** overlay (4.6 GB/s) · `/workspace` = MooseFS network volume (526 MB/s)
- Network: **~7 MB/s from HuggingFace, ~16 MB/s from PyPI.** Wave 1's wall clock is weight
  downloads, not compute — budget accordingly.
- Present: git, ffmpeg, uv, flock. Missing: **node, npm** (so frontend work happens locally), nvcc,
  espeak-ng (not needed by any chosen runtime).

## What's left to build

**Current priorities live in [docs/ROADMAP.md](ROADMAP.md)**, not here. As of this checkpoint:

- The two in-flight background agents above — review, merge, verify once they report back.
- Once the Qwen analyzer backend lands: frontend wiring (a trigger in `DirectionPanel.tsx`/
  `Composer.tsx` to call `POST /api/direction/analyze-llm` and let the user apply its suggestions,
  same shape as the existing heuristic preview but async/job-polled). Not started, not designed yet.
- Once the LoRA POC reports back: either a go/no-go on real production integration (how a fine-tuned
  adapter would load into `VoxCPMBackend`), or — if it's a no-go — the Urdu product decision below
  becomes live again (ship generic-voice MVP as the final answer, or look at another path).
- **D1** — Docker, CI. Dockerfile rewritten CPU-slim but still **not build-tested**.
- PR housekeeping: `main` currently has no open PRs; `gh` CLI is installed locally now, so future
  branches can get a real PR instead of a handed-over compare link.

## Non-negotiables (full detail in CLAUDE.md and docs/ARCHITECTURE.md)

1. `import torch` must not be reachable from `app.main`. Enforced by
   `test_no_torch_outside_runtimes`. Check by hand with **leading whitespace allowed** — the legacy
   engines import torch inside functions, so an anchored grep reports them clean while the invariant
   is broken.
2. Eviction only inside `_ensure_ready()`, only while holding the GPU-slot semaphore.
3. Routing is pure — no `is_loaded`, ever. That is what made a cold server answer with a sine wave.
4. No silent fallback. `NoRouteError` → 422 listing what would work.
5. Permissive licenses only.
6. Nothing routes until Phase A verifies it. `LanguageSupport.verified=False` is the default and the
   catalog currently resolves nothing — deliberately.

## Design facts established by research (in the catalog / code already)

1. **Two F5 loader paths, not one class.** IndicF5 → `AutoModel(trust_remote_code=True)`;
   OpenBible-Urdu → raw checkpoint via stock `f5-tts` loader.
2. **`f5_openf5_en` dropped** — no permissive English F5 exists (all derive from CC-BY-NC SWivid).
   English routes to Chatterbox.
3. **F5 reference limit ~12 s, silent truncation** (not ~6 s; "8192" is an unrelated rotary table).
4. **F5 blank `ref_text` silently loads Whisper** — always pass `ref_text`.
5. **VRAM must be sampled concurrently** — post-hoc readings under-report peak ~5×. Scheduler sizes
   from recorded `vram_mb`, not live readings.
6. **~~Roman Urdu → Devanagari via `ai4bharat-transliteration`~~ — dead, do not reintroduce.**
   VoxCPM2 renders romanized Hindi/Urdu directly (tokenizer-free, owner-verified by ear); the whole
   transliteration subsystem was deleted. `ai4bharat` was also a py3.12 dependency nightmare
   (fairseq → tensorflow_addons → keras3 breakage, 9.8GB venv).
7. **VoxCPM2 warm-up trap** — built-in warm-up skips the cloning path; first real clone eats +40–55 s
   unless warmed with a real reference.

## Next session — start here

1. **Nothing is in flight.** Qwen analyzer backend + frontend wiring are merged to `main`. The LoRA
   POC's training (PR #11) and its real eval numbers + a first informal human listen (PR #12) are
   both merged to `main` — see the LoRA POC row above and `docs/VOXCPM_LORA_POC.md` for the full
   picture before deciding what's next.
2. Open decisions for the owner, not yet made: whether a rigorous blind listen is worth doing before
   any ship/no-ship call on LoRA, and whether to pursue another LoRA config (`enable_proj: true`) if
   the identity regression turns out to be real rather than a metric artifact. None of these should
   be decided unilaterally — see "What's left to build" above.
3. **New operational lesson, read before spawning any subagent that needs its own git branch:**
   **always pass `isolation: "worktree"`.** This session forgot it once (the LoRA POC agent) and it
   checked out a branch directly in the shared `D:\Projects\AI-Voice-Clone\` working tree, clobbering
   an in-progress edit mid-session. No exceptions for "this one's quick."
4. **Second lesson: `origin` in this local repo is not `fork`.** `origin` →
   `IftikharAhmedDev/AI-Voice-Clone.git` (an unrelated, stale predecessor fork — no rewrite, no
   `docs/ROADMAP.md`, contains code this project's own `CLAUDE.md` says was deleted, e.g. "Style
   Exaggeration"). `fork` → `MunawarAliAraiz/AI-Voice-Clone.git`, the real one. Always fetch/push/branch
   off `fork`, never `origin`, in this repo specifically. Two different subagents hit this same trap
   independently this session (the frontend Advanced-editor agent, and the LoRA POC agent) — it is not
   a one-off, it's this repo's actual remote configuration. Tell every future subagent this explicitly
   rather than assuming they'll discover it themselves.

**Token discipline (owner priority):** terse replies, no recaps, no exploratory pod runs without
go-ahead, batch verification. Build inline when holding the contracts. Use subagents for genuinely
parallel/independent work (this session ran two GPU-pod agents concurrently, plus a frontend UI agent
earlier) — but isolate every one of them in a worktree, and give each one the correct `fork` remote
explicitly rather than assuming it'll figure out which remote is real.

## Open items

- [ ] **Rotate the GitHub PAT** (`ghp_...`) — pasted into the transcript, and written to two pods'
      `/root/.git-credentials`. Permanently logged.
- [ ] **Urdu product decision (owner):** in progress, not resolved — the LoRA-fine-tune path is now
      being probed (see "In flight right now"), not just proposed. See `docs/URDU_CLONING_REPORT.md` §4.
- [ ] Accept the IndicF5 HF license + set `HF_TOKEN` on the pod (unblocks `f5_indic`).
- [ ] Empty `LEGACY_TORCH_IMPORTERS` once the old engine layer is deleted (blocked on B1/B2/B3
      landing replacements — deleting it now would break the running app).
- [ ] `NOTICE` file with CC-BY-SA attribution for OpenBible-Urdu.

Resolved since last check (2026-08-09): `SettingsPage.tsx` no longer exists (the desktop shell was
dropped project-wide) and `main.py` has no `tauri://` CORS origins — both stale items removed from
this list. `frontend/src-tauri/` itself (leftover generated files, no Tauri config anywhere) was
removed from git and gitignored the same day.
