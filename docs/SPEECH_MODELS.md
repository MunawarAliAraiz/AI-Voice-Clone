# Speech conversion models and qualification

Checkpoint: 2026-09-30. These pins were read from official PyPI wheel metadata
and Hugging Face model metadata. An adapter and contract tests are implemented;
no GPU voice conversion, diarization or separation generation has been tested.

## Implemented Chatterbox VC adapter

`backend/app/inference/runtimes/chatterbox_vc.py` supplies a standalone GPU
entry point and a testable `ChatterboxVCAdapter`. It consumes an existing source
speech WAV and an assigned target voice reference. It sends both audio paths
to Chatterbox VC; it does not transcribe and regenerate text.

| Item | Exact pin |
| --- | --- |
| Adapter model ID | `chatterbox_vc` |
| Python distribution | `chatterbox-tts==0.1.7` |
| Inspected official wheel SHA256 | `83782500e3ad4e7c919132e9d7eb8755f29f57c5bde5ec48c655ca23a4eb113c` |
| Tokenizer distribution | `s3tokenizer==0.3.0` |
| Weight repository | `ResembleAI/chatterbox` |
| Repository revision | `5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18` |
| Required checkpoint | `s3gen.safetensors` |
| Checkpoint SHA256 | `2b78103c654207393955e4900aac14a12de8ef25f4b09424f1ef91941f161d4e` |
| Checkpoint bytes | `1056484620` |
| Output sample rate | 24000 Hz, mono |
| Code and repository weight license | MIT |
| Access gate | None reported for this repository revision |

The VC checkpoint differs from the TTS loader's `s3gen.pt`. The adapter verifies
the exact snapshot directory revision and checkpoint hash before loading. It
constructs S3Gen/ChatterboxVC from that local safetensors file and rejects
unexpected/missing state keys except the two derived tokenizer buffers named
by the inspected package. It omits the optional builtin `conds.pt` pickle and
always supplies the user's target reference. It never calls the package's
unpinned `from_pretrained` method or downloads a repository on inference.

Source and target WAVs are copied to ephemeral request storage and checked
against supplied SHA256 hashes before the model reads them. Hashes demonstrate
byte consistency; the orchestrator must separately verify consent/authority
for each assigned voice. Input is limited to 50 MB, source chunks to 30 seconds
and reference clips to 60 seconds. The package conditions on the first ten
seconds of the target reference. It averages stereo inputs in its own decoder;
the output is mono. A one-hour recording therefore needs the orchestrator's
chunk/stem plan and assembly, which this adapter does not supply.

The adapter validates finite, nonempty, non-silent output without clipping,
publishes a PCM24 WAV atomically without overwriting an existing result, and
cleans temporary files on failure. It reports pinned model information, input
and output hashes, actual durations, load and generation timings, and
`quality_status=awaiting_listening_review`. GPU synchronization bounds timing.
The caller owns exclusive GPU admission and subprocess termination; terminating
the standalone process releases its CUDA context.

### Standalone request

Run inside the pinned Chatterbox runtime, with the API source on `PYTHONPATH`:

```text
python -m app.inference.runtimes.chatterbox_vc --request <conversion-request.json>
```

The JSON request contains:

```json
{
  "snapshot_dir": "/workspace/hf-cache/hub/models--ResembleAI--chatterbox/snapshots/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
  "source_audio": "/tmp/request/source.wav",
  "source_sha256": "<SHA256 of source.wav>",
  "target_reference": "/tmp/request/target.wav",
  "target_sha256": "<SHA256 of authorized target.wav>",
  "output_path": "/tmp/request/new-converted.wav"
}
```

Stdout contains one JSON result; dependency chatter is redirected to stderr.
On failure the result is `ok=false` with a stable, redacted detail. There is no
successful fallback audio. These paths belong to a trusted orchestrator; do not
expose this file-based CLI directly as an unrestricted remote API.

## Ungated diarization and overlap separation research

**User decision: avoid model access approval gates.** The previously researched
pyannote route is excluded from the current plan. The following candidates are
research findings, not enabled runtime adapters or qualified generation paths.

### Recommendation for the first qualification experiment

Evaluate **NVIDIA streaming Sortformer 4spk-v2 + SpeechBrain SepFormer
Libri3Mix + SpeechBrain ECAPA embeddings + the existing Chatterbox VC adapter**.
These official weight repositories were accessible anonymously on 2026-09-30.
This is a concrete experiment for three concurrent voices; it is not a claim
that automatic conversion already works or meets production audio quality.

| Component | Official checkpoint | Weight license | Native capability and limitation |
| --- | --- | --- | --- |
| Speaker activity | [Sortformer 4spk-v2](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2) | CC-BY-4.0 | Mono 16 kHz; up to four total speaker identities, streaming cache; activity probabilities rather than separated audio |
| Three-source separation | [SepFormer Libri3Mix](https://huggingface.co/speechbrain/sepformer-libri3mix) | Apache-2.0 | Exactly three output streams; mono **8 kHz**; trained on synthetic English speech mixtures |
| Voice matching | [ECAPA VoxCeleb](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) | Apache-2.0 | Mono 16 kHz speaker embeddings; neither diarization nor audio separation |
| Alternative diarizer | [Nemotron 3 Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization) | OpenMDW-1.1 | Mono 16 kHz; up to eight total identities; activity/timestamps; released 2026-09-23, runtime compatibility unverified here |

Sortformer's published overlap-aware diarization evaluation does not prove
clean three-voice audio separation. The four-speaker ceiling applies to the
whole recording, independently of the requested three simultaneous voices.
Its cache is designed for long recordings, but very long, noisy or non-English
recordings can degrade. Preserve overlapping activity channels, rather than
forcing one exclusive speaker per frame. [Official model card](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2).

The Libri3Mix configuration explicitly fixes `num_spks: 3` and
`sample_rate: 8000`. Upsampling separated stems to Chatterbox's input/output
rate cannot recover the original high-frequency content. This makes it a
limited-bandwidth qualification candidate, not a verified high-fidelity
solution. Speech with fewer active people needs separate source-count handling;
three fixed outputs must not be interpreted as three real voices automatically.
Real meetings, background noise, Urdu and hour-long identity continuity remain
untested. [Pinned inference configuration](https://huggingface.co/speechbrain/sepformer-libri3mix/resolve/1f2d824e2333aec9e60d0030fb144885c1afc426/hyperparams.yaml).

Nemotron is an alternative if more than four total identities are required. It
still needs a separator, so it does not remove the 8 kHz limitation above. The
card's Transformers example currently installs from source: pin an actual
supporting commit and verify imports rather than assuming the existing text
runtime supports its architecture. Its distinct OpenMDW-1.1 terms must be
preserved in the model notices; the NeMo code license is not its weight license.
[Official model card and license link](https://huggingface.co/nvidia/Nemotron-3-Diarization#licenseterms-of-use).

### Exact download pins and access evidence

| Repository | Immutable revision | Access verification |
| --- | --- | --- |
| `nvidia/diar_streaming_sortformer_4spk-v2` | `84edd514b8ef68004c10086918cd62f2148cbd59` | Metadata `gated: false`; anonymous checkpoint range GET returned 206 |
| `speechbrain/sepformer-libri3mix` | `1f2d824e2333aec9e60d0030fb144885c1afc426` | Metadata `gated: false`; anonymous masknet range GET returned 206 |
| `speechbrain/spkrec-ecapa-voxceleb` | `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286` | Metadata `gated: false`; anonymous embedding range GET returned 206 |
| `nvidia/Nemotron-3-Diarization` | `f667ed73aee57d40cc39428eb768b4fd87a0a29e` | Metadata `gated: false`; anonymous `.nemo` range GET returned 206 |

Each access probe requested only bytes 0-1023, used no authorization header or
HF token and read 1024 bytes. These checks establish public binary access at
this checkpoint. They do not verify complete weight hashes, runtime loading,
conversion performance or output quality. Hashes below are the official Hub
LFS metadata values; the downloader must verify full files before use.

| Repository / inference artifact | Bytes | SHA256 from official metadata |
| --- | ---: | --- |
| Sortformer / `diar_streaming_sortformer_4spk-v2.nemo` | 471367680 | `b371afce2c4958186469df33d939936b9746c89f38b10a69cfd2c61254e83329` |
| Libri3Mix / `masknet.ckpt` | 113375814 | `416af7b09d912c486261dd824788dd92d3b21373c8ec4d029209792522f4dbcb` |
| Libri3Mix / `encoder.ckpt` | 17272 | `e949e80c1a96965b723460209bb155eac1fdb2f6d2cf4e7b3e1d3152716f38d0` |
| Libri3Mix / `decoder.ckpt` | 17272 | `9afdda4e4ecb9eefb5d49329193c7a9215ae7443aab206f2fe4eb0f50988ca53` |
| ECAPA / `embedding_model.ckpt` | 83316686 | `0575cb64845e6b9a10db9bcb74d5ac32b326b8dc90352671d345e2ee3d0126a2` |
| ECAPA / `classifier.ckpt` | 5534328 | `fd9e3634fe68bd0a427c95e354c0c677374f62b3f434e45b78599950d860d535` |
| Nemotron alternative / `Nemotron-3-Diarization.nemo` | 198676480 | `867c53f552998f772e5b5e5c082962ae85ee7ca5669c2bc17d7f615133d4e96d` |

Download the pinned YAML/configuration and small inference files too. ECAPA's
full inference configuration also loads `mean_var_norm_emb.ckpt` and
`label_encoder.txt`; resolve their exact bytes/hashes when implementing the
manifest. Its YAML embeds repository paths: override these with local pinned
snapshot paths so the pretrainer cannot fetch mutable `main`. Exclude
Libri3Mix's 206 MB optimizer checkpoint, which is for training. The preferred
Sortformer/Libri3Mix/ECAPA inference weights total approximately 674 MB, plus
small configuration files and the existing 1.06 GB VC checkpoint. This is a
weight subtotal, not a Pod volume minimum: environments, caches, all other
models and temporary workspace still need separate budgeting.

Metadata sources: [Sortformer](https://huggingface.co/api/models/nvidia/diar_streaming_sortformer_4spk-v2/revision/84edd514b8ef68004c10086918cd62f2148cbd59?blobs=true),
[Libri3Mix](https://huggingface.co/api/models/speechbrain/sepformer-libri3mix/revision/1f2d824e2333aec9e60d0030fb144885c1afc426?blobs=true),
[ECAPA](https://huggingface.co/api/models/speechbrain/spkrec-ecapa-voxceleb/revision/0f99f2d0ebe89ac095bcc5903c4dd8f72b367286?blobs=true),
[Nemotron](https://huggingface.co/api/models/nvidia/Nemotron-3-Diarization/revision/f667ed73aee57d40cc39428eb768b4fd87a0a29e?blobs=true).

### Required identity stitching and audio assembly

The following is an implementation proposal inferred from the components'
interfaces; it has not been built or validated in this app.

1. Diarize the recording once, preserving multiple active speakers and stable
   recording-level IDs. Reject unsupported total-speaker counts.
2. Obtain clean single-speaker anchor clips and a reviewed target-voice
   assignment for each ID. Extract ECAPA embeddings at its expected 16 kHz.
3. Preserve original samples in non-overlap regions. Separate overlap windows
   at the separator's native rate. Implement one-, two- and three-source
   handling rather than always accepting all three output streams.
4. Associate separated streams with the recording IDs using anchor embedding
   similarity, activity intervals and overlapping samples from adjacent
   windows. Separation channels can swap between windows; use explicit
   assignment/permutation matching and confidence checks. ECAPA verification
   accuracy on its original corpus does not qualify these separated stems.
5. Leave low-confidence identities unresolved for review. A recording with no
   clean anchors, similar voices or persistent triple overlap cannot be
   promised automatic correct mapping merely because three channels exist.
6. Convert each assigned stem with bounded Chatterbox chunks. Reconcile timing,
   fades and joins, then save editable speaker stems and the reconstructed mix.
   Inspect speaker swaps, cross-talk, lost words and duration drift.

### Alternatives evaluated

| Option | Finding | Decision at this checkpoint |
| --- | --- | --- |
| [WhisperX](https://github.com/m-bain/whisperX) | Its current diarization instructions require Community-1 access/token; its README flags overlap limitations. ASR alignment does not separate audio. | Does not satisfy the ungated conversion path as supplied. Replacing its diarizer and adding separation still needs the work above. |
| [SepFormer WSJ0-3Mix](https://huggingface.co/speechbrain/sepformer-wsj03mix) | Official ungated Apache-2.0 three-source checkpoint, revision `21e78b44a378092c34fff32ba8e788a73584f474`; also 8 kHz English mixtures. | Possible comparison checkpoint, not an upgrade to wideband or long-recording identity support. |
| [ClearerVoice MossFormer2](https://github.com/modelscope/ClearerVoice-Studio/blob/main/clearvoice/demo_Numpy2Numpy.py) | The audio-only `MossFormer2_SS_16K` example emits two speakers. Its audio-visual target extraction requires video. | Does not supply the requested audio-only three-overlap path. |
| [ESPnet EEND-SS paper](https://arxiv.org/abs/2203.17068) and [toolkit](https://github.com/espnet/espnet) | Joint separation/diarization research supports a variable number of sources. No official ready-to-use three-person checkpoint with verified anonymous access, exact pin and weight license was established in this research pass. | Further research option; a paper/training recipe is insufficient evidence of a deployable checkpoint. |

Do not use unofficial ungated mirrors to bypass a publisher's gated terms.

### Maintainability and isolated runtimes

NeMo and SpeechBrain code are Apache-2.0; their weight licenses are listed
separately above. Official release metadata observed `NeMo v3.0.0` (2026-08-07)
and `SpeechBrain v1.1.1` (2026-08-27). These are candidate dependency starting
points, not resolved locks or proof of model compatibility. Libri3Mix is an
older checkpoint in an actively released toolkit. Nemotron is newer and needs
an explicit architecture/version compatibility check. Source-install examples
must not become unpinned Git dependencies in the Pod image.
[NeMo release](https://github.com/NVIDIA-NeMo/Speech/releases/tag/v3.0.0),
[NeMo code license](https://github.com/NVIDIA-NeMo/Speech/blob/v3.0.0/LICENSE),
[SpeechBrain release](https://github.com/speechbrain/speechbrain/releases/tag/v1.1.1).

Keep the NeMo diarizer, SpeechBrain separator/embedding and Chatterbox in
isolated interpreters. Resolve CUDA, torch, torchaudio and decoder dependencies
with hashes; import-test each image environment; load only verified local
snapshots; explicitly disable opportunistic downloads on inference. Run
phases sequentially and release models/processes before the next phase where
needed. A `.nemo` or `.ckpt` checkpoint may involve framework deserialization;
only allow the audited official artifact, after full hash verification.

## Remaining work for the ungated path

1. Resolve complete local load graphs, small-file hashes and isolated runtime
   locks. Add model notices, persistent weight downloads and truthful status.
2. Implement overlap-preserving diarization, source-count handling, reviewed
   speaker assignments, confidence-aware identity stitching and stem assembly.
3. Connect the VC subprocess and new phases through the Pod worker/local job
   contracts, authenticated uploads, cancellation and ephemeral cleanup.
4. Qualify English and Urdu single-, two- and three-speaker fixtures, including
   long recordings, missing clean anchors and changing speaker counts. Listen
   to source separation and the final converted mix; test joins and identities.
5. Measure peak VRAM, disk, first/warm timings and actual provider billing per
   GPU. Neither a minimum VRAM nor a reliable per-generation cost for this
   pipeline is established. Model-card throughput is not app cost evidence.

The 8 kHz separator tradeoff remains unresolved for production-quality audio.
Automatic three-overlap conversion must remain unqualified until real outputs
pass the above checks. No GPU provision or paid generation was performed for
this research.

## Historical gated route: excluded by current decision

Earlier research identified Community-1 speaker discovery and PixIT/ToTaToNet
three-speaker separation. All three official repositories report access gates,
so they are not part of the current ungated recommendation. Their contact/terms
approval has not been supplied; no gated configurations were retrieved. Keep
these pins only as research history, not an enabled download manifest.

| Repository | Previously observed revision | Weight license |
| --- | --- | --- |
| `pyannote/speaker-diarization-community-1` | `3533c8cf8e369892e6b79ff1bf80f7b0286a54ee` | CC-BY-4.0 |
| `pyannote/speech-separation-ami-1.0` | `9486b106945ae0cc0784041a08bfcdba5edadfb9` | MIT |
| `pyannote/separation-ami-1.0` | `4d38e95cfd067c894b8b60b00761831fb01e4a8c` | MIT |

Adapter tests use an injected fake backend exclusively to check arguments,
hash validation, cleanup, invalid-output rejection and publication contracts.
They are not real model outputs and prove no conversion quality. Existing TTS
listening results also do not qualify the separate voice conversion path.

## Primary sources

- [Chatterbox 0.1.7 official package](https://pypi.org/pypi/chatterbox-tts/0.1.7/json)
- [Chatterbox VC source](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/vc.py) (the implementation was checked against the actual 0.1.7 wheel)
- [Pinned Chatterbox checkpoint metadata](https://huggingface.co/api/models/ResembleAI/chatterbox/revision/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18?blobs=true)
- [Community-1 model card and conditions](https://huggingface.co/pyannote/speaker-diarization-community-1)
- [PixIT pipeline card and conditions](https://huggingface.co/pyannote/speech-separation-ami-1.0)
- [ToTaToNet chunk model and limits](https://huggingface.co/pyannote/separation-ami-1.0)
- [PixIT 3.3.2 source](https://raw.githubusercontent.com/pyannote/pyannote-audio/3.3.2/pyannote/audio/pipelines/speech_separation.py)
- [pyannote.audio 4.0.4 metadata](https://pypi.org/pypi/pyannote.audio/4.0.4/json), [3.3.2 metadata](https://pypi.org/pypi/pyannote.audio/3.3.2/json)
