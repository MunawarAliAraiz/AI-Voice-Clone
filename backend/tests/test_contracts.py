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

from app.domain.language import Script, profile_text
from app.domain.routing import resolve
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
#: Emptied once the predecessor engine/service/util layer was deleted (B2). The
#: invariant now stands with no grandfathered exceptions: torch is reachable
#: ONLY from `inference/runtimes/` and `inference/worker.py`.
LEGACY_TORCH_IMPORTERS: frozenset[str] = frozenset()


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


def test_domain_never_imports_inference() -> None:
    """
    The dependency arrow runs `inference -> domain`, one way.

    Domain is the pure layer; it may not know an inference layer exists. The
    first draft of `routing.py` imported `ModelCatalog` directly, which was a
    circular import (`inference.spec` already imports `domain.language.Script`)
    AND a layering inversion. `ports.CatalogView` exists to break it.
    """
    pattern = re.compile(r"^\s*(?:import app\.inference|from (?:app\.)?\.*inference)", re.MULTILINE)
    offenders = [
        p.relative_to(APP_ROOT).as_posix()
        for p in (APP_ROOT / "domain").rglob("*.py")
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"domain must not import inference: {offenders}. "
        f"Depend on app.domain.ports.CatalogView instead."
    )


def test_packages_import_in_any_order() -> None:
    """
    Importing `app.inference` first must work as well as `app.domain` first.

    A cycle can hide behind import order: the original defect passed the whole
    suite because every test happened to import `app.domain` first, and only
    blew up when something reached for `app.inference` first. Run both orders in
    a clean interpreter so sys.modules cannot mask it.
    """
    import subprocess
    import sys

    for first, second in (("app.inference", "app.domain"), ("app.domain", "app.inference")):
        proc = subprocess.run(
            [sys.executable, "-c", f"import {first}; import {second}"],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            f"importing {first} before {second} failed:\n{proc.stderr}"
        )


def test_model_catalog_satisfies_catalog_view() -> None:
    """
    The concrete catalog must structurally satisfy the domain's port.

    If this breaks, `resolve()` type-checks against something no real catalog
    provides, and the failure surfaces at runtime in Wave 2 instead of here.
    """
    from app.domain.ports import CatalogView

    assert isinstance(CATALOG, CatalogView)


def test_catalog_ids_unique() -> None:
    ids = [s.id for s in CATALOG.specs]
    assert len(ids) == len(set(ids))


def test_catalog_rejects_duplicate_ids() -> None:
    spec = CATALOG.specs[0]
    with pytest.raises(ValueError, match="duplicate model ids"):
        build_catalog((spec, spec))


def test_no_research_only_or_worse_weights() -> None:
    """
    RESEARCH_ONLY (or anything stricter) never appears, catalog or not.

    This is the one tier golden rule 6's 2026-08-15 amendment did NOT relax:
    XTTS v2 (CPML) and Fish Speech (research license) were removed for this
    reason, and Higgs Audio v3's terms are stricter than CC-BY-NC — that's
    exactly why it's still unintegrated despite being in the bake-off. The
    easiest way to undo any of that is to add a spec without checking.
    """
    for spec in CATALOG.specs:
        assert spec.license.personal_use_ok, (
            f"{spec.id} carries {spec.license.value}, stricter than personal "
            "use permits"
        )


def test_non_commercial_weights_are_badged_and_documented() -> None:
    """
    CC-BY-NC specs (golden rule 6, amended 2026-08-15) are legal in the
    catalog for the owner's personal use behind VCS_API_KEY, but every one
    must be identifiable as non-commercial by license alone — `unshippable()`
    is what a commercial-audit pass reads, and it must never silently include
    a spec nobody flagged.
    """
    for spec in CATALOG.unshippable():
        assert spec.license is License.CC_BY_NC, (
            f"{spec.id} is unshippable under {spec.license.value}, which is "
            "not the one tier personal-use is meant to permit"
        )


def test_experimental_specs_have_a_caveat() -> None:
    """
    `ModelSpec.notes` is maintainer prose (env var names, RuntimeError
    semantics, doc paths) and must never reach a user. `caveat` is the one
    field the picker renders, so every spec that opts into experimental
    listing must set a short one — this is what stopped the picker from
    dumping voxcpm2_urdu_lora's full engineering note into the UI.
    """
    for spec in CATALOG.specs:
        if spec.experimental_listing:
            assert spec.caveat, f"{spec.id} is experimental but has no caveat"
            assert len(spec.caveat) <= 140, (
                f"{spec.id}'s caveat is {len(spec.caveat)} chars — "
                "this is a UI hint, not documentation"
            )


def test_four_runtimes_five_specs() -> None:
    """
    The lineup is a decision, not an accident. Changing it is a plan change.

    Was five specs, dropped to four when `f5_openf5_en` was removed: R1b
    established that no genuinely permissive English F5 checkpoint exists —
    every candidate is license-washed on top of SWivid/F5-TTS's CC-BY-NC
    weights. English routes to Chatterbox (MIT) instead.

    Back to five with `voxcpm2_urdu_lora` (Urdu bake-off arm D, 2026-08-14),
    then that spec was withdrawn 2026-08-15 on the owner's real-use verdict
    (base VoxCPM2 sounded better than the fine-tune) and replaced by
    `voxcpm2_urdu_arabic` (arm B, same base checkpoint, no LoRA) so
    Perso-Arabic Urdu keeps a working answer — still the VOXCPM runtime, no
    new runtime for that swap.

    Six specs / four runtimes as of 2026-08-15: `omnivoice_urdu` (bake-off
    arm E, the best pronunciation result of the whole bake-off) is a genuinely
    new runtime — the first CC-BY-NC spec in the catalog, legal only under
    golden rule 6's personal-use amendment.

    Back down to five specs (still four runtimes) when Hindi was fully removed
    as a target language: `f5_indic` existed solely to serve Hindi
    (`(hi, Script.DEVANAGARI)`), so it was deleted outright rather than left
    with an empty `languages` tuple.
    """
    assert len(CATALOG.specs) == 5
    assert {s.runtime for s in CATALOG.specs} == {
        RuntimeKind.F5,
        RuntimeKind.CHATTERBOX,
        RuntimeKind.VOXCPM,
        RuntimeKind.OMNIVOICE,
    }
    assert len(CATALOG.by_runtime(RuntimeKind.F5)) == 1
    assert len(CATALOG.by_runtime(RuntimeKind.VOXCPM)) == 2
    assert len(CATALOG.by_runtime(RuntimeKind.OMNIVOICE)) == 1
    assert CATALOG.get("f5_openf5_en") is None
    assert CATALOG.get("voxcpm2_urdu_lora") is None, "withdrawn 2026-08-15"
    assert CATALOG.get("f5_indic") is None, "Hindi fully removed 2026-08-15"


def test_voxcpm2_urdu_arabic_shares_base_checkpoint() -> None:
    """
    Same repo, same pinned revision as VOXCPM2 — a second catalog entry for an
    unverified cell on an otherwise-verified spec (see spec.py's
    `experimental_listing` docstring: no per-cell flag exists, only per-spec),
    not a different download, so the two can never silently drift apart.
    """
    base = CATALOG.require("voxcpm2")
    arabic = CATALOG.require("voxcpm2_urdu_arabic")
    assert arabic.hf_repo == base.hf_repo
    assert arabic.hf_revision == base.hf_revision
    assert arabic.runtime is RuntimeKind.VOXCPM
    assert arabic.lora_local_path is None, "no fine-tune — base checkpoint only"


def test_voxcpm2_urdu_arabic_is_experimental_not_verified() -> None:
    """
    Bake-off arm B: real [BENCH]/[LISTEN] results, but the automated
    speaker-cosine gate (0.70) does not clear at the owner reference (0.6664
    measured) — see docs/URDU_BAKEOFF_RESULTS.md. Must still show up as
    verified=False regardless of the listening score.
    """
    arabic = CATALOG.require("voxcpm2_urdu_arabic")
    assert arabic.experimental_listing is True
    assert arabic.claims("ur", Script.ARABIC)
    assert not arabic.supports("ur", Script.ARABIC), (
        "verified=False is required until the automated gate clears too"
    )
    assert arabic.caveat, "an experimental spec must carry a user-facing caveat"


def test_urdu_perso_arabic_still_has_no_auto_route() -> None:
    """
    Adding an unverified experimental candidate must not change Auto routing.
    Only an explicit, opted-in request may reach an unverified cell — see
    domain.routing's NO SILENT FALLBACK rule.
    """
    with pytest.raises(NoRouteError):
        resolve(profile_text("السلام علیکم، آپ کیسے ہیں؟", "ur"), None, CATALOG)


def test_voxcpm2_urdu_arabic_requires_explicit_opt_in() -> None:
    profile = profile_text("السلام علیکم، آپ کیسے ہیں؟", "ur")
    with pytest.raises(NoRouteError):
        resolve(profile, "voxcpm2_urdu_arabic", CATALOG)

    plan = resolve(profile, "voxcpm2_urdu_arabic", CATALOG, allow_experimental=True)
    assert plan.model_id == "voxcpm2_urdu_arabic"
    assert plan.experimental is True
    assert plan.transform.is_identity, "Perso-Arabic reaches this spec unchanged"


def test_omnivoice_urdu_is_cc_by_nc_and_experimental() -> None:
    """
    Bake-off arm E: the best pronunciation result of the whole bake-off, and
    the only Urdu cell whose automated gate passes on both references — but
    it is CC-BY-NC (personal use only, see docs/URDU_MODEL_LICENSING.md) and
    verified=False until the PRODUCTION runtime (OmniVoiceBackend, not the
    eval harness's isolated loader) has its own pod smoke test.
    """
    omni = CATALOG.require("omnivoice_urdu")
    assert omni.runtime is RuntimeKind.OMNIVOICE
    assert omni.license is License.CC_BY_NC
    assert not omni.license.is_permissive, "CC-BY-NC must never read as commercially shippable"
    assert omni.license.personal_use_ok, "but it must be legal for personal use"
    assert omni.experimental_listing is True
    assert omni.claims("ur", Script.ARABIC)
    assert not omni.supports("ur", Script.ARABIC), (
        "verified=False until the production runtime is pod-verified"
    )
    assert omni.caveat, "an experimental spec must carry a user-facing caveat"


def test_omnivoice_urdu_requires_explicit_opt_in() -> None:
    profile = profile_text("السلام علیکم، آپ کیسے ہیں؟", "ur")
    plan = resolve(profile, "omnivoice_urdu", CATALOG, allow_experimental=True)
    assert plan.model_id == "omnivoice_urdu"
    assert plan.experimental is True
    assert plan.transform.is_identity, "Perso-Arabic reaches this spec unchanged"


def test_auto_urdu_routing_still_prefers_no_model_over_an_nc_one() -> None:
    """
    Two experimental candidates now claim (ur, ARABIC) — voxcpm2_urdu_arabic
    and omnivoice_urdu — and NEITHER may be Auto-selected, licensing aside.
    A CC-BY-NC spec being the "best" one must never tempt Auto routing into
    picking it silently; only an explicit, named request may reach it.
    """
    with pytest.raises(NoRouteError):
        resolve(profile_text("السلام علیکم، آپ کیسے ہیں؟", "ur"), None, CATALOG)


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


def test_all_revisions_pinned() -> None:
    """
    Every spec must pin a 40-char commit sha. HARD GATE as of Wave 1.

    This was xfail while `f5_openf5_en` still carried PENDING placeholders.
    That spec was dropped (no permissive English F5 exists), so every remaining
    spec has a real sha and this now holds strictly.

    It matters most for IndicF5, which is CONFIRMED to require
    `trust_remote_code=True`: an unpinned `main` would mean the code executing
    on this machine is whatever the repo owner pushed last.

    A spec's OPTIONAL LoRA adapter (`lora_hf_repo`/`lora_hf_revision`) is held
    to the same rule when present — golden rule 7 doesn't stop applying just
    because the second asset is a private repo rather than the base weights.
    """
    sha = re.compile(r"^[0-9a-f]{40}$")
    for spec in CATALOG.specs:
        assert spec.hf_revision != PENDING_PIN, f"{spec.id} revision is unpinned"
        assert spec.hf_repo != PENDING_REPO, f"{spec.id} repo is unresolved"
        assert sha.match(spec.hf_revision), f"{spec.id} revision is not a commit sha"
        if spec.lora_hf_repo is not None:
            assert sha.match(spec.lora_hf_revision or ""), (
                f"{spec.id} lora_hf_revision is not a pinned commit sha"
            )


def test_attribution_required_for_the_by_licenses() -> None:
    """
    CC-BY-SA and CC-BY-NC both carry the "BY" (attribution-required) clause
    and need a NOTICE entry; they differ in ShareAlike vs NonCommercial, not
    in whether attribution is owed. Wave 4 audits the file itself.
    """
    for spec in CATALOG.needs_attribution():
        assert spec.license in {License.CC_BY_SA_4_0, License.CC_BY_NC}


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


# ── Job queue ────────────────────────────────────────────────────────────────


def test_job_status_set_is_closed() -> None:
    """
    `JobStatus` (app/jobs/types.py) and the `CHECK (status IN (...))` clause on
    the `jobs` table (db/schema.sql) are one fact stated twice. Under
    `CREATE TABLE IF NOT EXISTS`, an existing table keeps its old CHECK
    constraint forever — there is no migration path to add a sixth status —
    so if these two ever drift, a status the enum allows could be silently
    rejected by SQLite on a database that already exists, or vice versa. This
    parses the literal list out of the SQL text itself, not a hand-maintained
    copy, so the two truly cannot go out of sync without this test noticing.
    """
    from app.jobs.types import JobStatus

    schema_sql = (BACKEND_ROOT / "app" / "db" / "schema.sql").read_text(encoding="utf-8")
    match = re.search(r"CHECK\s*\(\s*status\s+IN\s*\(([^)]+)\)\s*\)", schema_sql)
    assert match, "no `CHECK (status IN (...))` clause found on the jobs table"
    sql_statuses = {s.strip().strip("'") for s in match.group(1).split(",")}

    assert sql_statuses == {s.value for s in JobStatus}


def test_no_route_error_enumerates_what_would_work() -> None:
    """
    The whole point of NoRouteError: never leave the user guessing.

    A bare "unsupported" is barely better than the sine wave it replaced.
    """
    err = NoRouteError(
        language="ur",
        script="devanagari",
        supported=(("ur", "arabic"), ("en", "latin")),
        suggestion="Use Perso-Arabic (اردو) or Roman script for Urdu.",
    )
    problem = err.to_problem(instance="/api/tts/generate")
    assert problem["status"] == 422
    assert problem["code"] == "NO_ROUTE"
    assert problem["instance"] == "/api/tts/generate"
    assert {"language": "en", "script": "latin"} in problem["supported"]
    assert "Perso-Arabic" in problem["suggestion"]


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
    expected = {
        "__init__", "spec", "catalog", "protocol", "scheduler", "worker_client",
        # Runtime layer (the torch side) — spawned as a subprocess, never imported
        # by the API. Allowlisted for torch in TORCH_ALLOWED; see worker.py.
        "worker", "runtimes",
        # Production factory that constructs+starts WorkerProcess for the scheduler.
        "factory",
        # The Speech Direction LLM analyzer's OWN scheduler — deliberately NOT
        # a change to `scheduler.py` (golden rule 8) and structurally separate
        # from it (one fixed model, no VRAM-budget eviction). No torch here;
        # see analyzer_scheduler.py's module docstring.
        "analyzer_scheduler",
    }
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
    assert not spec.supports("en", Script.ARABIC)
