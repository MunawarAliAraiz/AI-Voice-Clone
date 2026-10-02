# Automatic cloud lifecycle

## Stop model setup and retry — 2026-10-02

After selecting existing storage or purchasing storage, the frontend explicitly
enables `PUT /api/runpod/setup/auto` with `{ "enabled": true }`. This persists
the user's automatic-download intent and returns the normal setup snapshot.
Existing legacy storage has a null `auto_setup_enabled` until this one-time
intent migration; a previous confirmed paid attempt requires an explicit
Resume. Saved enabled intent resumes at API startup. Status/discovery reads
do not start paid resources.

Model setup checks **CPU** availability beside the selected volume; available
GPU stock is not needed to download files. Missing CPU capacity retries once
a minute, for at most ten retries, without creating any resource. The snapshot
exposes `auto_setup_waiting` and `auto_setup_retry_at` (ISO UTC). A creation
attempt, unknown result or failed paid session disables automatic retries and
sets `auto_setup_requires_resume`; it never automatically spends another
installation budget after a failed paid attempt. The CPU request now uses the
dedicated `deployCpuPod` mutation and `deployCpuPodInput!` input, with a
catalog-derived instance ID such as `cpu3c-2-4` (two vCPUs, four GB RAM) and
the atomic `terminateAfter` deadline. GPU-specific fields are excluded from
the CPU request. The deadline is bounded by both the $1 installation budget
and a two-hour operational limit, including worker startup; it no longer
extends to 16 hours merely because a small CPU is inexpensive.

This matches the provider schema and official SDK's CPU path; no paid CPU
launch was performed to establish end-to-end success. Sources:
[Runpod schema](https://graphql-spec.runpod.io/),
[official SDK CPU deployment](https://github.com/runpod/runpod-python/blob/main/runpod/api/ctl_commands.py).

`POST /api/runpod/setup/cancel` returns the normal cloud setup snapshot. It
first stops the local provisioning/download monitoring task, then reconciles
and terminates only the temporary installer Pod owned by that setup record.
It never deletes persistent storage, cached complete files, partial download
files, saved voices, or output. It refuses to stop a generation session or a
session with queued generation jobs. The operation continues if the HTTP
client disconnects during cleanup.

The durable phases are `cancelling` and `cancelled`. A cancelled download may
still have `cleanup_pending=true` and a `compute` record: that means Runpod
has not confirmed the temporary machine is off. An absent Pod in a single
account listing before its requested deadline does not establish that an
ambiguous creation failed. The record remains fenced against new compute and
updates until reconciliation succeeds. A discovered Pod must match the
recorded unique name before automatic deletion. A failed termination or
changed ownership retains the record and explains the required cleanup.

Retry starts a new verification session on the same selected storage. The
installer hashes pinned existing files and reuses only those whose exact
size and digest match. Missing or invalid files are fetched. Partial
`.vcs-incomplete` files use HTTP Range to request the remaining bytes; if the
source ignores Range, that individual file restarts cleanly. A full checksum
check is required before publishing a downloaded file. Model weights and
the desktop updater have separate download lifecycles.

Offline fixtures exercise cancellation during in-flight creation, late
owned-Pod reconciliation, termination failure, ownership mismatch, unchanged
storage/cache bytes and preserved progress after restart. The existing model
transfer fixtures exercise completed-file adoption without downloading,
corrupt-file repair, partial Range resume, ignored Range and invalid ranges.
These are offline evidence, not a live Runpod download or GPU qualification.

The original legacy error cannot be reconstructed from its generic stored
message. A concrete source issue was found: GraphQL schema/parse rejection
bodies returned with HTTP 400 were discarded before the safe error-code
classifier. They are now classified the same way as HTTP 200 GraphQL errors.
Only proven pre-execution parse/schema rejection clears a creation record;
an unknown HTTP 400 or resolver/network failure remains ambiguous. This
does not establish the cause of the user's original deployment failure.

## User direction, 2026-09-30

Desktop setup asks for the Runpod management key. The desktop session key is
internal to the shell/API/MCP bridge. Cloud setup lists persistent storage,
asks to create storage if none is suitable, checks an existing volume against
the complete pinned release manifest, and downloads only missing or invalid
files. Show received/total bytes, completed/total files, current file, and a
separate verification phase. Model-dependent actions remain disabled until the
entire required model set is verified. Local editing and saved output remain
available while compute is absent.

The user chooses a voice model, language and generation settings. The app
selects compatible available hardware automatically. GPU memory does not select
or change the voice model; the already resolved model is immutable for the job.
GPU compute ends when the generation queue drains and outputs have been saved
locally. Persistent storage continues to be charged.

## Current implementation boundary

`backend/app/runpod/lifecycle_plan.py` implements pure contracts for full-file
manifest verification evidence, byte/file progress, setup readiness, regional
GPU offer ranking, memory residency plans, startup/execution/idle estimates,
and safe release conditions. Its tests use supplied evidence and catalog
offers. They make no paid resources and do not prove automatic provisioning.
No routes or queue coordinator currently consume these plans.

The current remote protocol serves a manually paired Pod. The image recipe
exists, but no qualified image digest or Serverless deployment is available.
Existing downloads have snapshot completion markers, rather than the proposed
complete file size/SHA-256 manifest. A volume ID or directory listing alone
cannot establish readiness. Evidence must be bound to the selected volume and
release, produced by hashing files on that volume, and invalidated on changing
either. Changes to the model catalog require a fresh manifest.

## Proposed default: Serverless flex

A queue endpoint with zero active workers, a maximum of one worker initially,
explicit `flashboot: FLASHBOOT`, and the documented default five-second idle
timeout fits intermittent generation. Runpod scales flex workers to zero and handles shutdown even when
the Windows app closes. Submit a dialogue as a batch/session to amortize startup
over its lines, preserving the original order and per-line local job records.
An endpoint worker needs a new handler/output transport adapter; the existing
Pod HTTP interface is not automatically a Serverless queue handler.

Runpod bills container initialization, loading weights into GPU memory,
execution, and the idle interval. Keeping weights on a volume saves repeated
external downloads; it does not keep the model in GPU memory once compute
ends. There is no benchmark for this image yet, so exact startup time and
per-generation cost remain unknown. FlashBoot can retain worker state and
accelerate revival; it does not guarantee instant first generation after a
long idle period or a new host allocation.

Official endpoint configuration uses GPU priorities/fallbacks. It does not
dynamically promise the lowest price for every request. The app must retrieve
fresh Serverless pool rates, rank qualified compatible pools, and configure
their order. Pod GPU rates must not be substituted for Serverless rates.
Hourly cheapest and completed-job cheapest can differ; measured per-GPU
startup and render time should eventually drive total cost estimates.

The read-only account check earlier in this session quoted an A40 GPU Pod at
$0.49/hour. The official settings table lists the 48 GB A40/A6000 Serverless
group at $0.00034/second, equivalent to **$1.224/hour while running**. These are
different products; requery live quotes before creating either. Serverless can
still reduce total spending by avoiding idle time and improving revival, but
it is not automatically cheaper than a properly terminated Pod for a batch.
No startup/render benchmark or provider receipt has established the winner.
One dialogue queue should share one compute session, rather than creating a
new Pod per line.

Sources: [Serverless pricing](https://docs.runpod.io/serverless/pricing),
[endpoint settings](https://docs.runpod.io/serverless/endpoints/endpoint-configurations),
[v2 create endpoint](https://docs.runpod.io/api-reference-v2/serverless/create-a-serverless-endpoint).
The general settings page says FlashBoot is enabled for new endpoints, while
the v2 create schema defaults it to `OFF`; set the mode explicitly.

## Storage installation with one user-entered key

Network volumes persist independently of compute, but a single volume confines
GPU placement to its data center. Query regional stock before proposing a new
volume location. Existing unrelated storage must be selected explicitly;
never commandeer the existing video project's volume. Multiple regional
copies can improve availability but require synchronization and more storage
spend. Concurrency requires one writer/install lease per volume and manifest,
atomic completion publication, and read-only inference weights.

Runpod's S3-compatible API works without running a Pod, but requires a
**separate S3 access key and secret**. The management key alone is not an S3
credential. The currently documented v2 account resources do not establish an
automatic S3-key issuing flow. To preserve one-key setup, investigate a small
temporary CPU installer/verifier Pod or CPU Serverless worker in the volume's
data center, then release it. CPU installation still has a cost; regional
mount eligibility and progress transport need qualification. Hash existing
weights through the same verifier before skipping downloads. No GPU is needed
to fetch weights, but the current worker image preflight expects CUDA and needs
a CPU installer entry point.

Storage must fit measured pinned snapshots plus runtime caches/scratch, with
headroom for an atomic update. 150 GB is a provisional floor and 200 GB the
existing suggestion, not a measured all-model minimum. Standard network storage
is currently listed as $0.07/GB/month for the first TB, so 200 GB is about
$14/month before taxes even with no generation; show the live quote at setup.

Sources: [network volumes](https://docs.runpod.io/storage/network-volumes),
[S3 authentication](https://docs.runpod.io/storage/s3-api),
[REST v2 resources](https://docs.runpod.io/api-reference-v2/overview).

## Options for latency and cost

| Option | Benefit | Tradeoff / qualification |
|---|---|---|
| Flex workers + persistent weights | Zero idle GPU compute after scale-down; provider owns lifecycle | Startup/model load is billed; region stock constraints; new handler required |
| Host cached Hugging Face models | Runpod avoids billed download time and can use local host cache | Custom worker must use pinned cached paths; complete multi-repository speech/helper manifest not tested |
| Short session idle grace | Several generations share one warm worker | The grace interval is billed; expose an optional speed preference |
| Always active worker | Lowest repeat-request latency | Continuously billed GPU; optional explicit user preference only |
| Disposable GPU Pod | Reuses the current inference protocol; rank exact GPU offers | App must terminate compute reliably, even on local crash; image/start/load latency persists |
| Beta global volume + GPU Pods | Storage is not confined to one region | No Serverless support; extra operation charges; no qualified integration |
| High-performance network volume | Faster storage reads can shorten cold load | Premium recurring storage cost; benchmark improvement first |

Host cached models do not replace the requested user-owned persistent manifest
without another product decision. A model can be cached on disk and still need
deserialization, allocation, CUDA transfer and warmup. The host cache's benefit
must be measured for the exact pinned speech models and helper dependencies.

Sources: [cached models](https://docs.runpod.io/serverless/endpoints/model-caching),
[global volumes beta](https://docs.runpod.io/storage/globalvolume/overview),
[high-performance storage](https://docs.runpod.io/storage/high-performance-storage).

## GPU memory policy

The present full-feature capacity reserves 43,548 MiB concurrently, so 48 GB is
the safe current tier. All downloaded models need disk, not simultaneous VRAM.
A future exclusive stage strategy may release helper processes before speech
inference and use the largest stage's measured peak plus headroom. For current
reservations that arithmetic is 21,548 MiB, but it is not a qualified 24 GB
full-feature promise. Conversion/separation peaks and reference/audio length
scaling are still unmeasured. Require process eviction under the GPU slot and
actual peaks on target hardware before enabling that strategy.

## Release gates / fresh session

1. Publish a reproducible, tested image digest and an isolated CPU installer.
2. Produce a complete pinned file/size/hash manifest for the required model set.
3. Bind verified evidence to volume/release; implement resumable transfer and
   file/byte progress. Test partial snapshots, old revisions and corrupt files.
4. Implement the chosen provider adapter, durable local jobs, output retrieval,
   bounded execution, safe retries and lifecycle ownership. Never terminate an
   unrelated Pod or delete the model volume on compute release.
5. For disposable Pods, install provider-side or externally hosted maximum
   duration/budget cleanup; desktop-only shutdown cannot survive PC failure.
6. Test queue drain racing a new job under one lifecycle lock. Release compute
   only when no install, queued or active jobs remain and output is durable.
7. Run real English/Urdu/three-speaker inference, listening QA, measured startup,
   measured GPU peaks, and actual provider billing reconciliation.

No paid resources or GPU generation were created by this planning module.
# Implemented desktop adapters — 2026-10-01

`app/runpod/controller.py` now owns storage quotes/purchase reconciliation,
CPU installer progress, persisted model evidence, account-bound setup,
approved session limits and disposable GPU sessions. `managed_remote_scheduler`
and remote text helpers use the same controller/lock. The normal panel removes
manual pairing; `/api/runpod/setup*` exposes discovery, quote, selection,
explicit purchase/install, policy, status and compute release. Readonly status
never provisions. Generation admission refuses incomplete storage or missing
compute approval. The current runtime mode is a terminated HTTP Pod with a
five-second queue grace; no automatic Serverless comparison is claimed.

REST v2 currently has no Pod expiry in its create schema. The managed adapter
uses authenticated GraphQL `PodFindAndDeployOnDemandInput.terminateAfter` in
the atomic create request. This guard's behavior, CPU placement/actual pricing,
image disk size and cloud termination billing still require live qualification.
Status errors preserve ownership for reconciliation. Ambiguous purchase/Pod
creates are not replayed, and automatic cleanup only deletes the exact named
app-owned Pod; model storage is retained.

The app requires `app/runpod/release.json` with reviewed `gpu`/`installer`
immutable image references before renting resources. Hosted publication awaits
explicit user authorization after automatic review rejected public Git push.
The missing release is an honest setup block, not fabricated readiness.
