# Persistent model storage

## Implemented boundary â€” 2026-09-30

`app.remote_worker.installer_main:create_installer_app` is a CPU-only FastAPI
service. Its image does not install Torch. It requires `POD_WORKER_TOKEN` and
an actual mounted `VCS_MODEL_VOLUME_ROOT` (default `/workspace`), with
`HF_HUB_CACHE` inside that mount. An explicitly injected volume root permits
small isolated filesystem tests. It never calls Hugging Face login or writes
access credentials. Optional `HF_TOKEN` is process-only and is not forwarded
to download CDN hosts.

Authenticated routes:

- `GET /v1/health`: protocol 1, role `model_installer`.
- `GET /v1/models`: catalog/helper statuses, no network or weight loads.
- `GET /v1/setup`: aggregate readiness and progress.
- `POST /v1/setup`: verify/adopt/download the supported required model graph.
- `GET|POST /v1/models/{id}/install`: status/start for one registered model.

`ModelInstaller.start()` discovers the pinned Hub file tree, checks every
existing file against provider size/checksum, adopts matching snapshots,
resumes `.vcs-incomplete` bytes using HTTP Range, validates the completed
checksum and atomically publishes files. SHA-256 validates LFS weights; Git
blob SHA-1 validates small repository files. Raw SHA-256 is also recorded for
every verified file. Bad checksums cannot become installed. A historical marker
or an existing directory alone never proves readiness; each new process must
explicitly start verification. Serialize installs to avoid races between aliases.

Progress is based on actual streamed/adopted bytes, not timers. Fields are
`state`, `revision`, `bytes_total`, `bytes_completed`, `files_total`,
`files_verified`, `current_file`, `progress_pct`, `detail`, and (after completion)
`evidence`. States: `not_started`, `discovering`, `verifying`, `downloading`,
`installed`, `failed`, `unsupported`. Unknown totals/percent are null. Aggregate
setup returns `ready`, `manifest_id`, `required_model_ids`, `models`, and
aggregate byte progress. Evidence version is 2; each file contains repo,
revision, path, size_bytes, and raw SHA-256. `release_manifest_id()` hashes
manifest version and the supported required pinned graph.

## Required graph

Required IDs are `voxcpm2`, `chatterbox_ml_v3`, `omnivoice_urdu`,
`qwen2.5-3b-instruct-analyzer` and `gemma-4-31b-it-transliterator` (helper constants remain in
`model_pins.py`). VoxCPM Arabic is an alias of the same base snapshot, not
another download. F5 is explicitly unsupported because this image has no F5
runtime; downloading its weights cannot make that runtime exist.

OmniVoice 0.2.1 package source was inspected from its published wheel. Its
`from_pretrained` resolves an unqualified repo before forwarding arguments;
the caller must resolve the catalog commit to a local snapshot first. The pinned
OmniVoice snapshot already embeds its complete `audio_tokenizer` folder, so
external `eustlb/higgs-audio-v2-tokenizer` is not required. Its optional ASR
uses `openai/whisper-large-v3-turbo`, now pinned to
`41f01f3fe87f28c78e2fbf8b568835947dd65ed9` and included in the required graph.
ASR must be enabled when a reference transcript is omitted. Weight licensing
remains separate: OmniVoice is personal-use only as shown in the existing UI.

Verified snapshot refs are written to the persistent Hub cache. The generation
container must enforce `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` after
storage verification, and pass pinned local paths to runtimes. Otherwise an
embedded package using `main` can still make network requests. Root worker
integration owns these settings and its readiness preflight.

## Metadata-only capacity measurement

Public commit trees were queried on 2026-09-30, without downloading weights.
This implementation conservatively downloads the complete pinned repositories,
including alternate formats, instead of risking a hidden dependency omission.

| Repository | Files | Bytes |
| --- | ---: | ---: |
| Qwen/Qwen2.5-3B-Instruct | 12 | 6,183,464,935 |
| ResembleAI/chatterbox | 18 | 13,866,212,931 |
| k2-fsa/OmniVoice | 13 | 3,267,470,260 |
| openai/whisper-large-v3-turbo | 13 | 1,622,466,054 |
| openbmb/VoxCPM2 | 9 | 4,960,731,703 |
| unsloth/gemma-4-31B-it-unsloth-bnb-4bit | 13 | 19,114,388,791 |

Total: **49,014,734,674 bytes**, about **49.01 GB / 45.65 GiB**, before partial
files, framework caches and future revisions. 200 GB is ample headroom for this
graph; it is not a measured minimum. The downloader keeps a 2 GiB free-space
reserve and checks missing bytes before transfer. Public anonymous metadata
access succeeded for all six repositories at this checkpoint. Metadata access
is not a full weight download or runtime qualification.

Sources: [OmniVoice package](https://pypi.org/project/omnivoice/0.2.1/),
[OmniVoice implementation](https://github.com/k2-fsa/OmniVoice/blob/master/omnivoice/models/omnivoice.py),
[Whisper pinned model](https://huggingface.co/openai/whisper-large-v3-turbo/tree/41f01f3fe87f28c78e2fbf8b568835947dd65ed9).

## Verification and remaining qualification

22 installer tests now pass; two symlink creation tests skip on the current
Windows profile because it lacks symlink privileges. All 35 architecture
contract tests pass, including no-Torch imports. Coverage includes verified
Arabic alias reuse, retry progress reset, low storage, cancelled writer joining,
adoption, corrupt-file repair,
checksum failure, range resumption, ignored/malformed ranges, private-token
redirect isolation, Git blob checksums, path validation, Omni's full graph,
old-marker rejection and authenticated CPU service. These are tiny injected
files, not real model generations. CPU image build/mounted-container smoke,
real volume transfer and GPU English/Urdu generations remain separate gates.
Full hashing on a new worker reads the graph and contributes startup latency;
measure that alongside model loading before advertising first-request times.

The Arabic VoxCPM alias reuses verified base evidence without repeating a
transfer or hash pass. Cancellation retains the serial snapshot lock until
the worker thread stops; interrupted partial files remain resumable. Metadata
and refs publish through random temporary files to avoid following an existing
temporary-file symlink. Full re-verification currently reads the pinned graph
on each new process; an authenticated fast path is not implemented.
