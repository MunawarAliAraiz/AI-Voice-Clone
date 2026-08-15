"""
One-off end-to-end verification, NOT part of the bake-off harness.

Closes the loop between "eval-verified" (eval/urdu_numerals.py,
eval/run_number_fix_check.py, eval/run_database_respell_v2.py) and "what
production actually sends": calls the REAL `domain.routing.resolve()` --
the exact function `POST /api/generate` calls -- against the REAL catalog,
then feeds `plan.resolved_text` to the REAL `OmniVoiceBackend`. If this
sounds identical to the already-verified eval clips, the wiring is correct,
not just unit-tested.

Deletable once judged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domain.language import profile_text  # noqa: E402
from app.domain.routing import resolve  # noqa: E402
from app.inference.catalog import build_catalog  # noqa: E402
from app.inference.runtimes.omnivoice import OmniVoiceBackend  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO_ROOT / "eval" / "results" / "urdu_bakeoff" / "normalization_layer_check"
_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)

# Combines everything checked separately during eval: a date, a URL, and
# database -- in one sentence, through the real resolve() -> synth() path.
_INPUT_TEXT = "یہ رپورٹ 14 اگست 2026 تک جمع کرانی ہے، URL اور database چیک کر لیں۔"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    catalog = build_catalog()
    profile = profile_text(_INPUT_TEXT, "ur")
    # Mirrors what tts.py's POST /api/generate does for an explicit
    # omnivoice_urdu request (allow_experimental=True, since verified=False
    # pending the owner listen -- see docs/URDU_BAKEOFF_RESULTS.md SS5b).
    plan = resolve(profile, "omnivoice_urdu", catalog, allow_experimental=True)

    print(f"input:              {_INPUT_TEXT!r}", file=sys.stderr)
    print(f"resolved_text:      {plan.resolved_text!r}", file=sys.stderr)
    print(f"text_normalizations:{plan.text_normalizations!r}", file=sys.stderr)
    print(f"rationale:          {plan.rationale!r}", file=sys.stderr)
    assert not plan.needs_transform, "identity transform expected for omnivoice_urdu"

    backend = OmniVoiceBackend()
    print("Loading OmniVoiceBackend...", file=sys.stderr)
    backend.load("omnivoice_urdu", "k2-fsa/OmniVoice", "c5fdb5ccb189668d56333f77ba2629f4cd7535f4")
    print("Loaded.", file=sys.stderr)

    out_path = OUT_DIR / "combined_via_resolve.wav"
    # Exactly what tts.py enqueues as params["text"] -- plan.resolved_text,
    # never re-derived, never the raw body.text.
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
