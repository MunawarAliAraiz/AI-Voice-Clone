"""
One-off verification script, NOT part of the bake-off harness proper.

Re-synthesizes exactly the 3 corpus items the arm-Eprod owner listen flagged
as broken (num_ascii, num_eastern, date — see docs/URDU_BAKEOFF_RESULTS.md
§5b) with digits expanded to Urdu words via eval/urdu_numerals.py, through
the real production OmniVoiceBackend, for both references. Deletable once
the fix is confirmed or rejected by ear.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from urdu_numerals import expand_numbers_in_text  # noqa: E402
from urdu_represent import load_corpus  # noqa: E402

from app.inference.runtimes.omnivoice import OmniVoiceBackend  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO_ROOT / "eval" / "results" / "urdu_bakeoff" / "number_fix_check"
_REV = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)

_TARGET_IDS = {"num_ascii", "num_eastern", "date"}

# reference_text is a transcript of the REFERENCE AUDIO, not the text being
# synthesized -- it must match whichever clip is passed. The owner's clip has
# a known transcript (eval/fixtures/README.md); the female clip's transcript
# was never recorded, and the original arm-Eprod female run never passed one
# either (OmniVoice auto-transcribes via its own Whisper when omitted). A
# male transcript reused against the female clip breaks cloning -- exactly
# the "jibberish" failure this bug caused.
_REFERENCES: dict[str, tuple[Path, str]] = {
    "owner": (_REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav", _REF_TEXT),
    "female": (_REPO_ROOT / "eval" / "fixtures" / "voice_urdu_female.wav", ""),
}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    corpus = load_corpus()
    items = [i for i in corpus if i.id in _TARGET_IDS]
    assert len(items) == 3, f"expected 3 target items, found {len(items)}"

    backend = OmniVoiceBackend()
    print("Loading OmniVoiceBackend...", file=sys.stderr)
    backend.load("omnivoice_urdu", "k2-fsa/OmniVoice", _REV)
    print("Loaded.", file=sys.stderr)

    for ref_id, (ref_path, ref_text) in _REFERENCES.items():
        for item in items:
            original = item.perso_arabic
            expanded = expand_numbers_in_text(original)
            out_path = OUT_DIR / f"{ref_id}_{item.id}_expanded.wav"
            print(f"[{ref_id}/{item.id}] {original!r} -> {expanded!r}", file=sys.stderr)
            result = backend.synth(
                text=expanded,
                reference_audio=str(ref_path),
                output_path=str(out_path),
                params={"language": "ur"},
                sample_rate=24000,
                reference_text=ref_text,
            )
            print(f"  wrote {out_path.name} ({result['sample_rate']} Hz)", file=sys.stderr)

    backend.unload()
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
