# Hosted Runpod image build and release

The Windows development machine has no Docker engine. The dedicated
`Build Runpod images` GitHub Actions workflow builds two Linux/amd64 images on
disposable Ubuntu 24.04 runners. It triggers on relevant changes pushed to
`codex/desktop-runpod-mcp` in `MunawarAliAraiz/AI-Voice-Clone`, or a manual dispatch.
It publishes no PR code and uses the workflow's short-lived package token;
Runpod/Hugging Face credentials are not supplied to GitHub.

## Outputs

| Target | Registry package | Purpose |
| --- | --- | --- |
| `installer` | `ghcr.io/munawaraliaraiz/ai-voice-clone-installer` | CPU HTTP service verifying/downloading pinned persistent model snapshots without CUDA or Torch |
| `gpu` | `ghcr.io/munawaraliaraiz/ai-voice-clone-gpu` | Existing TTS/text HTTP service with isolated inference environments |

Tags use the complete source commit. Provisioning must consume the immutable
`image` reference from the successful `release-installer.json` or
`release-gpu.json` artifact, never a mutable tag. A GPU import/build check is
not a real generation or billing qualification. The workflow labels those gates
explicitly as not tested.

Each artifact records source/base/image digests, dependency lock SHA-256 hashes,
the workflow URL, platform manifest digest and summed **compressed registry
layer bytes**. Compressed bytes approximate a cold image transfer, not expanded
container disk usage or startup duration. Do not claim a measured minimum
container disk allocation from that number alone.

## Reproducibility and checks

- Python 3.12 slim Bookworm and uv 0.11.32 use immutable linux/amd64 digests.
  The Dockerfile verifies interpreter/tool versions. Debian package resolution
  uses the fixed `20260901T000000Z` snapshot.
- All five API/runtime environments and the build tools use committed
  transitive hash locks. GPU imports are checked during build; no weights load.
- Every GitHub Action is pinned to the official version's commit hash.
- OCI provenance and SBOM are published. The build context allowlist excludes
  user audio/database, `.env`, DPAPI files, and model caches.
- The installer image is pulled and launched against a disposable bind mount.
  Authenticated health/models must succeed; anonymous health must return 401;
  Torch must be absent. No setup/download POST is made during this smoke test.
- The GPU image is pushed directly by BuildKit without loading its large layers
  into the runner's Docker daemon. The installer image alone is loaded for smoke.

The GPU image contains multiple CUDA/PyTorch environments and can be large.
All GPU environments install within one layer using uv hardlinks for identical
immutable wheels, then the download cache is removed. This can reduce duplicate
CUDA 12.8 files without combining incompatible Python dependency environments.
Jobs remove unused hosted SDKs from explicitly named runner directories and
require 45 GiB available before the GPU build (8 GiB for installer). These are
build capacity guards, not measured image sizes. If the hosted runner cannot
meet the guard, use a larger Linux runner; do not weaken dependency isolation
or pretend the build succeeded. Per-target GHA cache reduces repeat downloads.
No source/cache/model keys are included in build arguments.

## First publication procedure

1. Review source and run `python -m pytest -q pod/tests`.
2. Push the accepted source to the configured branch. Monitor **both** jobs.
   A failed job provides no qualified image; do not configure its mutable tag.
3. Download the two digest evidence artifacts. Ensure the source commit matches
   the intended release. Record the published references and image sizes in
   `HANDOFF.md` and the application's immutable release configuration.
4. GHCR packages created with `GITHUB_TOKEN` may initially be private. Set only
   these worker packages to public in their package settings before anonymous
   Runpod pulls, or use a separately authorized registry credential. Verify an
   anonymous registry manifest request; repository visibility alone is not proof
   of package visibility. Do not put a GitHub access token into the desktop app.
5. Test the published CPU service on an approved Runpod volume/CPU configuration.
   Some data centers may not support CPU/network-volume combinations; regional
   eligibility and price must be checked before provisioning.
6. After approved GPU spend protection and a suitable persistent volume, run
   the real English/Urdu generation and billing gates in `POD_WORKER.md`.
   First cold image transfer and model loading are billed Pod startup time.

The HTTP GPU image is not a Serverless queue handler. Automatic terminated-Pod
generation can consume it; qualifying Serverless Flex requires a separate
handler and bounded output transfer. A briefly warm session requires a bounded
provider-side deadline. No mode may be described as production-tested solely
because its image builds.

## Local rebuild

Use the same base references as the workflow with `pod/build.py --target gpu`
or `--target installer`. Docker with Linux containers is required. A local
build does not push. See `POD_WORKER.md` for the complete command and runtime
contract.

Primary references: [Docker's GitHub Actions guide](https://docs.docker.com/guides/gha/),
[GitHub container registry authentication](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry),
[uv container integration](https://docs.astral.sh/uv/guides/integration/docker/).
