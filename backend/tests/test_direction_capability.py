"""
Contract + behavior tests for the Speech-Direction renderer/capability layer
(`app/jobs/direction.py`).

Two things are guarded here:

1. **Honesty of the capability report** — VoxCPM must declare emotion/tone/energy
   IGNORED (it takes no such conditioning) and never quietly claim to honor a
   knob it can't act on. That declaration is the whole reason Direction is a
   real feature and not the Style-Exaggeration defect again (golden rule 5).
2. **IGNORED fields never leak into params** — `render()` must not smuggle an
   emotion into the synth knobs just because the analyzer detected one.

`test_direction_fields_cover_ir` is the sync test referenced from
`jobs/direction.py`: the capability report must cover exactly `DIRECTION_FIELDS`,
and every acoustic IR field on `DirectedSegment` must be represented.
"""

from __future__ import annotations

from app.domain.direction import (
    DirectedSegment,
    DirectionPlan,
    DirectionSummary,
    Emotion,
    Level,
    Rate,
    Tone,
)
from app.inference.catalog import CATALOG
from app.inference.spec import RuntimeKind
from app.jobs.direction import (
    DIRECTION_FIELDS,
    Support,
    capability_for,
    render,
)

_VOXCPM = CATALOG.require("voxcpm2")
_CHATTERBOX = CATALOG.require("chatterbox_ml_v3")
#: A runtime with no Direction renderer at all — must stay generic, not
#: Chatterbox (which got its own _CHATTERBOX_FIELDS table in Phase 4a).
_NON_VOXCPM = CATALOG.by_runtime(RuntimeKind.F5)[0]


def _plan(*segments: DirectedSegment) -> DirectionPlan:
    return DirectionPlan(
        language="ur",
        source_script="latin",
        segments=tuple(segments),
        summary=DirectionSummary(),
    )


# ── Capability report ────────────────────────────────────────────────────────


def test_direction_fields_cover_ir():
    """The report covers exactly DIRECTION_FIELDS, and every acoustic IR field
    on DirectedSegment is represented so none can be silently dropped."""
    report = capability_for(_VOXCPM)
    assert {f.field for f in report.fields} == set(DIRECTION_FIELDS)

    # Every acoustic/prosodic field the IR carries must appear in the report.
    ir_acoustic = {"emotion", "tone", "intensity", "energy", "rate", "emphasis"}
    assert ir_acoustic <= set(DIRECTION_FIELDS)
    # Plus the two structural fields segmentation implies.
    assert {"segmentation", "pause_after"} <= set(DIRECTION_FIELDS)


def test_voxcpm_capability_is_honest():
    report = capability_for(_VOXCPM)
    by_field = {f.field: f.support for f in report.fields}

    # VoxCPM takes no emotion/tone/energy conditioning — must say so.
    assert by_field["emotion"] is Support.IGNORED
    assert by_field["tone"] is Support.IGNORED
    assert by_field["energy"] is Support.IGNORED
    # What it can really do.
    assert by_field["segmentation"] is Support.HONORED
    assert by_field["pause_after"] is Support.HONORED
    assert by_field["rate"] is Support.HONORED
    # Correlated-but-not-a-real-knob → approximated, not honored.
    assert by_field["intensity"] is Support.APPROXIMATED
    assert by_field["emphasis"] is Support.APPROXIMATED

    assert report.model_id == _VOXCPM.id
    assert set(report.ignored) == {"emotion", "tone", "energy"}


def test_generic_capability_claims_no_acoustics():
    """A runtime with no Direction renderer honors only what any TTS trivially
    can (segmentation, pauses, post-synth tempo) and IGNORES everything acoustic
    — never HONORED without a renderer that proves it."""
    report = capability_for(_NON_VOXCPM)
    by_field = {f.field: f.support for f in report.fields}

    assert by_field["segmentation"] is Support.HONORED
    assert by_field["pause_after"] is Support.HONORED
    assert by_field["rate"] is Support.HONORED
    for acoustic in ("emphasis", "intensity", "emotion", "tone", "energy"):
        assert by_field[acoustic] is Support.IGNORED


def test_chatterbox_capability_is_honest():
    """Chatterbox has real acoustic knobs (exaggeration/cfg_weight) so several
    fields are APPROXIMATED rather than IGNORED — but it takes no discrete tone
    input, and nothing populates `tone` yet either, so tone stays IGNORED."""
    report = capability_for(_CHATTERBOX)
    by_field = {f.field: f.support for f in report.fields}

    assert by_field["segmentation"] is Support.HONORED
    assert by_field["pause_after"] is Support.HONORED
    assert by_field["rate"] is Support.APPROXIMATED
    assert by_field["emphasis"] is Support.APPROXIMATED
    assert by_field["intensity"] is Support.APPROXIMATED
    assert by_field["energy"] is Support.APPROXIMATED
    assert by_field["emotion"] is Support.APPROXIMATED
    assert by_field["tone"] is Support.IGNORED

    assert report.model_id == _CHATTERBOX.id
    assert set(report.ignored) == {"tone"}


# ── Rendering ────────────────────────────────────────────────────────────────


def test_render_maps_intensity_to_cfg_in_range():
    plan = _plan(
        DirectedSegment(text="A.", index=0, intensity=Level.LOW),
        DirectedSegment(text="B.", index=1, intensity=Level.MEDIUM),
        DirectedSegment(text="C.", index=2, intensity=Level.HIGH),
    )
    out = render(plan, _VOXCPM)
    cfgs = [s.params["cfg_value"] for s in out.segments]

    # Monotonic with intensity, and every value inside the spec's declared range.
    assert cfgs[0] < cfgs[1] < cfgs[2]
    lo = _VOXCPM.params["cfg_value"]["minimum"]
    hi = _VOXCPM.params["cfg_value"]["maximum"]
    assert all(lo <= c <= hi for c in cfgs)


def test_render_ignored_fields_do_not_leak():
    """A detected emotion must not smuggle itself into the synth params: the
    only key VoxCPM gets is the one it declares (cfg_value)."""
    plan = _plan(
        DirectedSegment(
            text="Chala jao!",
            index=0,
            emotion=Emotion.ANGRY,
            tone=Tone.FIRM,
            energy=Level.HIGH,
        )
    )
    out = render(plan, _VOXCPM)
    assert set(out.segments[0].params) == {"cfg_value"}


def test_render_rate_maps_to_speed():
    plan = _plan(
        DirectedSegment(text="A.", index=0, rate=Rate.SLOW),
        DirectedSegment(text="B.", index=1, rate=Rate.NORMAL),
        DirectedSegment(text="C.", index=2, rate=Rate.FAST),
    )
    speeds = [s.speed for s in render(plan, _VOXCPM).segments]
    assert speeds[0] < speeds[1] < speeds[2]
    assert speeds[1] == 1.0  # NORMAL is a true no-op


def test_render_maps_intensity_energy_to_chatterbox_exaggeration():
    """Higher (intensity, energy) should yield a higher blended exaggeration —
    the same monotonic-with-intensity shape test_render_maps_intensity_to_cfg_in_range
    checks for VoxCPM's cfg_value, on Chatterbox's own knob."""
    plan = _plan(
        DirectedSegment(text="A.", index=0, intensity=Level.LOW, energy=Level.LOW),
        DirectedSegment(text="B.", index=1, intensity=Level.MEDIUM, energy=Level.MEDIUM),
        DirectedSegment(text="C.", index=2, intensity=Level.HIGH, energy=Level.HIGH),
    )
    out = render(plan, _CHATTERBOX)
    exaggerations = [s.params["exaggeration"] for s in out.segments]

    assert exaggerations[0] < exaggerations[1] < exaggerations[2]
    lo = _CHATTERBOX.params["exaggeration"]["minimum"]
    hi = _CHATTERBOX.params["exaggeration"]["maximum"]
    assert all(lo <= e <= hi for e in exaggerations)


def test_render_arousal_emotion_nudges_chatterbox_exaggeration():
    """ANGRY should push exaggeration up and CALM should pull it down, relative
    to NEUTRAL at the same (intensity, energy) — proving the emotion-arousal
    nudge in _EXAGGERATION_AROUSAL_DELTA actually applies."""
    base = _plan(DirectedSegment(text="A.", index=0, intensity=Level.MEDIUM, energy=Level.MEDIUM))
    angry = _plan(
        DirectedSegment(
            text="A.", index=0, intensity=Level.MEDIUM, energy=Level.MEDIUM, emotion=Emotion.ANGRY
        )
    )
    calm = _plan(
        DirectedSegment(
            text="A.", index=0, intensity=Level.MEDIUM, energy=Level.MEDIUM, emotion=Emotion.CALM
        )
    )

    base_exagg = render(base, _CHATTERBOX).segments[0].params["exaggeration"]
    angry_exagg = render(angry, _CHATTERBOX).segments[0].params["exaggeration"]
    calm_exagg = render(calm, _CHATTERBOX).segments[0].params["exaggeration"]

    assert calm_exagg < base_exagg < angry_exagg


def test_render_rate_maps_to_chatterbox_cfg_weight():
    """SLOW -> higher cfg_weight (more deliberate pacing), FAST -> lower —
    inverse of exaggeration's speed-it-up effect, per the cited Chatterbox
    docs relationship."""
    plan = _plan(
        DirectedSegment(text="A.", index=0, rate=Rate.SLOW),
        DirectedSegment(text="B.", index=1, rate=Rate.NORMAL),
        DirectedSegment(text="C.", index=2, rate=Rate.FAST),
    )
    cfg_weights = [s.params["cfg_weight"] for s in render(plan, _CHATTERBOX).segments]
    assert cfg_weights[0] > cfg_weights[1] > cfg_weights[2]

    lo = _CHATTERBOX.params["cfg_weight"]["minimum"]
    hi = _CHATTERBOX.params["cfg_weight"]["maximum"]
    assert all(lo <= c <= hi for c in cfg_weights)


def test_render_chatterbox_ignored_tone_does_not_leak():
    """Tone is IGNORED for Chatterbox (no analyzer signal, no free knob) — a
    FIRM tone on the segment must not appear anywhere in the emitted params."""
    plan = _plan(DirectedSegment(text="Chala jao!", index=0, tone=Tone.FIRM))
    out = render(plan, _CHATTERBOX)
    assert set(out.segments[0].params) == {"exaggeration", "cfg_weight", "language_id"}


def test_render_chatterbox_injects_language_id():
    """SynthRequest has no language field; DirectionPlan does. render() must
    carry it through in params so a future ChatterboxBackend.synth() can read
    it — see the render() docstring for why this isn't in DIRECTION_FIELDS."""
    plan = _plan(DirectedSegment(text="Hello.", index=0))
    out = render(plan, _CHATTERBOX)
    assert out.segments[0].params["language_id"] == plan.language


def test_render_generic_emits_no_model_params():
    """A runtime with no Direction renderer still segments (speed + pause) but
    emits no model-specific params — no cfg_value it can't consume."""
    plan = _plan(DirectedSegment(text="Hello.", index=0, intensity=Level.HIGH))
    out = render(plan, _NON_VOXCPM)
    assert len(out.segments) == 1
    assert out.segments[0].params == {}
    assert out.segments[0].pause_after_ms == plan.segments[0].pause_after_ms


def test_render_empty_plan_yields_no_segments():
    out = render(_plan(), _VOXCPM)
    assert out.segments == ()
    # The capability report is still produced — the chip shows even with no text.
    assert out.capability.model_id == _VOXCPM.id
