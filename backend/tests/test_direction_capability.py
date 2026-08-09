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
_NON_VOXCPM = CATALOG.by_runtime(RuntimeKind.CHATTERBOX)[0]


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
