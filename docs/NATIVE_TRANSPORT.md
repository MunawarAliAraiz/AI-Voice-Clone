# Native Serverless transport checkpoint

The default controller session now selects `NativeScheduler` for desktop/UI/MCP
speech and text helper jobs. It never falls back to a Pod. Public generation
admission remains held: source-reviewed deployment and lifecycle receipts are
empty, and no revised compute allowance has been approved or imported.

## Boundaries

- `native_transport.py` uses authenticated REST v2 management and runtime v2
  status/cancel calls. Endpoint creation and `/run` require winning SQLite claims
  flushed before POST. HTTP rejection, lost response or malformed success never
  repeats a paid request.
- `native_backend.py` combines fresh rates, strict endpoint/worker parsing,
  cumulative reservations, operation journals, owned recovery, local deadline
  cleanup and billing upserts. Config versions come from actual worker readback,
  with unchanged endpoint observations around that read; none are invented.
- `QualifiedNativeSubmitter` is separate from the independent-alarm guard
  contract. A known ledger job ID repairs a missing file-journal acknowledgement
  only when endpoint, operation and payload fingerprint match.
- Inputs/results remain bounded protocol v1. Speech output is checked before
  atomic local publication. A saved result may be retrieved after admission
  expires, without new inference. Expired results remain unavailable.
- Source-reviewed qualification precedes provider credentials and mutation.
  The ledger lives outside app profiles and binds a verified Runpod account.
  No profile, API field or environment variable can supply a trusted receipt.

## Shutdown and cost limitations

The local sweeper is convenience cleanup, not PC-off protection. Native provider
startup, retries, worker ceilings, timeout, idle scale-down and billing cessation
still need real qualification. Worker-list 404 is unavailable evidence, not an
empty worker list. DELETE acknowledgement alone is not confirmed shutdown.
Reservations stay held until actual billing and a reviewed finality audit exist.
There is no claim of a provider-enforced dollar cap or cross-PC account cap.

Runpod-only generation is the default; Cloudflare remains optional. No production
Cloudflare guard deployment is authorized by the earlier mock approval.

## Verification and remaining work

The native integration suite initially passed 260 offline cases, including
existing ledger, qualification, Flex journal, controller and admission-block
regressions. Provider data and PCM fixtures are synthetic, never real output.
Independent recovery tests cover dual-journal crashes, restart cleanup, repeated
stop, cancellation and result retrieval after deadline. Exact current counts and
source hashes belong in the checkpoint receipt and handoff.

The image CLI accepts Flex and the hosted workflow supports a Flex-only build.
Its network-disabled SDK/import/mount smoke is distinct from real GPU proof.

Still required: reviewed image/lifecycle evidence, an audited allowance import
and recovery-only production bootstrap independent of admission expiry; actual
model hashes plus mounted readback; real speech/text/captions and voice-conversion
worker/queue integration; Windows VM/user installation/update testing and listening
review. The voice-conversion runtime exists but no worker/queue operation carries
it yet; protocol v1 must not silently accept that operation.

Primary provider contracts inspected:
[creation](https://docs.runpod.io/api-reference-v2/serverless/create-a-serverless-endpoint),
[workers](https://docs.runpod.io/api-reference-v2/serverless/list-serverless-endpoint-workers),
[billing](https://docs.runpod.io/api-reference-v2/billing/get-serverless-billing-history).
