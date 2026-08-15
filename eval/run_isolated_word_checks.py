"""
One-off exploratory verification, NOT part of the bake-off harness.

Isolates each remaining arm-Eprod failure (see docs/URDU_BAKEOFF_RESULTS.md
SS5b) word-by-word: بارہ کرم, URL, database, check, meeting, office -- each
alone AND in minimal context, plus the two already-known-good control cases
(office, meeting-without-a-number) so a fix attempt can be checked for
regressions, not just wins. Deliberately does NOT apply any normalization --
this is measurement, not a fix, matching the plan: isolate before treating.

Owner reference only (male) -- these are diagnostic, not a gate run.
Deletable once judged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.inference.runtimes.omnivoice import OmniVoiceBackend  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO_ROOT / "eval" / "results" / "urdu_bakeoff" / "isolated_word_checks"
_REV = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"
_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)

# (id, text) -- ordered: bare word, then minimal context, then the
# already-tested full corpus sentence (for reference), for each target.
_CASES: list[tuple[str, str]] = [
    ("barae_karam_bare", "براہ کرم"),
    ("barae_karam_context", "براہ کرم فائل بھیج دیں۔"),
    ("url_bare", "URL"),
    ("url_context", "یہ URL ہے۔"),
    ("database_bare", "database"),
    ("database_context", "ہمیں database چاہیے۔"),
    ("check_bare", "check"),
    ("check_context", "میں نے check کیا۔"),
    ("meeting_bare", "میٹنگ"),
    ("meeting_no_number", "میٹنگ ہے۔"),
    ("meeting_with_number", "میٹنگ 3 بجے ہے۔"),
    ("office_bare", "office"),
    ("office_context", "میں office میں ہوں۔"),
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
