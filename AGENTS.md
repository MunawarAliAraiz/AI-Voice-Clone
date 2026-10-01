# Project guide

AI Voice Clone Studio is a React UI backed by FastAPI. The Windows desktop
edition keeps user data locally and sends GPU inference to a user-owned Runpod
Pod. Read the documents below before changing behavior.

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

- [Architecture](docs/ARCHITECTURE.md): current design and invariants.
- [Desktop architecture](docs/DESKTOP_ARCHITECTURE.md): local/Pod split and rationale.
- [Windows desktop build](docs/DESKTOP.md): packaging, installer and native test evidence.
- [Model storage](docs/MODEL_STORAGE.md): complete pinned graphs, checksums and measured disk use.
- [Cloud image release](docs/POD_IMAGE_RELEASE.md): hosted builds and immutable image qualification.
- [Cloud lifecycle](docs/CLOUD_LIFECYCLE.md): automatic storage verification, GPU selection and compute release plan.
- [Pod worker](docs/POD_WORKER.md): worker contract and deployment gaps.
- [MCP](docs/MCP.md): local tool surface and host configuration.
- [Speech pipeline](docs/SPEECH_PIPELINE.md): reviewed plans and audio assembly.
- [Speech models](docs/SPEECH_MODELS.md): conversion adapter, separation research and qualification gaps.
- [Handoff](docs/HANDOFF.md): current state and the next verification step.
- [Roadmap](docs/ROADMAP.md): completed and planned work.

Update the handoff at every meaningful checkpoint. Keep project state in the
repository, rather than relying on chat history.
