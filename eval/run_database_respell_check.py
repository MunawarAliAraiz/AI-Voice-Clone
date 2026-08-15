"""
One-off exploratory verification, NOT part of the bake-off harness.

Tests a respelling fix for "database" (confirmed word-level wrong in BOTH
bare and minimal-context isolation -- see docs/URDU_BAKEOFF_RESULTS.md),
the same pattern that fixed URL: swap the Latin word for its Urdu-script
phonetic spelling before synthesis. Owner reference only. Deletable once
judged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.inference.runtimes.omnivoice import OmniVoiceBackend  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO_ROOT / "eval" / "results" / "urdu_bakeoff" / "database_respell_check"
_REV = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"
_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)

_CASES: list[tuple[str, str]] = [
    ("database_respelled_bare", "ڈیٹا بیس"),
    ("database_respelled_context", "ہمیں ڈیٹا بیس چاہیے۔"),
    ("database_respelled_full", "ہمیں database کا backup لینا ہوگا اور پھر server دوبارہ restart کرنا پڑے گا۔".replace("database", "ڈیٹا بیس")),
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
