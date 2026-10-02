# Desktop and Runpod architecture

## Simple setup and progress state

The Runpod UI presents storage, model preparation and voice generation as
separate steps. Native collapsed disclosures hold technical controls; visible
cost summaries and explicit purchase/spending approval remain outside them.
GPU selection is automatic within the stored region, capacity and price limits.
Voice model overrides are collapsed without widening license or experimental
routing eligibility. Recommended language defaults are unchanged.

The controller persists model setup phase/error and distinguishes an actual
provider Pod ID from a merely recorded start attempt. UI progress uses measured
bytes and never animates an unknown percentage or a failed task. On restart,
orphaned active phases become interrupted errors. Model evidence is retained
before cleanup, but generation stays unavailable until installer termination
is confirmed. Reconciled ownership is persisted before requesting deletion.
Only proven pre-execution GraphQL rejection may discard a creation record;
ambiguous creation still requires reconciliation or expiry.

## Agent, update and first-launch boundaries

The local MCP executable discovers the current DPAPI session descriptor and
uses the same authenticated API/job queue as the desktop. Client configuration
is an explicit user action, with backup and atomic replacement; discovery does
not establish a live client handshake. Shared queue polling lets the desktop
observe agent jobs and issue in-app/native notifications.

Native updater commands accept no user-provided URL, signature or key. Only
the current exact loopback origin/main window receives those command grants.
The fixed HTTPS feed points to an installer signed by the embedded Ed25519
public key and requires a matching signed version. Before restart, a shared
mutation lock checks pending jobs/cloud lifecycle work and fences new mutations.
Installer launch failure restores the owned API and admission.

FFmpeg is excluded from the installer. A CPU-only first-launch controller
downloads a pinned publisher archive, checks archive/file hashes and adopts
only verified cached files. Audio imports/processing and generation remain
blocked until this local runtime is ready. Pod model setup is separate.

Generation availability derives from cloud/model/policy and audio-tool state,
not local API health. Disabled controls provide accessible explanations and
setup links; backend admission enforces the same restrictions. Contracts and
runbooks: [MCP](MCP.md), [updates](DESKTOP_UPDATES.md), [Convert](CONVERT.md)
and [audio-tool delivery](FFMPEG_REDISTRIBUTION.md).

## Product decisions

The Windows app keeps reference voices, the SQLite database, edit projects, and
finished audio under the user's AppData directory. A user-owned Runpod Pod keeps
only model weights and runtime caches on its network volume. Uploaded audio
and temporary inference data use ephemeral Pod storage and are cleaned per job.
The app must remain useful for local editing when the Pod is
stopped. The web build continues to work during migration.

The shell is Tauri 2. It launches a loopback-only Python FastAPI sidecar on a
free port with a new session key, waits for health, then opens the same-origin
React UI. The sidecar serves the built frontend and owns the local database.
The GPU Pod will expose a separate, versioned inference service. The local job
queue will call it through an adapter; model routing remains local and is
decided once at enqueue. Do not make the Pod service own user history.

## Remote protocol and security

The remote service must authenticate every request and report its protocol
version and installed model revisions. A synth request sends the already
resolved model ID, text, settings, and the required reference clip; its response
returns real audio and measured time. It must never choose a different model.
Remote inference errors become failed local jobs with stable problem codes.
The Runpod management key is encrypted with Windows DPAPI into the current
user's AppData directory; the Pod gets a different inference token. Neither
belongs in the installer or frontend storage. Pod-side temporary audio must be
cleaned after each job.

## Capacity and costs

### User decision: automatic compute lifecycle (2026-09-30)

Desktop users enter only their Runpod management key. Local API authentication
is automatic; the web-only API-key settings control is hidden in desktop mode.
Manual Pod IDs, worker tokens and GPU selection are implementation tools, not
the intended normal desktop setup flow.

After connecting, discover network volumes. Ask the user to select an eligible
existing volume or approve creation with the quoted ongoing storage charge.
Verify the app's complete versioned model manifest, including pinned revisions,
required files and checksums; the mere existence of a volume is not readiness.
An existing verified installation should skip downloading. Otherwise download
missing files to persistent storage and show actual file/byte progress. Keep
model-dependent generation/conversion unavailable until its required manifest
is verified. Local projects, references and outputs remain on the PC.

Automatically select the least expensive currently available, compatible GPU
that meets the qualified memory requirement in the volume's region. Refresh
availability/rates before provisioning; retry bounded compatible alternatives
without silently increasing an approved cost limit. GPU choice and speech model
choice are different: the user may choose voices/models, while compute selection
is automatic. Startup, model loading, generation and upload/download may all
contribute to billable worker lifetime. Release compute only after the complete
queued batch succeeds/fails and actual output is safely saved locally. Preserve
the network volume. App shutdown or disconnection cannot be the sole shutdown
protection; the provider must enforce maximum runtime.

The preferred design to qualify is Serverless flex (zero active workers), with
FlashBoot and a short idle window for a dialogue batch. This is a proposed
adapter, not an implemented endpoint. Ephemeral GPU Pods with automatic
termination are a fallback. Neither preserves guaranteed GPU residency at zero
idle compute cost. Persistent weights avoid repeated downloads but still need
to be loaded into VRAM on a fresh worker. Download/verification should use a
temporary CPU worker if available, not reserve an idle GPU throughout setup.
See [CLOUD_LIFECYCLE.md](CLOUD_LIFECYCLE.md) for the API research and constraints.

The 48 GB requirement below belongs to the current all-feature residency design;
task-specific sequential loading may reduce it, but requires a scheduler change
and measured qualification. It must not be advertised by lowering the UI label.

The existing capacity check reserves 16,000 MiB for audio, 19,500 MiB for
Gemma, 6,000 MiB for Qwen, and 2,048 MiB headroom: **43,548 MiB total** when
all three features are configured. This makes a 48 GB GPU the supported
starting point for full feature parity. A 24 GB Pod may run audio generation
but the existing code deliberately disables Gemma script conversion there.
Any lower full-feature requirement needs an explicit sequential residency
design and real GPU measurements, not a changed label in the picker.

Offer a 200 GB Standard network volume. The current pinned download graph is
49,014,734,674 bytes, with scratch/cache headroom checked by the installer.
See [MODEL_STORAGE.md](MODEL_STORAGE.md) for the measured graph and verification
contract. Actual Pod runtime disk and VRAM use still require qualification.
Storage remains billable after compute is terminated. Show live Runpod GPU
rates and volume rate separately.
Per-generation cost is an estimate from measured active time times the chosen
GPU's hourly rate; actual Pod/volume billing is shown separately because idle
time cannot honestly be assigned to one generation.

## Planned feature flow

Scripted dialogue has editable lines, each with a speaker/voice and language.
Each line is a durable job and can be retried independently; a final assembly
uses successful clips in script order. Recorded-audio voice conversion has an
analysis pass, a user-reviewed speaker-to-voice mapping, bounded conversion
chunks, and resumable assembly. One-hour recordings and up to three simultaneous
speakers require listening tests in Urdu and English before release. The
diarization, separation, and conversion models need their own dependency
environments and pinned, license-checked weights.

## Current checkpoint 2026-10-01

The Windows installer is built and local native integration is being verified
against the final bundle. Runpod setup now uses one management key, account
credit/storage discovery, a reviewed storage purchase quote, CPU model
download/verification progress, and automatic regional GPU selection for
bounded disposable Pod sessions. Generation admission requires verified model
storage and an approved compute policy. Provider-side termination is included
atomically in creation; completed output remains local and model storage remains
on the network volume. See [CLOUD_LIFECYCLE.md](CLOUD_LIFECYCLE.md).

No worker image has been published and no new live generation is qualified.
Public source publication approval is pending, and paid operations require
reviewed immutable worker image references. Serverless comparison and automatic
recorded-audio conversion remain open. Dialogue is Beta and its generation tests
are deferred. [HANDOFF.md](HANDOFF.md) is the current execution checkpoint;
the dated sections below describe earlier states.

## Historical checkpoint 2026-09-29

Implemented in source: local static frontend, session-key API, Tauri shell,
packaging script, stdio MCP adapter, authenticated Pod TTS worker, remote
scheduler, pinned catalog model-install endpoint, Runpod v2 client, DPAPI key
store, GPU compute estimates, and read-only Pod/volume analytics UI. The
provisioning methods are not exposed because no tested worker image digest is
published. These have not yet produced an installer or generated real audio
through Runpod. Dynamic Pod pairing, deployment, scripted dialogue, and
recorded-audio conversion remain open.

## Historical checkpoint 2026-09-30

Dynamic DPAPI Pod pairing and model download controls are implemented.
Speech Direction and Gemma conversion now have authenticated remote endpoints
and desktop adapters with revision checks. Scripted dialogue uses individually
durable generation jobs, saved local drafts, and an assembly manifest containing
clip hashes, original routes and editable spans. Recorded-audio plans and real
WAV mixing validate the one-hour/three-overlap limits; automatic separation and
GPU voice conversion are still qualification/integration work.

MCP discovers the current desktop session through a DPAPI descriptor. Its
standalone executable passed stdio/API bridge tests. The Pod image recipe and
isolated hashed dependencies exist, but the image has not been built/published.
The live Runpod account was checked read-only: no Pods, one existing 50 GB video
volume (not used for this app). No paid resource was created.
