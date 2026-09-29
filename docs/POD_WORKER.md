# Pod worker checkpoint

`backend/app/remote_worker/main.py` is an authenticated, versioned TTS
boundary. It accepts a resolved model ID, text, parameters, and a reference
clip; the local scheduler persists returned audio and history on Windows.
Requests require a separate `POD_WORKER_TOKEN`. Use the Runpod HTTPS Pod proxy
for transport; do not publish plain HTTP directly to the internet.

The worker must run with `/workspace` mounted to a persistent Runpod network
volume. Set `HF_HOME=/workspace/hf-cache`, `VCS_DATA_DIR=/workspace/runtime`,
and the existing per-runtime interpreter variables documented in
`scripts/pod-bootstrap.sh`. The API environment needs `huggingface_hub` for
the new install endpoint. `POST /v1/models/{model_id}/install` starts a pinned
catalog snapshot download into `HF_HOME/hub`; `GET` on the same path reports
state. A model with unpinned revision is rejected. The endpoint never accepts
an arbitrary repository URL or revision from a client.

The existing bootstrap script is for the older web deployment and is not a
reproducible desktop Pod image. A digest-pinned worker image with the runtime
venvs, system libraries, and Python package needs to be built, published,
installed on a test Pod, and verified before provisioning can be enabled in
the UI. Test each pinned model's actual snapshot download, disk use, load,
English/Urdu output, and hourly cost. The new install API has only been tested
with a fake downloader. No Runpod Pod has been created by this branch.

The worker currently covers TTS only. Remote Qwen analysis, Gemma
transliteration, scripted dialogue, diarization, separation, and voice
conversion are still open. Do not present a 48 GB GPU as proven sufficient
for the added separation/VC models until their simultaneous peak is measured.
