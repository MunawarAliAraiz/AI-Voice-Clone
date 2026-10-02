# Persistent model storage

## Setup wizard capacity contract — 2026-10-02

New Standard volumes default to **60 GB**: the current pinned graph contains
49,014,734,674 bytes, plus a **10,000,000,000-byte reserve**, rounded up to
whole decimal GB. The quote binds this measurement to the exact release manifest;
changed pins require updated capacity evidence before a purchase. Advanced choices
accept larger sizes up to the documented API bound, a custom name and an available
region. The price is an estimate: $0.07/GB/month for the first 1,000 GB and
$0.05/GB/month beyond that. Existing 200 GB volumes are retained; no automatic
shrink, deletion or migration occurs.

The volume list reports allocated size and location, **not free space or model
contents**. A saved app-owned volume is the normal choice. A `voice-clone-` name
is only a candidate; explicit selection or mounted identity verification is needed.
An explicitly selected unrelated volume uses `/workspace/voice-clone/hf-cache`
to separate the app's files from the other workload. Existing app caches keep their
legacy `/workspace/hf-cache` path so prior downloads remain reusable.

The authenticated CPU worker exposes `POST /v1/capacity` (202) and
`GET /v1/capacity`. It checks the complete required graph on a background thread,
deduplicates identical repository/revision/path entries, hashes existing files,
reports per-model checks, and measures the mounted filesystem's free bytes.
The controller polls it before requesting any full setup download. Required free
space is all additional missing writes plus the 10 GB reserve. Existing partial
bytes reduce additional writes; an ignored Range request truncates the partial
first, freeing those bytes before the full replacement. Corrupt target files remain
occupied until atomic replacement. A corrupt complete partial is downloaded again.
Insufficient capacity stops setup and asks the user to increase storage.

A `.vcs-volume.json` app identity marker lives in the selected cache namespace.
It identifies the voice-app folder; it never proves model health or readiness.
Transfer still requires verified current-file evidence. Scan cancellation joins
the background thread before worker shutdown returns, and serializes with installs.
The framework image lives on the Pod's container disk; the persistent reserve holds
caches and working space. This is a measured model-based minimum, not a live
qualification of every framework cache or a real 60 GB transfer.

Offline tests cover full-graph capacity, shared snapshot deduplication, partial
deficits, corrupt complete-partial repair, authenticated asynchronous scans,
marker isolation and joined cancellation. Real volume setup and GPU generation
remain separate qualification steps.

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
graph; it is not a measured minimum. The current downloader keeps a 10 GB decimal
free-space reserve and checks remaining writes before transfer. Public anonymous metadata
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
