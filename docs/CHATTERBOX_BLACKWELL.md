# Chatterbox on Blackwell GPUs

## Observed failure

The live Runpod test using Chatterbox 0.1.7 on a PRO 6000 Blackwell MIG
48 GB GPU failed during model loading with `CUDA_ARCHITECTURE_UNSUPPORTED`.
The previous isolated Chatterbox environment used PyTorch/Torchaudio
2.6.0 with CUDA 12.4. Other speech environments already use CUDA 12.8.
This is a GPU runtime compatibility failure; it does not mean model files
must be downloaded again.

## Proposed correction and qualification boundary

Keep Chatterbox 0.1.7, its model revisions and its weight files unchanged.
Use PyTorch **2.8.0+cu128** and matching Torchaudio **2.8.0+cu128** in its
isolated environment. The official [PyTorch 2.7 release notes](https://pytorch.org/blog/pytorch-2-7/)
describe Blackwell support with CUDA 12.8. The official
[previous-version instructions](https://pytorch.org/get-started/previous-versions/)
provide the matching 2.8.0 CUDA 12.8 package pair.

Chatterbox 0.1.7 package metadata declares Torch/Torchaudio 2.6.0.
`pod/requirements/chatterbox.overrides.in` explicitly replaces those two
constraints. This is an app compatibility exception requiring our own
qualification, not a statement that upstream certifies the newer pair.
[uv overrides](https://docs.astral.sh/uv/pip/compile/#overriding-dependency-versions)
replace dependency requirements rather than merely adding constraints.
Do not remove the override or hide a resolver conflict using `--no-deps`.

## Reproduce the lock

Use **uv 0.11.32**, Linux x86_64 manylinux 2.28, Python 3.12, binary wheels
and SHA256 hashes. No Python installation or model download is needed.
Keep the existing output lock so uv preserves unrelated package versions.

```powershell
uv pip compile pod/requirements/chatterbox.in --python-version 3.12 --python-platform x86_64-manylinux_2_28 --no-python-downloads --no-config --generate-hashes --only-binary :all: --emit-index-url --no-annotate --no-header --override pod/requirements/chatterbox.overrides.in --torch-backend cu128 --output-file pod/requirements/chatterbox.lock --quiet
```

The changed packages are Torch, Torchaudio, their NVIDIA CUDA libraries,
Triton and Sympy. All unrelated existing package versions remain pinned.
The image uses `uv pip sync --require-hashes --torch-backend cu128`; it
installs this resolved lock without resolving Chatterbox metadata again.
The lock is deliberately scoped to the Pod environment. Local desktop
Python remains free of Torch.

## Checks

- Image build: import Chatterbox and its watermarker, verify Chatterbox
  0.1.7 and exact Torch/Torchaudio/CUDA versions. No weights are loaded.
- GPU startup: discover CUDA and device capability, then execute a tiny
  actual CUDA kernel and synchronize in every isolated runtime. Device
  detection alone cannot establish kernel compatibility.
- Unit tests: verify override/build wiring, hashes, unchanged unrelated
  dependency pins and preflight rejection/success behavior with doubles.
- Required live gate: build and qualify the immutable image, then test
  actual Chatterbox generation on the failing Blackwell GPU, recording
  model loading, inference, valid non-silent output and cost. Also check
  an already working speech runtime to detect image regressions.

**Current status: lock resolved; 28 focused Pod tests pass and Ruff checks
are clean. No successful
Chatterbox GPU output has yet been established with this new pair.**
Import checks and simulated CUDA checks are not generation proof.
