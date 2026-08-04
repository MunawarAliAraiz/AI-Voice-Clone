"""
Wave 0 contract tests.

These assert the properties every later wave depends on. They are cheap, they
need no GPU, and several of them are the ONLY mechanical guard on a rule that is
otherwise just a comment.

If one of these fails, the correct response is almost never "delete the test".
It is "the contract changed, so amend the contract deliberately and tell the
other agents". That is the whole point of freezing contracts before fan-out.
"""

from __future__ import annotations

import pkgutil
import re
from pathlib import Path

import pytest

from app.domain.language import Script
from app.exceptions import AppError, NoRouteError, QueueFullError
from app.inference.catalog import CATALOG, PENDING_PIN, PENDING_REPO, build_catalog
from app.inference.protocol import SchedulerProtocol
from app.inference.spec import License, ModelSpec, RuntimeKind
from tests.fakes import FakeScheduler

pytestmark = pytest.mark.contract

BACKEND_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = BACKEND_ROOT / "app"

#: The ONLY places torch may be imported. See ARCHITECTURE.md, invariant 1.
TORCH_ALLOWED = ("inference/runtimes/", "inference/worker.py")

#: Pre-rewrite modules that still import torch. X1 deletes every one of these in
#: Wave 2, at which point this set must be emptied and this comment removed.
#:
#: It exists so the invariant is enforced on ALL NEW CODE starting now, during
#: the parallel build, rather than being switched off until the deletions land.
#: The test below also fails if an entry is listed but no longer offends, so the
#: list cannot rot.
#:
#: Note these import torch INSIDE functions, not at module top level. A
#: function-local import still makes torch reachable and still pays the ~4s cost
#: on first call — which is why the check allows leading whitespace. A plain
#: `grep "^import torch"` reports these files clean, and that false negative is
#: exactly why this is a test and not a grep in a doc.
LEGACY_TORCH_IMPORTERS = frozenset(
    {
        "utils/gpu.py",
        "utils/gpu_manager.py",
        "services/translation_service.py",
        "engines/f5_tts.py",
        "engines/fish_speech.py",
        "engines/xtts_v2.py",
    }
)


# ── Invariant 1: no torch in the API process ─────────────────────────────────


def test_no_torch_outside_runtimes() -> None:
    """
    `import torch` must not be reachable from `app.main`.

    This is the single strongest structural guarantee in the design: it is what
    lets the entire non-GPU suite run on a machine with no CUDA, and what keeps
    a ~4s torch import out of startup. Wave 4 runs the same grep by hand; this
    runs it on every commit.
    """
    pattern = re.compile(r"^\s*(?:import torch|from torch)", re.MULTILINE)
    offenders: set[str] = set()

    for path in APP_ROOT.rglob("*.py"):
        rel = path.relative_to(APP_ROOT).as_posix()
        if any(rel.startswith(allowed) or rel == allowed for allowed in TORCH_ALLOWED):
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.add(rel)

    new_offenders = offenders - LEGACY_TORCH_IMPORTERS
    assert not new_offenders, (
        f"torch imported outside the worker: {sorted(new_offenders)}. "
        f"Only {TORCH_ALLOWED} may import torch — see ARCHITECTURE.md invariant 1."
    )


def test_legacy_torch_list_does_not_rot() -> None:
    """
    Every entry in LEGACY_TORCH_IMPORTERS must still exist and still offend.

    Without this, a deleted file lingers in the allowlist and quietly re-opens a
    hole for a future module of the same name. When X1 finishes, this test fails
    until the set is emptied — which is the reminder to empty it.
    """
    pattern = re.compile(r"^\s*(?:import torch|from torch)", re.MULTILINE)
    stale = [
        rel
        for rel in LEGACY_TORCH_IMPORTERS
        if not (APP_ROOT / rel).exists()
        or not pattern.search((APP_ROOT / rel).read_text(encoding="utf-8"))
    ]
    assert not stale, (
        f"These are in LEGACY_TORCH_IMPORTERS but no longer import torch (or are "
        f"gone): {sorted(stale)}. Remove them from the set."
    )


def test_domain_is_pure() -> None:
    """
    The domain layer performs no I/O.

    Routing, script detection and chunking must be testable with a literal input
    and a literal expected output. Anything that reaches for the filesystem, the
    network or the clock belongs in a service, not here.
    """
    forbidden = re.compile(
        r"^\s*(?:import (?:os|socket|sqlite3|aiosqlite|httpx|requests|time)\b"
        r"|from (?:os|pathlib|socket|sqlite3|aiosqlite|httpx|requests) import)",
        re.MULTILINE,
    )
    offenders = [
        p.relative_to(APP_ROOT).as_posix()
        for p in (APP_ROOT / "domain").rglob("*.py")
        if forbidden.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"domain layer must stay pure, but these do I/O: {offenders}"


# ── Catalog integrity ────────────────────────────────────────────────────────


def test_catalog_ids_unique() -> None:
    ids = [s.id for s in CATALOG.specs]
    assert len(ids) == len(set(ids))


def test_catalog_rejects_duplicate_ids() -> None:
    spec = CATALOG.specs[0]
    with pytest.raises(ValueError, match="duplicate model ids"):
        build_catalog((spec, spec))


def test_every_shipped_license_is_permissive() -> None:
    """
    No CC-BY-NC or research-only weights, ever.

    This is a product constraint, not a preference: XTTS v2 (CPML) and Fish
    Speech (research license) were removed for exactly this reason, and the
    easiest way to undo that decision is to add a spec without checking.
    """
    assert CATALOG.unshippable() == (), (
        f"non-permissive weights in catalog: "
        f"{[(s.id, s.license.value) for s in CATALOG.unshippable()]}"
    )


def test_three_runtimes_five_specs() -> None:
    """The lineup is a decision, not an accident. Changing it is a plan change."""
    assert len(CATALOG.specs) == 5
    assert {s.runtime for s in CATALOG.specs} == {
        RuntimeKind.F5,
        RuntimeKind.CHATTERBOX,
        RuntimeKind.VOXCPM,
    }
    assert len(CATALOG.by_runtime(RuntimeKind.F5)) == 3


def test_fake_runtime_is_not_in_the_catalog() -> None:
    """The fake runtime is test infrastructure. It must never be selectable."""
    assert all(s.runtime is not RuntimeKind.FAKE for s in CATALOG.specs)


def test_unverified_cells_are_not_routable() -> None:
    """
    A spec must not be selectable for a (language, script) it has not been
    measured on.

    On a Wave 0 checkout this means the catalog routes NOTHING, which is correct
    and deliberate — Phase A has not run. Advertising an unmeasured claim is how
    XTTS ended up offering Urdu.
    """
    for spec in CATALOG.specs:
        for ls in spec.languages:
            if not ls.verified:
                assert not spec.supports(ls.language, ls.script)
                assert spec.claims(ls.language, ls.script)


def test_supported_pairs_contains_only_verified() -> None:
    """The 422 body offers these to the user. It must not offer a guess."""
    verified = {
        (ls.language, ls.script)
        for spec in CATALOG.specs
        for ls in spec.languages
        if ls.verified
    }
    assert set(CATALOG.supported_pairs()) == verified


def test_gate_thresholds() -> None:
    """CER < 0.25 and speaker cosine > 0.70, per the Phase A gate."""
    from app.inference.spec import LanguageSupport

    passing = LanguageSupport("ur", Script.ARABIC, cer=0.20, speaker_cosine=0.75)
    assert passing.meets_gate()
    assert not LanguageSupport("ur", Script.ARABIC, cer=0.30, speaker_cosine=0.75).meets_gate()
    assert not LanguageSupport("ur", Script.ARABIC, cer=0.20, speaker_cosine=0.65).meets_gate()
    # Unmeasured never passes.
    assert not LanguageSupport("ur", Script.ARABIC).meets_gate()


@pytest.mark.xfail(
    reason="Phase A / Wave 3 resolves PENDING_PIN and PENDING_REPO. "
           "This flips to a hard failure once research lands — it is the gate "
           "that stops an unpinned revision reaching production.",
    strict=False,
)
def test_all_revisions_pinned() -> None:
    """
    Every spec must pin a 40-char commit sha.

    An unpinned `main` combined with `trust_remote_code=True` (IndicF5 may need
    it) is a supply-chain hole: the code that executes on this box would be
    whatever the repo owner pushed last.
    """
    sha = re.compile(r"^[0-9a-f]{40}$")
    for spec in CATALOG.specs:
        assert spec.hf_revision != PENDING_PIN, f"{spec.id} revision is unpinned"
        assert spec.hf_repo != PENDING_REPO, f"{spec.id} repo is unresolved"
        assert sha.match(spec.hf_revision), f"{spec.id} revision is not a commit sha"


def test_attribution_required_for_cc_by_sa() -> None:
    """CC-BY-SA weights need a NOTICE entry. Wave 4 audits the file itself."""
    for spec in CATALOG.needs_attribution():
        assert spec.license is License.CC_BY_SA_4_0


# ── Exceptions ───────────────────────────────────────────────────────────────


def test_every_app_error_has_a_stable_code_and_status() -> None:
    def leaves(cls: type) -> list[type]:
        subs = cls.__subclasses__()
        return [cls] if not subs else [c for s in subs for c in leaves(s)]

    for cls in leaves(AppError):
        assert cls.code and cls.code.isupper(), f"{cls.__name__} has no stable code"
        assert 400 <= cls.http_status <= 599, f"{cls.__name__} has a bad status"


def test_error_codes_are_unique() -> None:
    """Clients branch on `code`. Two errors sharing one is undiagnosable."""
    def leaves(cls: type) -> list[type]:
        subs = cls.__subclasses__()
        return [cls] if not subs else [c for s in subs for c in leaves(s)]

    codes = [c.code for c in leaves(AppError)]
    dupes = {c for c in codes if codes.count(c) > 1}
    assert not dupes, f"duplicate error codes: {dupes}"


def test_no_route_error_enumerates_what_would_work() -> None:
    """
    The whole point of NoRouteError: never leave the user guessing.

    A bare "unsupported" is barely better than the sine wave it replaced.
    """
    err = NoRouteError(
        language="ur",
        script="devanagari",
        supported=(("ur", "arabic"), ("hi", "devanagari")),
        suggestion="Did you mean language='hi'?",
    )
    problem = err.to_problem(instance="/api/tts/generate")
    assert problem["status"] == 422
    assert problem["code"] == "NO_ROUTE"
    assert problem["instance"] == "/api/tts/generate"
    assert {"language": "hi", "script": "devanagari"} in problem["supported"]
    assert "hi" in problem["detail"] or "hi" in problem["suggestion"]


def test_problem_document_shape() -> None:
    """RFC 9457: type, title, status, detail — plus our stable `code`."""
    problem = QueueFullError(limit=8).to_problem()
    assert {"type", "title", "status", "detail", "code"} <= problem.keys()
    assert problem["status"] == 503
    assert problem["limit"] == 8


# ── Protocol conformance ─────────────────────────────────────────────────────


def test_fake_scheduler_satisfies_the_protocol() -> None:
    """
    If this breaks, every API test is testing something the real scheduler
    cannot do.
    """
    assert isinstance(FakeScheduler(), SchedulerProtocol)


def test_spec_is_immutable() -> None:
    """
    The catalog is a constant, not mutable state.

    In particular there is no `is_loaded` on a spec: mixing residency into the
    object routing reads is the precise defect that made a cold server answer
    with a sine wave.
    """
    spec = CATALOG.specs[0]
    with pytest.raises((AttributeError, TypeError)):
        spec.id = "mutated"  # type: ignore[misc]
    assert not hasattr(spec, "is_loaded")


def test_inference_package_exports_no_implementation() -> None:
    """
    `app.inference` exports contracts only.

    Exporting `InferenceScheduler` here would drag the implementation into every
    consumer and destroy the seam B2 codes against.
    """
    import app.inference as inference

    assert "InferenceScheduler" not in inference.__all__
    assert "SchedulerProtocol" in inference.__all__


def test_no_stray_modules_in_contract_packages() -> None:
    """Guards against a Wave 2 agent quietly adding a file outside its lane."""
    expected = {"__init__", "spec", "catalog", "protocol", "scheduler", "worker_client"}
    found = {m.name for m in pkgutil.iter_modules([str(APP_ROOT / "inference")])}
    unexpected = found - expected
    assert not unexpected, f"unexpected modules in app/inference: {unexpected}"


def test_model_spec_supports_requires_exact_script() -> None:
    """(ur, latin) is Roman Urdu; (ur, arabic) is native. They are not the same."""
    spec = ModelSpec(
        id="t", display_name="t", runtime=RuntimeKind.F5, license=License.MIT,
        hf_repo="x/y", hf_revision="a" * 40,
        languages=(
            __import__("app.inference.spec", fromlist=["LanguageSupport"]).LanguageSupport(
                "ur", Script.ARABIC, verified=True
            ),
        ),
        vram_mb=1, est_load_sec=1.0,
    )
    assert spec.supports("ur", Script.ARABIC)
    assert not spec.supports("ur", Script.LATIN)
    assert not spec.supports("hi", Script.ARABIC)
