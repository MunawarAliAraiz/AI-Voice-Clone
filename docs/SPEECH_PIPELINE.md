# Dialogue and recorded-audio conversion

## Implementation checkpoint: 2026-09-30

`backend/app/speech/` provides immutable CPU plans and real PCM16 WAV assembly.
It imports no GPU framework. Ten CPU tests exercise exact audio samples, three
speaker overlaps, stereo channel placement, chunk boundaries, timing, hashes,
mapping review, authorization metadata, input mutation, and atomic failure.

These CPU plans do **not** perform speech synthesis, diarization, source separation,
or voice conversion. Passing these tests is not a GPU generation or listening
qualification. The desktop Dialogue tab now generates independent lines through
the existing durable synthesis queue, retains attempts for targeted regeneration,
and assembles succeeded outputs through `POST /api/dialogue/assemble`. Drafts are
saved by `GET/PUT /api/dialogue/draft` in the local application data directory;
they do not depend on the shell's changing loopback port. This integration uses
`speech/dialogue_assembly.py` for format normalization and streamed assembly.
Automatic recorded-audio conversion and dedicated dialogue/conversion MCP tools
remain open.

## Scripted dialogue contract

`ScriptLine(id, speaker_id, text, pause_after_sec)` describes independently
editable lines. `build_script_plan(lines, voices, model)` requires exactly one
assigned `SpeakerVoice` for each speaker, with between one and three speakers.
Each line id must be unique and text nonempty. A pause is finite, 0–60 seconds.

`SpeakerVoice` records the enrolled voice id, local reference path and SHA256,
authorization record id and evidence SHA256, and optional reference transcript.
The reference file hash is verified when planning and again before assembly.
The caller must obtain the authorization record from an actual enrollment flow;
the module validates its identifiers and hash shape, not the truth of consent.
An enrollment evidence store and user-facing consent flow remain necessary.

`ModelPin(id, repo, revision, code_license, weights_license)` requires a full
40-character commit and both licenses. The caller supplies this from the vetted
registry. A syntactically valid pin does not establish that a model is approved,
downloaded, licensed for this user, or GPU-qualified.

After the Pod has generated each line with its assigned voice, call:

```python
from pathlib import Path
from app.speech import assemble_script_wav

# plan comes from build_script_plan; outputs are actual Pod generation files.
result = assemble_script_wav(
    plan,
    {"line-1": Path("/local/line-1.wav"), "line-2": Path("/local/line-2.wav")},
    Path("/local/dialogue.wav"),
)
```

The mapping must contain exactly every planned line. Output follows plan order,
regardless of dictionary order. Explicit pauses are included after each line,
including the last. Inputs must share sample rate/channel format. Mono and stereo
PCM16 WAV are supported; other formats require an explicit preprocessing step.
Assembly streams bounded chunks, writes atomically, and rejects truncated audio.
The result records source hashes and frame-based line spans so revisions can
replace one line without reconstructing speaker assignments.

## Recorded conversion contract

`build_conversion_plan(source_path, stems, voices, models,
mapping_review_id=...)` requires:

- A source WAV of at most 3,600 seconds.
- Real `RecordedStem(id, speaker_id, audio_path, audio_sha256, start_sec)` outputs
  produced by speaker diarization and separation; segments cannot extend beyond
  the source timeline or overlap another segment of the same speaker.
- At most three assigned speakers and three simultaneous stems. Endpoints use
  frame positions; a segment ending as the next starts does not overlap it.
- An explicit mapping review record before any conversion is scheduled.
- Pinned pipeline models and verified source/reference/stem hashes.

After an actual voice converter returns each stem, call
`mix_converted_stems_wav(plan, {stem_id: converted_path}, output_path)`.
Converted stems must preserve the exact original frame count, sample rate and
channel count. Duration drift is rejected, because it would misalign overlaps.
Source and enrollment mutations invalidate the reviewed plan.

Mixing places stems at their reviewed offsets and preserves the full source
timeline. Two bounded-memory passes compute a summed peak and apply one uniform
gain only when needed to avoid PCM16 clipping. `AudioResult.applied_gain` reports
that adjustment. Original/background audio is not automatically added; gaps
between provided stems are silent. Preserve ambience using a future explicit
background-stem contract rather than silently mixing the original voices back in.

## Next integration steps

1. Extend the implemented per-line dialogue jobs with durable assembly history
   and a dialogue MCP contract. Store conversion mapping reviews and enrollment
   evidence locally before scheduling recorded-audio conversion.
2. Qualify dialogue lines generated through the pinned remote TTS scheduler;
   current UI/API tests use fixtures, not GPU voice generations.
3. Implement Pod diarization, overlapping-source separation, and voice-conversion
   adapters in isolated runtimes. Pin and verify their artifacts and licenses;
   the user requested ungated alternatives on 2026-09-30, so do not proceed with
   gated pyannote downloads. See [SPEECH_MODELS.md](SPEECH_MODELS.md).
4. Preserve timing through real conversion and measure memory/latency on one-hour
   recordings. Add source ambience support and robust resampling if needed.
5. Add UI and MCP tools only against the real durable job contracts.
6. Run GPU smoke tests and human listening tests for English, Urdu, voice identity,
   intelligibility, overlaps, artifacts, and full-duration timing; record actual
   GPU cost and persistent storage before publishing capacity/cost claims.
