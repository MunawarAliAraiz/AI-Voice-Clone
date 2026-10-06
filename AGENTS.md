# Project guide

AI Voice Clone Studio is a React UI backed by FastAPI. The Windows desktop
edition keeps user data locally. Its default generation adapter targets guarded
Runpod Serverless; production admission remains held pending real qualification. Read the documents below before changing behavior.

## Golden rules

- Never return fabricated audio as a successful generation.
- Keep `torch` out of the local API process; GPU runtimes stay isolated.
- Resolve a generation's model once when enqueuing; never silently reroute it.
- Keep voice references, history, and completed output on the user's PC. The
  Pod's persistent volume holds weights and caches, not the local database.
- Pin model repositories and revisions. Check code and weight licenses
  separately; noncommercial models require an explicit personal-use choice.
- No Runpod or Hugging Face secrets in source, frontend storage, installers,
  logs, or generated audio metadata.
- A decodable audio file is not a quality gate. Listen to representative
  English and Urdu outputs before declaring a model ready.

## Where things live

- `frontend/src/`: studio UI and HTTP client.
- `frontend/src-tauri/`: Windows desktop shell and sidecar lifecycle.
- `backend/app/api/`: local FastAPI routes and schemas.
- `backend/app/jobs/`: durable local job queue.
- `backend/app/inference/`: model catalog, routing execution, and GPU workers.
- `backend/app/remote_worker/`: Pod-side inference service.
- `backend/app/runpod/`: Runpod API, cost estimates, and Windows key storage.
- `backend/app/mcp/`: local MCP server for Codex and Claude.

## Documents

- [Native transport checkpoint](docs/NATIVE_TRANSPORT.md): connected source adapter, once-only recovery and remaining live gates.

- [Native Serverless admission](docs/NATIVE_SERVERLESS_ADMISSION.md): Runpod-only default, cumulative reservations and unresolved live shutdown/startup controls.
- [Account analytics](docs/ACCOUNT_ANALYTICS.md): account-wide historical charges, storage billing and live read-only verification.
- [Customer connections](docs/CONNECTIONS.md): required Runpod, optional Apify/Cloudflare, encrypted keys and honest feature gating.
- [Cloud recovery status](docs/CLOUD_RECOVERY_STATUS.md): prepared replacement, current test evidence and remaining access/qualification.
- [Cloudflare spending guard](docs/SPENDING_GUARD_CORE.md): durable alarms, owned cleanup, disabled admission and deployment requirements.
- [Serverless protocol](docs/FLEX_PROTOCOL.md): bounded envelopes, once-only submission and result verification.
- [Serverless worker](docs/FLEX_WORKER.md): pinned SDK entrypoint, image preparation and hosted build gates.
- [Downloads without a setup rental](docs/VOLUME_SETUP_WITHOUT_POD.md): separate encrypted S3 connection, transfer requirements and limitations.
- [Cloud spending protection proposal](docs/CLOUD_SPENDING_PROTECTION.md): offline design and qualification gates; no further paid tests.
- [Runpod budget incident and release hold](docs/RUNPOD_BUDGET_INCIDENT.md): provider timers are not enforced; no new paid Pod starts or tests.
- [Reliability and performance plan](docs/PERFORMANCE_PLAN.md): current cache regression, truthful job stages, model tests, measured baseline and $2 experiment ceiling.
- [Architecture](docs/ARCHITECTURE.md): current design and invariants.
- [Workspace design](docs/UI_DESIGN.md): layout, tokens, accessibility and visual checks.
- [Liquid orb](docs/ORB.md): real status mapping, motion limits and static fallback.
- [App icon](docs/ICON.md): original mark, reproducible favicon/ICO and size checks.
- [Queue progress](docs/QUEUE_PROGRESS.md): active work, real stages, status recovery and measured feed cost.
- [Model tests](docs/MODEL_TESTS.md): short queued samples, reference voice defaults and the distinction between file checks, residency and past results.
- [Desktop architecture](docs/DESKTOP_ARCHITECTURE.md): local/Pod split and rationale.
- [Windows desktop build](docs/DESKTOP.md): packaging, installer and native test evidence.
- [Desktop updates](docs/DESKTOP_UPDATES.md): signed update flow, local feed preparation and publication gates.
- `scripts/test-desktop-sidecars.py`: signed package and isolated frozen API/MCP checks; no desktop window, installer, normal profile or provider starts.
- `scripts/test-installer-mcp-lock.py`: disposable production NSIS hook/frozen MCP lock regression; no app installation or registry/profile changes.
- [FFmpeg redistribution](docs/FFMPEG_REDISTRIBUTION.md): exact runtime/source evidence and remaining source requirements.
- [Model storage](docs/MODEL_STORAGE.md): complete pinned graphs, checksums and measured disk use.
- [Cloud image release](docs/POD_IMAGE_RELEASE.md): hosted builds and immutable image qualification.
- [Cloud lifecycle](docs/CLOUD_LIFECYCLE.md): automatic storage verification, GPU selection and compute release plan.
- [Guided setup](docs/GUIDED_SETUP.md): confirmed setup pages, automatic defaults, storage sizing and app-exit behavior.
- [Runpod setup](docs/RUNPOD_SETUP.md): current blocker and user-operated storage/model/generation setup steps.
- [CPU setup investigation](docs/CPU_SETUP_INVESTIGATION.md): CPU deployment request correction and read-only provider evidence.
- [Worker failure investigation](docs/WORKER_FAILURE_INVESTIGATION.md): safe generation/helper errors, loader correction and live-test limits.
- [Selected cache investigation](docs/POD_CACHE_INVESTIGATION.md): preserve approved volume paths between CPU setup and GPU generation.
- [Pod worker](docs/POD_WORKER.md): worker contract and deployment gaps.
- [MCP](docs/MCP.md): local tool surface and host configuration.
- [Convert](docs/CONVERT.md): pasted scripts, public YouTube captions and explicit Urdu translation drafts.
- [Speech pipeline](docs/SPEECH_PIPELINE.md): reviewed plans and audio assembly.
- [Speech models](docs/SPEECH_MODELS.md): conversion adapter, separation research and qualification gaps.
- [Handoff](docs/HANDOFF.md): current state and the next verification step.
- [Roadmap](docs/ROADMAP.md): completed and planned work.

Update the handoff at every meaningful checkpoint. Keep project state in the
repository, rather than relying on chat history.
