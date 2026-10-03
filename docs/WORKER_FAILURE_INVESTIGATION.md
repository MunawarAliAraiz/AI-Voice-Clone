# Cloud voice and script-conversion failures

Investigation date: 2026-10-03. Read-only inspection of the installed user's
profile; no model downloads, GPU requests, or cloud mutations performed.

## Confirmed observations

- Two stored VoxCPM 2 synthesis jobs failed with `GENERATION_FAILED` and only
  `Runpod worker returned HTTP 500`.
- Two stored script-conversion jobs failed with `TRANSLITERATOR_UNAVAILABLE`
  and only `Pod script conversion is unavailable`.
- Model setup is recorded ready. No current compute session is recorded.
- One read-only provider lookup of the recorded last app-owned generation Pod
  returned HTTP 404. It has already been removed, so the failure's GPU logs
  cannot be recovered from that Pod in this investigation.
- No voice scripts, credentials, recordings or generated outputs were printed.

The stored messages do **not** establish the GPU root cause. Do not describe
these generations or text conversions as fixed or live-tested.

## Evidenced source corrections

### Pinned VoxCPM constructor

The GPU image pins `voxcpm==2.0.3`. Its exact PyPI wheel was read in memory and
hash-checked against published package metadata:

`24da58a30d094a9e9a7ead450ae9cffda0d31eaeba620b61ad99179dd87e486b`

Its constructor accepts `enable_denoiser`, not `load_denoiser`. The previous
runtime always failed the direct constructor call and then broadly caught
`TypeError`, falling back to `from_pretrained(repo)`. That fallback discarded
the already resolved pinned snapshot path, and could mask an internal
constructor failure as a signature difference.

The runtime now directly calls the correct constructor with the pinned local
snapshot, `enable_denoiser=False` and `optimize=False`. An internal constructor
error is preserved; it is not retried against a repository default.

This is a confirmed contract defect. The fallback could have succeeded, so it
is **not proven** to have caused the user's observed HTTP 500.

Primary sources: [exact PyPI version metadata](https://pypi.org/pypi/voxcpm/2.0.3/json)
and [upstream constructor](https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/core.py).
The pinned wheel, rather than moving upstream `main`, controls this correction.

### Error information lost at three boundaries

1. The worker had no handler for application errors, turning known runtime
   failures into an unstructured HTTP 500.
2. The desktop adapter discarded every error body.
3. Script and direction adapters replaced the remaining explanation with
   generic unavailable text.

Known worker errors now return curated categories for model loading, missing
cache files, GPU memory, worker crashes, timeouts and helper failures. Loading
errors retain their loading category and recognized runtime failure code.
The desktop accepts only known categories and uses its own fixed messages.
It never echoes arbitrary third-party detail, tracebacks, paths or extensions.
Script and direction adapters preserve that safe explanation.

Unknown/old-worker errors still report the HTTP status. This avoids presenting
an unsupported guessed cause.

## Verification and release boundary

Offline tests cover the exact constructor contract, no fallback on internal
`TypeError`, load/memory/cache classification, authenticated worker HTTP
failures, helper messages and rejection of malformed or private error bodies.
They use fake runtimes and do not prove real GPU generation.

Changes to worker `main.py`, the runtime and scheduler require a new immutable
GPU worker image before they affect Runpod. Packaging only a desktop update
cannot change code in an already published worker image. The desktop error
adapter can be packaged independently, but an old worker may still provide
only an unstructured 500.

Next live check, after image qualification and explicit authorization for paid
testing: one short VoxCPM generation and one conversion, recording the worker
failure category if either fails. Full voice quality still requires listening.
