# Desktop and Runpod architecture

## Product decisions

The Windows app keeps reference voices, the SQLite database, edit projects, and
finished audio under the user's AppData directory. A user-owned Runpod Pod keeps
only model weights, runtime environments, and temporary inference data on its
network volume. The app must remain useful for local editing when the Pod is
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

The existing capacity check reserves 16,000 MiB for audio, 19,500 MiB for
Gemma, 6,000 MiB for Qwen, and 2,048 MiB headroom: **43,548 MiB total** when
all three features are configured. This makes a 48 GB GPU the supported
starting point for full feature parity. A 24 GB Pod may run audio generation
but the existing code deliberately disables Gemma script conversion there.
Any lower full-feature requirement needs an explicit sequential residency
design and real GPU measurements, not a changed label in the picker.

Offer a 200 GB network volume by default. A 150 GB minimum is provisional:
the previous Pod occupied roughly 76 GB before the complete new model set;
each pinned snapshot, environment, and scratch budget must be measured before
the all-model install is advertised as fitting. Storage remains billable while
the Pod is stopped. Show live Runpod GPU rates and volume rate separately.
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

## Checkpoint 2026-09-29

Implemented in source: local static frontend, session-key API, Tauri shell,
packaging script, stdio MCP adapter, authenticated Pod TTS worker, remote
scheduler, pinned catalog model-install endpoint, Runpod v2 client, DPAPI key
store, GPU compute estimates, and read-only Pod/volume analytics UI. The
provisioning methods are not exposed because no tested worker image digest is
published. These have not yet produced an installer or generated real audio
through Runpod. Dynamic Pod pairing, deployment, scripted dialogue, and
recorded-audio conversion remain open.
