# Runpod worker deployment

The Pod is an authenticated inference service. Windows retains voice profiles,
the database, editable scripts, history and finished audio. The Pod's network
volume retains model weights and runtime caches. Worker protocol version 1
uses a separate `POD_WORKER_TOKEN`; it never needs the user's Runpod account key.

## Implemented contract

`backend/app/remote_worker/main.py` exposes health, model/install status,
catalog-pinned model download, warm, TTS synthesis, Qwen speech direction and
Gemma transliteration. Requests carry an already resolved model ID; returned
audio must identify that same model. The desktop pairs through
`https://<pod-id>-8000.proxy.runpod.net`. Expose **8000/http** in the Pod template;
do not expose unauthenticated raw HTTP on the public internet.

The install API accepts known IDs and fixed revisions only. It writes a
completion marker after snapshot download; an interrupted snapshot alone is
not considered installed. User-supplied arbitrary repositories or revisions
are not accepted. Model access errors are redacted. Gated models need the user
to accept their terms and provide `HF_TOKEN` as a Pod environment secret.
Do not call `huggingface_hub.login()` in the container: that would persist the
token on the network volume.

## Container layout

| Path | Contents | Persistence |
| --- | --- | --- |
| `/opt/vcs/app` | Checked-in worker source | Immutable image |
| `/opt/venvs/api` | FastAPI, HTTP/download client; no torch | Immutable image |
| `/opt/venvs/voxcpm` | VoxCPM 2.0.3, torch/torchaudio 2.8 cu128 | Immutable image |
| `/opt/venvs/chatterbox` | Chatterbox 0.1.7, torch/torchaudio 2.6 cu124 | Immutable image |
| `/opt/venvs/omnivoice` | OmniVoice 0.2.1, torch/torchaudio 2.8 cu128 | Immutable image |
| `/opt/venvs/text` | Qwen/Gemma dependency stack; each runs in its own child process | Immutable image |
| `/workspace/hf-cache/hub` | Pinned snapshots and install markers | Network volume |
| `/workspace/torch-cache`, `/workspace/torch-inductor` | Runtime caches | Network volume |
| `/tmp/vcs-runtime-*`, `/tmp/vcs-worker-*` | Runtime state, uploads, generated audio | Ephemeral container disk |

`pod/start.py` requires `/workspace` to be an actual mount, verifies writable
cache directories, overrides legacy persistent `TMPDIR`/`VCS_DATA_DIR`, refuses
desktop/remote scheduler recursion, disables fake audio, and checks runtime
imports and CUDA in child interpreters. Upload/output files are removed after
the response; a crash may leave files on the ephemeral container disk until it
is removed. Recreating the Pod retains weights but does not retain user audio.

## Build from the repository root

Docker with Linux containers is required. This Windows development session has
no Docker engine, so the image has **not** been built or GPU-tested.
The committed [hosted image workflow](POD_IMAGE_RELEASE.md) builds/publishes
the CPU installer and GPU targets independently and captures digest/size
evidence. Publishing must pass that workflow before either image is configured
for provider provisioning.

1. Resolve the current `linux/amd64` digests of `python:3.12-slim-bookworm` and
   `ghcr.io/astral-sh/uv:0.11.32` using `docker buildx imagetools inspect`.
2. Build using those immutable references:

   ```text
   python pod/build.py --python-image python:3.12-slim-bookworm@sha256:<digest> --uv-image ghcr.io/astral-sh/uv:0.11.32@sha256:<digest> --tag <registry>/voice-clone-worker:<version>
   ```

3. The build installs six committed dependency locks with SHA-256 hashes.
   Debian system packages come from the fixed `20260901T000000Z` snapshot.
   Imports are checked without downloading or loading model weights. A failure
   stops the build. The image contains ffmpeg/libsndfile and no model weights,
   local data directory, account keys or Hugging Face token.
4. Push the successful image to the selected registry, obtain its published
   `RepoDigest`, and use **that digest** in Runpod provisioning. Record the
   source commit, base digests, image digest and dependency-lock hashes in the
   release evidence. Tags alone are insufficient for a reproducible release.

`pod/Dockerfile.dockerignore` restricts the context to worker source and Pod
manifests, including when run from a checkout that contains local references.
The builder deliberately requires explicit base digests and Python 3.12/uv
0.11.32; no digest is guessed or presented as a published image.

### Dependency maintenance

`pod/requirements/*.in` pins direct dependencies. The corresponding `*.lock`
files pin all transitives and accepted distribution hashes for Python 3.12 on
Linux x86_64. The API pins were drawn from `backend/uv.lock` with the additional
Hub downloader. Regenerate intentionally with uv 0.11.32:

```text
python pod/lock.py
```

Four inference/API environments use wheels only. VoxCPM also requires the
source-only pure Python packages `argbind` and `oss2`; those are explicitly
allowed, hash-checked and built without isolation using pinned setuptools
80.9.0 and wheel 0.45.1. There is no unconstrained build-dependency install.
PyTorch and TorchCodec use the explicit CUDA wheel backend at both lock and
install time. Chatterbox's declared torch 2.6 requirement is respected instead
of the old bootstrap script's incompatible torch 2.8 override. The package
versions have been resolved, but model compatibility must still pass the real
image smoke tests below.

## Configure a voice Pod

- Use a **separate voice network volume**. Do not attach or repurpose a video
  project's existing volume. Mount it at `/workspace`.
- Provisionally allow **200 GB** for all weights/cache; **150 GB** is the current
  UI minimum. Measure each installed snapshot and remaining space before
  claiming an exact minimum for all planned conversion/separation models.
- Existing full-feature capacity arithmetic requires **43548 MiB**, so start
  qualification on an NVIDIA **48 GB or larger** GPU. A 24 GB Pod is an audio
  subset with Gemma unavailable. Added speech conversion/separation peaks are
  unmeasured and must be qualified independently.
- Set a randomly generated URL-safe `POD_WORKER_TOKEN` of at least 24
  characters in Runpod environment secrets. The desktop pairing token and Pod
  token must match. Never place it in a Docker build argument or command line.
- Optional `HF_TOKEN` is also an environment secret for gated model access.
- Supply provider-side time/spend protection before a paid test. An API health
  response does not prove account balance or affordability.

Runtime interpreter settings are already configured in the image. Run one
uvicorn worker so a second API process cannot compete for GPU residency. Start
without automatic model loads; use the desktop's download controls, then warm
or generate a specific installed model.

## Verification and release evidence

Run `python pod/smoke.py --url <worker-url>` in the backend API environment with
`POD_WORKER_TOKEN` set. It performs authenticated health/model checks and makes
no generation request by default. To explicitly test real generation:

```text
python pod/smoke.py --url <worker-url> --model voxcpm2 --reference <authorized-reference.wav> --text "This is a voice generation test." --output <new-result.wav>
```

The script refuses a mismatched model response or output overwrite and reports
output hash, duration and timing. It labels quality as awaiting listening
review and cost as unmeasured. It does not replace the release gate:

1. Measure snapshot download size and persistent disk use for every enabled ID.
2. Warm and generate representative English/Urdu on the actual image/GPU.
   Inspect model revision, output hash, peak VRAM and first/warm timings.
3. Listen for voice identity, pronunciation, intelligibility and artifacts.
4. Exercise Qwen direction and all supported Gemma conversions, including
   simultaneous/resident capacity boundaries.
5. Recreate the Pod against the same volume and verify installed markers,
   warmed cache reuse and absence of local voice/history data.
6. Record GPU rate, boot/load/inference durations and actual Runpod billing.
   UI estimates remain uncalibrated until reconciled with these measurements.

Local verification in this checkpoint: all five runtime/API dependency
solutions resolved with hashes; the pinned build-tool lock also resolved;
four deployment tests passed (immutable image validation, mount/recursion
guard, ephemeral data override, health credential redirect refusal). Docker build, actual model
downloads, GPU generations, listening and billing have not run.

## Remaining gaps

- No image is published yet and no voice Pod has been created by this branch.
- F5 remains in the historical catalog, but `make_backend` has no F5 runtime
  implementation. The image does not claim to make that unavailable path work.
- Recorded-audio diarization, three-speaker separation and voice conversion
  GPU adapters are not supplied by this TTS/text image. They need isolated,
  pinned runtime dependencies, accepted gated terms and real overlap QA.
- OmniVoice weights are noncommercial; its code/package being installed does
  not change that license. Download/use requires the explicit personal-use
  choice described by the catalog and licensing documentation.

The old `scripts/pod-bootstrap.sh` remains a legacy web/lab setup, with mutable
dependency installs and different persistent data handling. Use this container
path for the desktop worker release after it passes qualification.

## Primary references

- [uv Docker integration](https://docs.astral.sh/uv/guides/integration/docker/)
- [PyTorch CUDA wheel versions](https://pytorch.org/get-started/previous-versions/)
- [TorchCodec/PyTorch compatibility](https://github.com/meta-pytorch/torchcodec)
- [VoxCPM package metadata](https://pypi.org/pypi/voxcpm/2.0.3/json)
- [Chatterbox package metadata](https://pypi.org/pypi/chatterbox-tts/0.1.7/json)
- [OmniVoice package metadata](https://pypi.org/pypi/omnivoice/0.2.1/json)
