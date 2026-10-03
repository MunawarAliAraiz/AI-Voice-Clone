# GPU model cache investigation — 2026-10-03

## Confirmed code defect

Advanced storage selection can use an existing volume belonging to another app.
The desktop deliberately passes `/workspace/voice-clone/hf-cache` and its `hub`
subdirectory to keep this app's files separate. The CPU installer honours those
settings. GPU startup previously overwrote both with `/workspace/hf-cache`.
This changed the model location between setup and generation. Offline runtime
lookups could therefore miss the files that setup had verified.

`pod/start.py` now accepts only the two approved cache homes, checks that
`HF_HUB_CACHE` matches that home's `hub` directory, and preserves the selected
location. Torch and compiler caches use the same app-specific parent on shared
volumes. Unexpected or mismatched paths fail before filesystem setup. Dedicated
legacy volumes retain their existing cache paths.

This is a verified code defect, but it is **not established as the cause** of the
reported VoxCPM HTTP 500 or unavailable script converter. The affected Pod and
its traceback were unavailable, and dedicated storage uses the original path.

## Gemma compatibility checks

The exact pinned config for
`unsloth/gemma-4-31B-it-unsloth-bnb-4bit` at
`8e256fc6d63003fc0ca8c91b976e6dcc38433385` declares `gemma4`,
`Gemma4ForConditionalGeneration`, and bitsandbytes NF4 quantization. Its
tokenizer uses the Tokenizers backend. Official Transformers `v5.14.1`
source includes the Gemma4 configuration and model mappings. The image's text
environment pins that version, Torch 2.8.0 CUDA 12.8, Accelerate 1.14.0, and
bitsandbytes 0.50.0. No specific model-type or missing SentencePiece dependency
defect was found in this review.

The current GPU selector requires at least 48 GB. Its capacity check requests
43,548 MiB (16,000 audio + 19,500 converter + 6,000 analyzer + 2,048 headroom),
which fits ordinary 48 GB cards. This rules out a simple selection-versus-reserve
mismatch, rather than proving actual GPU readiness.

Primary source checks:

- [Pinned Gemma config](https://huggingface.co/unsloth/gemma-4-31B-it-unsloth-bnb-4bit/blob/8e256fc6d63003fc0ca8c91b976e6dcc38433385/config.json)
- [Pinned Gemma tokenizer config](https://huggingface.co/unsloth/gemma-4-31B-it-unsloth-bnb-4bit/blob/8e256fc6d63003fc0ca8c91b976e6dcc38433385/tokenizer_config.json)
- [Official Transformers v5.14.1 auto mappings](https://github.com/huggingface/transformers/blob/v5.14.1/src/transformers/models/auto/modeling_auto.py)
- [Official Transformers v5.14.1 Gemma tokenizer](https://github.com/huggingface/transformers/blob/v5.14.1/src/transformers/models/gemma/tokenization_gemma.py)

## Qualification

Focused startup regression tests cover both approved paths, the legacy default,
isolated Torch caches, offline settings, and unexpected/mismatched path refusal.
These tests use temporary directories and mocked CUDA import checks. No live
Pod, paid download, model load, script conversion, or speech generation was run.
The corrected source must enter a newly qualified immutable GPU image before
desktop publication; changing desktop source alone does not update a Pod image.
