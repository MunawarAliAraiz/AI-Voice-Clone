"""
Phase A evaluation harness — scores a TTS output against the three gates.

    CER            < 0.25   Whisper large-v3, script-aware normalization
    speaker cosine > 0.70   SpeechBrain ECAPA-TDNN
    RTF            < 1.0    caller-supplied generation time

⚠️ THE GATE IS A SCREEN, NOT A VERDICT.

On 2026-08-04, VoxCPM2 scored CER 0.0702 (pass) and speaker cosine 0.6859 — a
0.014 miss — and a native Urdu speaker listening to that exact file reported it
"sounds Hindi" and "does not sound like me". A near-gate cosine coexisted with
total failure on the actual product requirement.

So: a PASS here means "worth a human listening to it". It does not mean the
output is usable. A FAIL is reliable; a PASS is not. Never mark a catalog cell
verified on these numbers alone.

This file lives in git deliberately. The original was written only into
/workspace/engines-lab/ on a pod and was destroyed by a volume migration.
Anything that took effort to build belongs in the repo.

Usage:
    python eval/eval_harness.py \
        --reference eval/fixtures/voice_urdu.wav \
        --generated /path/to/output.wav \
        --text "<target text the model was asked to speak>" \
        --language ur --gen-time-sec 2.7

Dependencies (install into a dedicated venv — NOT the API env, which must stay
torch-free per the project's structural invariant):

    uv pip install torch torchaudio transformers speechbrain jiwer soundfile
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Language = Literal["ur", "hi", "en"]

CER_GATE = 0.25          # strictly less than
SPEAKER_SIM_GATE = 0.70  # strictly greater than
RTF_GATE = 1.0           # strictly less than

WHISPER_MODEL = "openai/whisper-large-v3"
SPEAKER_MODEL = "speechbrain/spkrec-ecapa-voxceleb"

_WHISPER_LANG: dict[str, str] = {"ur": "urdu", "hi": "hindi", "en": "english"}

# Digit folding. Urdu/Hindi text mixes several digit families and Whisper is
# inconsistent about which it emits, so all fold to ASCII before comparison —
# otherwise CER is penalised for a difference nobody can hear.
_DIGIT_MAP = {}
for _family in ("٠١٢٣٤٥٦٧٨٩", "۰۱۲۳۴۵۶۷۸۹", "०१२३४५६७८९"):
    for _i, _ch in enumerate(_family):
        _DIGIT_MAP[ord(_ch)] = str(_i)

# Script-aware punctuation. Includes the Urdu full stop (U+06D4), Arabic comma
# and question mark, and the Devanagari danda pair — a Latin-only punctuation
# set silently inflates CER on exactly the languages this project targets.
_PUNCT = re.compile(r"[\.\,\!\?\;\:\"'“”‘’\(\)\[\]\-–—۔،؟।॥]+")


def normalize_text(text: str, language: Language) -> str:
    """
    Normalize for CER comparison: NFC, fold digits, strip punctuation,
    lowercase, collapse whitespace.

    `language` is accepted for symmetry but normalization is deliberately
    SCRIPT-driven, not language-driven — per the project's script-detection
    rule, never branch on the declared language without checking the actual
    script of the string.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_DIGIT_MAP)
    text = _PUNCT.sub(" ", text)
    return " ".join(text.lower().split())


def compute_cer(reference_text: str, hypothesis_text: str, language: Language) -> float:
    """Character error rate after script-aware normalization."""
    import jiwer

    ref = normalize_text(reference_text, language)
    hyp = normalize_text(hypothesis_text, language)
    if not ref:
        return 1.0
    return float(jiwer.cer(ref, hyp))


@dataclass(frozen=True)
class EvalResult:
    language: str
    target_text: str
    whisper_transcript: str
    cer: float
    speaker_sim: float
    rtf: float | None
    duration_sec: float

    @property
    def cer_pass(self) -> bool:
        return self.cer < CER_GATE

    @property
    def speaker_pass(self) -> bool:
        return self.speaker_sim > SPEAKER_SIM_GATE

    @property
    def rtf_pass(self) -> bool:
        return self.rtf is None or self.rtf < RTF_GATE

    @property
    def overall_pass(self) -> bool:
        return self.cer_pass and self.speaker_pass and self.rtf_pass

    def render(self) -> str:
        def mark(ok: bool) -> str:
            return "PASS" if ok else "FAIL"

        rtf = f"{self.rtf:.4f}" if self.rtf is not None else "n/a"
        return (
            f"language: {self.language}\n"
            f"target_text:        {self.target_text!r}\n"
            f"whisper_transcript: {self.whisper_transcript!r}\n"
            f"duration_sec:       {self.duration_sec:.2f}\n"
            f"CER:         {self.cer:.4f}  (< {CER_GATE})  -> {mark(self.cer_pass)}\n"
            f"speaker_sim: {self.speaker_sim:.4f}  (> {SPEAKER_SIM_GATE})  -> {mark(self.speaker_pass)}\n"
            f"RTF:         {rtf}  (< {RTF_GATE})  -> {mark(self.rtf_pass)}\n"
            f"OVERALL:     {mark(self.overall_pass)}\n"
            f"\nA PASS means 'worth a human listening to'. It is NOT sufficient\n"
            f"evidence that the clone works — see the module docstring."
        )


class EvalHarness:
    """Models load lazily and are cached, so scoring many files costs one load."""

    def __init__(self, device: str = "cuda") -> None:
        # SpeechBrain wants an explicit index; a bare "cuda" makes it warn and
        # fall back to device 0.
        self.device = "cuda:0" if device == "cuda" else device
        self._asr = None
        self._spk = None

    def _asr_pipeline(self):
        if self._asr is None:
            import torch
            from transformers import pipeline

            self._asr = pipeline(
                "automatic-speech-recognition",
                model=WHISPER_MODEL,
                device=0 if self.device.startswith("cuda") else -1,
                torch_dtype=torch.float16 if self.device.startswith("cuda") else torch.float32,
            )
        return self._asr

    def _speaker_model(self):
        if self._spk is None:
            from speechbrain.inference.speaker import EncoderClassifier

            self._spk = EncoderClassifier.from_hparams(
                source=SPEAKER_MODEL,
                savedir="/tmp/spkrec-ecapa",
                run_opts={"device": self.device},
            )
        return self._spk

    def transcribe(self, wav_path: str | Path, language: Language) -> str:
        out = self._asr_pipeline()(
            str(wav_path),
            generate_kwargs={"language": _WHISPER_LANG[language], "task": "transcribe"},
        )
        return str(out["text"]).strip()

    def speaker_similarity(self, a: str | Path, b: str | Path) -> float:
        """Cosine similarity between ECAPA-TDNN embeddings of two clips."""
        import torch
        import torchaudio

        model = self._speaker_model()

        def embed(path: str | Path) -> "torch.Tensor":
            wav, sr = torchaudio.load(str(path))
            if wav.shape[0] > 1:                      # mixdown to mono
                wav = wav.mean(dim=0, keepdim=True)
            if sr != 16000:                           # ECAPA expects 16 kHz
                wav = torchaudio.functional.resample(wav, sr, 16000)
            return model.encode_batch(wav.to(self.device)).squeeze()

        return float(
            torch.nn.functional.cosine_similarity(embed(a), embed(b), dim=0).item()
        )

    def evaluate(
        self,
        reference: str | Path,
        generated: str | Path,
        target_text: str,
        language: Language,
        gen_time_sec: float | None = None,
    ) -> EvalResult:
        import soundfile as sf

        duration = float(sf.info(str(generated)).duration)
        transcript = self.transcribe(generated, language)
        return EvalResult(
            language=language,
            target_text=target_text,
            whisper_transcript=transcript,
            cer=compute_cer(target_text, transcript, language),
            speaker_sim=self.speaker_similarity(reference, generated),
            rtf=(gen_time_sec / duration) if gen_time_sec and duration > 0 else None,
            duration_sec=duration,
        )


def main() -> int:
    p = argparse.ArgumentParser(description="Score a TTS output against the Phase A gates.")
    p.add_argument("--reference", required=True, help="Reference speaker audio (the voice being cloned)")
    p.add_argument("--generated", required=True, help="Generated audio to evaluate")
    p.add_argument("--text", required=True, help="Target text the model was asked to speak")
    p.add_argument("--language", required=True, choices=["ur", "hi", "en"])
    p.add_argument("--gen-time-sec", type=float, default=None, help="Wall-clock generation time; enables the RTF gate")
    p.add_argument("--device", default="cuda")
    args = p.parse_args()

    result = EvalHarness(device=args.device).evaluate(
        reference=args.reference,
        generated=args.generated,
        target_text=args.text,
        language=args.language,
        gen_time_sec=args.gen_time_sec,
    )
    print(result.render())
    return 0 if result.overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
