"""
One-off exploratory verification, NOT part of the bake-off harness.

Two things being probed before the number-expansion fix ships:

1. Does eval/urdu_numerals.py generalize past the 3 corpus items it was
   built against? New synthetic sentences (ages, prices, percentages,
   larger numbers) the corpus doesn't cover.
2. Can "URL"'s mispronunciation ("oo are el" -- the Latin letter U read with
   Urdu/Hindi vowel phonetics, not English "you") be fixed by respelling it
   for the TTS input? Several candidate spellings, same sentence otherwise,
   judged by ear -- exactly like every other finding in this bake-off.

Deletable once judged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from urdu_numerals import expand_numbers_in_text  # noqa: E402

from app.inference.runtimes.omnivoice import OmniVoiceBackend  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _REPO_ROOT / "eval" / "results" / "urdu_bakeoff" / "more_checks"
_REV = "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"
_REF_AUDIO = _REPO_ROOT / "eval" / "fixtures" / "voice_urdu.wav"
_REF_TEXT = (
    "ہیلو میرا نام منور ہے اور تم بہت ہی فضول کام کر رہے ہو، "
    "بالکل بھی اچھا کام نہیں کیا تم نے۔"
)

# ── 1. New number sentences the corpus doesn't cover ─────────────────────────
_NUMBER_SENTENCES: dict[str, str] = {
    "age": "میری عمر 25 سال ہے۔",
    "price": "اس کی قیمت 1500 روپے ہے۔",
    "percent": "تقریباً 50 فیصد لوگوں نے یہ بات مانی۔",
    "big_number": "شہر کی آبادی 90000 سے زیادہ ہے۔",
    "small_count": "میرے پاس صرف 7 کتابیں ہیں۔",
}

# ── 2. URL respelling candidates ──────────────────────────────────────────────
_URL_SENTENCE_TEMPLATE = "براہ کرم PDF فائل ای میل کر دیں اور {url} بھی ساتھ بھیج دیں۔"
_URL_VARIANTS: dict[str, str] = {
    "baseline": "URL",
    "dotted": "U.R.L",
    "urdu_letters": "یو آر ایل",
    "lowercase": "url",
}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    backend = OmniVoiceBackend()
    print("Loading OmniVoiceBackend...", file=sys.stderr)
    backend.load("omnivoice_urdu", "k2-fsa/OmniVoice", _REV)
    print("Loaded.", file=sys.stderr)

    def synth(name: str, text: str) -> None:
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

    for name, sentence in _NUMBER_SENTENCES.items():
        expanded = expand_numbers_in_text(sentence)
        synth(f"num_{name}", expanded)

    for name, url_text in _URL_VARIANTS.items():
        sentence = _URL_SENTENCE_TEMPLATE.format(url=url_text)
        synth(f"url_{name}", sentence)

    backend.unload()
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
