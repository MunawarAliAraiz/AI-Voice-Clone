"""
One-off real-audio check requested by the owner (2026-08-15), NOT part of the
bake-off harness -- same pattern as run_normalization_layer_check.py: calls
the REAL domain.routing.resolve() against the REAL catalog, then feeds
plan.resolved_text to the REAL OmniVoiceBackend. Verifies the exact text that
hit INTERNAL_ERROR locally (Windows box has no interpreter for any runtime --
expected, not a bug) actually synthesizes cleanly on the pod.

Deletable once judged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domain.language import profile_text
from app.domain.routing import resolve
from app.inference.catalog import build_catalog
from app.inference.runtimes.omnivoice import OmniVoiceBackend

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO_ROOT / "eval" / "results" / "urdu_bakeoff" / "user_report_check"
_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)

# Verbatim from the owner's browser test: long, multi-clause, mixes ASCII
# digits (8, 30, 2, 20, 2026), English loanwords (emails, GitHub, pull
# request, review, meeting, project, release, version, production, deploy),
# and the respelled "ڈیٹا بیس" (database).
_INPUT_TEXT = (
    "آج صبح میں نے تقریباً 8 بجے دفتر کے لیے گھر سے نکلنا تھا، لیکن ایک ضروری "
    "کام کی وجہ سے مجھے تقریباً 30 منٹ دیر ہو گئی۔ دفتر پہنچنے کے بعد میں نے "
    "پہلے emails check کیں، پھر GitHub پر ایک pull request review کی، اور اس "
    "کے بعد ڈیٹا بیس کا بیک اپ لیا۔ دوپہر 2 بجے ہماری ایک اہم meeting تھی جس "
    "میں project کی اگلی release کے بارے میں بات ہوئی۔ اگر سب کچھ ٹھیک رہا تو "
    "ہم 20 اگست 2026 کو نیا version production میں deploy کر دیں گے۔"
)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    catalog = build_catalog()
    profile = profile_text(_INPUT_TEXT, "ur")
    # No allow_experimental needed any more -- omnivoice_urdu is verified=True
    # as of 2026-08-15 (docs/URDU_BAKEOFF_RESULTS.md's closing note). Mirrors
    # exactly what tts.py's POST /api/generate does for this explicit request.
    plan = resolve(profile, "omnivoice_urdu", catalog)

    print(f"input:              {_INPUT_TEXT!r}", file=sys.stderr)
    print(f"resolved_text:      {plan.resolved_text!r}", file=sys.stderr)
    print(f"text_normalizations:{plan.text_normalizations!r}", file=sys.stderr)
    print(f"rationale:          {plan.rationale!r}", file=sys.stderr)
    print(f"experimental:       {plan.experimental!r}", file=sys.stderr)
    assert not plan.needs_transform, "identity transform expected for omnivoice_urdu"
    assert plan.experimental is False, "verified=True now -- no bypass should be needed"

    backend = OmniVoiceBackend()
    print("Loading OmniVoiceBackend...", file=sys.stderr)
    backend.load("omnivoice_urdu", "k2-fsa/OmniVoice", "c5fdb5ccb189668d56333f77ba2629f4cd7535f4")
    print("Loaded.", file=sys.stderr)

    out_path = OUT_DIR / "user_report_via_resolve.wav"
    backend.synth(
        text=plan.resolved_text,
        reference_audio=str(_REF_AUDIO),
        output_path=str(out_path),
        params={"language": "ur"},
        sample_rate=24000,
        reference_text=_REF_TEXT,
    )
    print(f"wrote {out_path}", file=sys.stderr)

    backend.unload()
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
