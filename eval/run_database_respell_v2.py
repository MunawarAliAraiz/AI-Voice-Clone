"""
One-off exploratory verification, NOT part of the bake-off harness.

The all-Urdu-script respelling "ڈیٹا بیس" collides with an existing Urdu
word: بیس is also how the number 20 ("bees") is spelled, and the model reads
it that way in 2 of 3 contexts instead of the intended English "base" sound.
Testing a mixed respelling instead: only "data" (the part that was actually
broken -- "da-ta-base" syllable separation) gets the Urdu-script fix; "base"
stays Latin, since Latin English loanwords already render correctly
elsewhere in this corpus (office, check, GitHub all confirmed fine as-is).

Owner reference only. Deletable once judged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.inference.runtimes.omnivoice import OmniVoiceBackend  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO_ROOT / "eval" / "results" / "urdu_bakeoff" / "database_respell_v2"
_REV = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"
_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)

_CASES: list[tuple[str, str]] = [
    ("mixed_bare", "ڈیٹا base"),
    ("mixed_context", "ہمیں ڈیٹا base چاہیے۔"),
    (
        "mixed_full",
        "ہمیں ڈیٹا base کا backup لینا ہوگا اور پھر server دوبارہ restart کرنا پڑے گا۔",
    ),
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    backend = OmniVoiceBackend()
    print("Loading OmniVoiceBackend...", file=sys.stderr)
    backend.load("omnivoice_urdu", "k2-fsa/OmniVoice", _REV)
    print("Loaded.", file=sys.stderr)

    for name, text in _CASES:
        out_path = OUT_DIR / f"{name}.wav"
        print(f"[{name}] {text!r}", file=sys.stderr)
        backend.synth(
            text=text,
            reference_audio=str(_REF_AUDIO),
            output_path=str(out_path),
            params={"language": "ur"},
            sample_rate=24000,
            reference_text=_REF_TEXT,
        )
        print(f"  wrote {out_path.name}", file=sys.stderr)

    backend.unload()
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
