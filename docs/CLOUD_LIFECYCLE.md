# Automatic cloud lifecycle

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
