# Phase 4 design — Chatterbox runtime & the emotion/tone mapping problem

Written 2026-08-10. Companion to [ROADMAP.md](ROADMAP.md)'s Phase 4 entry. This is a design doc, not
a build log — nothing in `app/inference/runtimes/`, `pyproject.toml`'s `chatterbox` extra, or
`app/jobs/direction.py`'s capability table changes in the session that wrote this file. The only code
that landed alongside this doc is the IR taxonomy addition described in §9.

## 1. Problem statement

Speech Direction's IR (`backend/app/domain/direction.py`) carries four fields that describe *how* a
segment should be delivered: `emotion` (9 discrete values as of this doc — see §9), `tone` (5 values),
`intensity`, and `energy` (both `Level`: LOW/MEDIUM/HIGH). Today every real runtime (VoxCPM) declares
all four **IGNORED** — it takes no such conditioning, and the capability chip says so honestly
(`app/jobs/direction.py`'s `_VOXCPM_FIELDS`).

Chatterbox is the one model in this project's catalog that could change that — but it only exposes
**two** continuous acoustic knobs: `exaggeration` (0.0–1.0) and `cfg_weight` (0.0–1.0). Naively mapping
each of the four IR fields independently onto those two knobs means they fight each other — the last
field applied silently wins, which is exactly the kind of silent, unpredictable behavior golden rule 5
exists to prevent. This document proposes one deterministic blend (§5) instead, and states plainly
which fields stay IGNORED because there's nothing honest to map them to yet.

## 2. What already exists

This is not a from-scratch design — Chatterbox is a partially-researched stub already sitting in the
codebase:

- **`backend/app/inference/catalog.py`** (~line 155): `CHATTERBOX_ML_V3 = ModelSpec(...)` —
  `id="chatterbox_ml_v3"`, `runtime=RuntimeKind.CHATTERBOX`, `license=License.MIT` (the comment there
  cites cross-checking against the HF model card, GitHub, and PyPI — `chatterbox-tts 0.1.7` — so §6
  restates this rather than re-verifying it), pinned `hf_revision`, `vram_mb=6000` (**provisional,
  unmeasured** — no `phase_a_verified=True`), `params={"exaggeration": {0.0-1.0, default 0.5},
  "cfg_weight": {0.0-1.0, default 0.5}}` ("R2 confirms the real parameter names and ranges" per the
  existing comment). Its `languages` tuple (Hindi/Devanagari, English/Latin) does **not** set
  `verified=True` on either cell.
- **`backend/app/inference/spec.py`**: `RuntimeKind.CHATTERBOX` already exists in the enum (line 42).
  `ModelSpec.supports()` (lines 187–197) returns `False` for any `LanguageSupport` cell that isn't
  `verified=True` — by design, per that method's own docstring ("Unverified cells return False. A
  model may not be routed to on the strength of a README claim."). Consequence: `domain/routing.py`'s
  `resolve()` **cannot route to Chatterbox today**, catalog entry notwithstanding. This is the system
  working as designed — the same gate VoxCPM had to clear — not a bug to route around.
- **`backend/app/config.py`**: `chatterbox_python: str = ""` (line 66) already exists and is already
  wired into `Settings.interpreters()` (line 153–154) exactly like `voxcpm_python`. **Zero config-layer
  work is needed** for Phase 4b.
- **`backend/app/inference/factory.py`**: `make_worker_factory`'s `factory()` closure already raises a
  clear `ValueError(f"no interpreter configured for runtime {runtime.value!r}")` when
  `VCS_CHATTERBOX_PYTHON` isn't set (lines 48–54). This is the exact generic mechanism a live pod
  session already exercised for a different runtime this same day, confirming it works.
- **`backend/app/inference/runtimes/__init__.py`**: `make_backend(runtime: str) -> RuntimeBackend`
  (lines 63–84) dispatches by string; only `"voxcpm"` and `"fake"` branches exist. Phase 4b needs one
  new branch:
  ```python
  if runtime == "chatterbox":
      from .chatterbox import ChatterboxBackend
      return ChatterboxBackend()
  ```
  The `RuntimeBackend` Protocol it returns (lines 23–60): `runtime: str`, `loaded_model_id: str | None`,
  `load(model_id, hf_repo, hf_revision) -> float` (seconds), `synth(*, text, reference_audio,
  output_path, params, sample_rate, reference_text=None) -> dict` (returns
  `{duration_sec, gen_time_sec, sample_rate}` only — **never** a waveform), `unload() -> None`.
- **`backend/app/inference/runtimes/voxcpm.py`** is the template a `ChatterboxBackend` should mirror:
  pinned-revision `snapshot_download` at load, warm-once with a bundled Apache-2.0 example clip
  (`_warm`, lines 70–85, best-effort inside `contextlib.suppress(Exception)`), `.get(key, default)`-
  defensive param reads in `synth()`, `soundfile.write` on a numpy array, `unload()` doing
  `gc.collect()` + `torch.cuda.empty_cache()` inside `contextlib.suppress(Exception)`.
- **`backend/pyproject.toml`** (lines 44–49): both the `chatterbox` *and* `voxcpm` extras are still
  empty, `TODO`-commented stubs — despite VoxCPM being fully pod-validated and shipping real audio.
  This means the extras are **not** how VoxCPM's real dependency actually got onto the pod; check
  `scripts/pod-bootstrap.sh` at Phase 4b time to see the real mechanism (a direct `uv pip install
  voxcpm` into its own venv, per that script) before assuming the `pyproject.toml` extra is
  load-bearing. Worth resolving the staleness either way, but it is not blocking Phase 4b.

## 3. Chatterbox API — confirmed findings (web search, 2026-08)

The multilingual variant this catalog entry targets:

```python
from chatterbox.mtl_tts import ChatterboxMultilingualTTS
model = ChatterboxMultilingualTTS.from_pretrained(device="cuda", t3_model="v3")  # "v3" matches "Multilingual v3"
wav = model.generate(
    text, audio_prompt_path=<ref wav>, language_id=<lang code>,
    exaggeration=<float>, cfg_weight=<float>,
)  # -> torch tensor
```

- Sample rate is **fixed at 24000 Hz** across the Chatterbox family (`model.sr`), unlike VoxCPM's
  model-reported-but-effectively-fixed 48000 Hz. `synth()` must report whatever was actually written —
  never silently resample — same discipline VoxCPM's own comment states (`voxcpm.py` lines 11–12).
- The tensor needs `.squeeze().cpu().numpy()` before `sf.write`, to match the one existing backend's
  `soundfile`-on-numpy pattern rather than introducing a second save path (`torchaudio.save`) for a
  future third backend to have to choose between. State this choice explicitly in the implementation,
  don't silently pick.
- **Language plumbing gap, confirmed by reading the wire protocol directly**: `SynthRequest`
  (`app/inference/protocol.py`, lines 101–120) has **no `language` field** — only `model_id, text,
  reference_audio, output_path, reference_text, params, sample_rate`. `SynthesizeParams`
  (`app/jobs/handlers/synthesize.py`, line 59) **does** carry `language: str`, one layer up, at
  enqueue time. Chatterbox's `synth()` needs a `language_id` it currently has no path to receive.
  Phase 4a's render layer (§5) should inject it into the `params` dict — e.g.
  `params["language_id"] = <mapped code>` — read via `params.get("language_id", ...)` inside
  `ChatterboxBackend.synth()`, matching `voxcpm.py`'s existing `.get()`-defensive style. The exact
  code Chatterbox's `language_id` expects (e.g. `"hi"`/`"en"`) vs. this project's own
  `domain.language.LanguageCode` values needs a one-time mapping check against the Chatterbox docs at
  implementation time — not assumed identical.
- Sources: [pypi.org/project/chatterbox-tts](https://pypi.org/project/chatterbox-tts/),
  [github.com/resemble-ai/chatterbox](https://github.com/resemble-ai/chatterbox),
  [huggingface.co/ResembleAI/chatterbox](https://huggingface.co/ResembleAI/chatterbox),
  [huggingface.co/spaces/ResembleAI/Chatterbox-Multilingual-TTS-V3](https://huggingface.co/spaces/ResembleAI/Chatterbox-Multilingual-TTS-V3),
  [github.com/davidbrowne17/chatterbox-streaming](https://github.com/davidbrowne17/chatterbox-streaming)
  — this last one documents the load-bearing interaction quoted in §5: *"higher exaggeration tends to
  speed up speech; reducing cfg_weight helps compensate with slower, more deliberate pacing."*

## 4. Why a blend table, not per-field overwrites

`app/jobs/direction.py`'s existing VoxCPM mapping (`_VOXCPM_CFG_BY_INTENSITY`, `_SPEED_BY_RATE`) is
the established style: small, explicit, readable dicts — not a formula with tunable coefficients. The
problem Chatterbox introduces is that VoxCPM had exactly one acoustic field (`intensity`) mapping to
exactly one knob (`cfg_value`); Chatterbox has two knobs and four candidate fields. §5 proposes one
deterministic function per knob, each fed by a *disjoint* subset of fields where possible, so no two
fields compete to set the same knob.

## 5. The mapping design

**`exaggeration`** — base value from `(intensity, energy)`, nudged by emotion "arousal":

```python
_CHATTERBOX_EXAGGERATION_BASE: dict[tuple[Level, Level], float] = {
    # (intensity, energy) -> base exaggeration
    (Level.LOW, Level.LOW): 0.25,      (Level.LOW, Level.MEDIUM): 0.35,    (Level.LOW, Level.HIGH): 0.45,
    (Level.MEDIUM, Level.LOW): 0.35,   (Level.MEDIUM, Level.MEDIUM): 0.5,  (Level.MEDIUM, Level.HIGH): 0.6,
    (Level.HIGH, Level.LOW): 0.45,     (Level.HIGH, Level.MEDIUM): 0.6,    (Level.HIGH, Level.HIGH): 0.75,
}
_EXAGGERATION_AROUSAL_DELTA = 0.1  # ANGRY/EXCITED: +delta; CALM/SAD/ANXIOUS: -delta; clamp [0, 1]
```

The up/down emotion grouping reuses the exact reasoning `direction_analyze.py`'s `_determine_energy`
already establishes for the IR's own `energy` field (ANGRY/EXCITED up, CALM/SAD/ANXIOUS down — see
this project's `direction_analyze.py`, updated 2026-08-10 alongside this doc). ANXIOUS is placed on
the "down" side as a judgment call: anxious delivery reads as withdrawn/tense rather than "leaned
into" the way anger or excitement does — flagged in §10 as wanting a by-ear check once a real backend
exists, since anxious speech can also read as fast/tense, which risks being *already* covered by
`cfg_weight`'s pacing axis (double-counting risk, noted so it isn't silently ignored).

**`cfg_weight`** — driven by `rate` only, independent of the fields above, directly following the
cited exaggeration-speeds-up-speech / cfg_weight-slows-it-down relationship (§3):

```python
_CHATTERBOX_CFG_WEIGHT_BY_RATE: dict[Rate, float] = {
    Rate.SLOW: 0.6, Rate.NORMAL: 0.5, Rate.FAST: 0.35,
}
```

**`tone` (including `NARRATIVE`) — recommend IGNORED for Chatterbox v1.** Two reasons, stated plainly
rather than left ambiguous: (1) nothing populates `tone` today — the analyzer always emits
`Tone.NEUTRAL` (§9) — so there is nothing live to map even if a knob existed; (2) mapping tone onto
the same two knobs `emotion`/`intensity`/`energy` already drive would reintroduce the exact
field-collision problem this doc exists to avoid.

**Every field's capability row**, in the existing `_VOXCPM_FIELDS`-style tuple format
(`(field, Support, rationale)`):

| Field | Support | Rationale |
|---|---|---|
| `segmentation` | HONORED | Same as VoxCPM — each segment is a separate synthesis pass. |
| `pause_after` | HONORED | Same as VoxCPM — silence insertion is renderer-side, model-agnostic. |
| `rate` | APPROXIMATED | Drives `cfg_weight` pre-synth *and* the existing post-synth ffmpeg `atempo` still applies on top — belt-and-suspenders, matching how VoxCPM already treats rate as a real, independent DSP step rather than trusting the model's own pacing alone. |
| `emphasis` | APPROXIMATED | Conveyed via punctuation/casing in the passed-through text sent to `model.generate()`, same as VoxCPM — **only true if the implementation confirms the direction-analyzed text, with its casing/asterisks, is literally what's sent**, not a de-marked-up version; verify this at implementation time rather than assuming it transfers. |
| `intensity` | APPROXIMATED | Contributes to the blended `exaggeration` base (§5) — not a dedicated knob. |
| `energy` | APPROXIMATED | Contributes to the blended `exaggeration` base (§5) — not a dedicated knob. |
| `emotion` | APPROXIMATED | Nudges `exaggeration` by arousal grouping (§5) — not true per-category conditioning; Chatterbox has no "angry" input, only a continuous scalar. |
| `tone` | IGNORED | No analyzer-derived signal yet (§9), and Chatterbox's two knobs are already spoken for by intensity/energy/emotion/rate — see rationale above. |

## 6. VRAM / residency risk

`backend/app/config.py`: `budget_mb=16_000`, `max_workers=2` (lines 60–61), sized for a 24GB card.
VoxCPM measures **7300MB** resident (`catalog.py`'s `VOXCPM2.vram_mb`, comment marks it MEASURED on an
A5000). Chatterbox's `vram_mb=6000` is **provisional, unmeasured** — no `phase_a_verified` flag.
7300+6000=13300 fits under 16000 nominally, but this project's pods vary — the RTX 4000 Ada 20GB pod
used for this session's own validation run has materially less headroom than the 24GB card these
defaults assume (see `CLAUDE.md`'s own VRAM-budget note). **Do not bake a two-resident-model scheduling
assumption into `max_workers`/`budget_mb` until Phase 4c measures Chatterbox's real footprint on the
actual pod hardware in use at the time.**

## 7. Licensing

MIT, already verified — `catalog.py`'s own comment cross-checks the HF model card, GitHub, and PyPI
(`chatterbox-tts 0.1.7`). Golden rule 6 is already satisfied here; this section exists only to restate
that fact for a reader who hasn't seen `catalog.py`, not to re-litigate it.

## 8. Routing / verification gate

Chatterbox needs its own Phase-A-style validation pass — real GPU, measured CER via Whisper against a
reference transcript, speaker cosine similarity, RTF — before `verified=True` can be set on its
`LanguageSupport` cells. The thresholds already exist and don't need inventing:
`LanguageSupport.meets_gate()` (`app/inference/spec.py`, lines 125–129): `cer < 0.25 and
speaker_cosine > 0.70`. Until measured and passing, Chatterbox stays correctly unroutable — the same
gate VoxCPM cleared, not a new mechanism, and not a bug to patch around by pre-emptively flipping
`verified=True` (`catalog.py`'s own module docstring already warns against exactly this).

## 9. Landed alongside this doc — IR taxonomy expansion

Two enum values were added to `backend/app/domain/direction.py` in the same change that added this
doc, ahead of any Chatterbox runtime work, because they're the direct answer to the question that
triggered this whole design pass ("should there be an anger/anxiety/commentary option"):

- **`Emotion.ANXIOUS`** — detected today by the heuristic analyzer
  (`backend/app/domain/direction_analyze.py`), same as `ANGRY`/`SAD`/etc. Inert until some renderer
  honors `emotion` (this doc's §5 is that renderer, once built).
- **`Tone.NARRATIVE`** — the narrator/sports-commentator delivery style the owner asked about.
  **Not** analyzer-derived — the analyzer never sets `tone` at all today (every segment gets
  `Tone.NEUTRAL` by dataclass default), and no detection heuristic was invented just to fill the enum.
  This is documented directly in `direction_analyze.py`'s module docstring rather than left as a silent
  gap.

Both are purely additive to a frozen contract module — no field or signature changes, nothing
iterates these enums exhaustively (verified by grep), and both are honestly declared IGNORED by
every current renderer's capability chip until a real mapping (like §5) exists and ships.

## 10. Open questions requiring explicit owner sign-off

- **`Tone.NARRATIVE` vs. `Tone.COMMENTARY` naming.** Chosen: `NARRATIVE`, reasoning that "commentary"
  reads as an appended opinion/critique where the actual ask was a delivery *style*. Cheap to rename
  before anything depends on the string value on the wire.
- **The `ANXIOUS`/`SAD` lexicon overlap and the specific Roman Urdu/Hindi word choices** — starting
  suggestions only, flagged in `direction_analyze.py` as unverified against native speakers, same
  caveat the rest of that lexicon already carries.
- **Confirm the Tone-deferred decision** — i.e. that nobody wants a first-pass, necessarily-shaky
  tone-detection heuristic instead of leaving `tone` honestly unpopulated for now.
- **The exaggeration/cfg_weight blend numbers in §5** are a starting point, not measured against real
  Chatterbox output. Once `ChatterboxBackend` exists, they want a quick by-ear sanity pass on the pod
  — the same "owner's ear is authoritative" precedent already used to tune VoxCPM's `cfg_value` range
  (see `catalog.py`'s comment on `VOXCPM2`).
- **Whether `tone` ever gets its own dedicated detection design** (a "Phase 5"?) now that a value with
  real product intent (`NARRATIVE`) exists in the enum with no current path to ever populate itself.

## 11. Sequencing

- **Phase 4a** (CPU-only, no GPU): land `_CHATTERBOX_FIELDS` + the §5 blend functions in
  `app/jobs/direction.py`, the `language_id` plumbing change in `synthesize.py`/`direction.py`'s
  `render()`, and all associated tests — including repointing
  `backend/tests/test_direction_capability.py`'s `_NON_VOXCPM` fixture (currently
  `CATALOG.by_runtime(RuntimeKind.CHATTERBOX)[0]`, line 40) at a *different*, still-generic spec, since
  `test_generic_capability_claims_no_acoustics`/`test_render_generic_emits_no_model_params` currently
  rely on Chatterbox being the generic example — a known, planned breaking change at this step, not a
  surprise. `FakeScheduler`-based render tests mirror `backend/tests/test_jobs_direction_synthesize.py`'s
  existing pattern.
- **Phase 4b** (GPU needed): real `app/inference/runtimes/chatterbox.py` (`ChatterboxBackend`), the
  `make_backend()` dispatch branch, resolve the `pyproject.toml` extra staleness (§2), pod install +
  smoke test. `ChatterboxBackend.synth()` correctness is only verifiable via `pytest -m gpu` on the
  pod — `runtimes/` code is deliberately excluded from the CPU suite
  (`tests/test_contracts.py::test_no_torch_outside_runtimes` is the one test that *does* touch that
  directory, specifically to prove torch isn't imported elsewhere).
- **Phase 4c** (GPU needed): Phase-A-style validation against §8's gate, flip `verified=True` only on
  cells that measure a pass, delete any that fail — never pre-emptively.
