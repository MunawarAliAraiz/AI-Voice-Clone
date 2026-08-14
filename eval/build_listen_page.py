"""
Build the BLIND side-by-side listening page — the actual decision instrument.

    python eval/build_listen_page.py

Blinding is done by copying every clip to `blind/<token>.wav` under an opaque
random name and writing the token -> arm mapping to a SEPARATE `key.json`. The
page references only tokens, so the arm cannot be recovered from the filename,
the DOM, or devtools without deliberately opening the key. Referencing the
original paths and merely hiding the label would not be blind: `arm_F_male/...`
is right there in the audio element's src.

Each row is one corpus sentence for one reference speaker. The reference clip
itself is shown UNBLINDED at the top of the row — you cannot judge "speaker
identity" without hearing who you are comparing against — followed by every
arm's attempt in shuffled order.

Scores are entered in the page and exported as JSON, so a human verdict lands
in the repo next to the automated numbers instead of in a chat message.

Why this exists at all: CER and speaker cosine are a screen. ECAPA-TDNN is
English-trained and out-of-distribution for this voice, and VoxCPM2 once passed
CER and nearly passed cosine while still sounding like a stranger to the owner.
`LanguageSupport.verified` may not be set from an automated gate alone.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import random
import secrets
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS = _REPO_ROOT / "eval" / "results" / "urdu_bakeoff"

CRITERIA = [
    ("pron", "Pakistani Urdu pronunciation"),
    ("natural", "Naturalness"),
    ("identity", "Speaker identity"),
    ("prosody", "Prosody"),
    ("codeswitch", "Urdu-English code switching"),
]


def _audio_src(path: Path, embed: bool, fallback: str) -> str:
    """
    A `src` for one clip.

    Embedded by default, as a base64 MP3 data URI, because a page that
    references `blind/xxx.wav` relatively only plays when it is opened in a way
    that preserves the directory — and it silently does NOT when the file is
    viewed through a preview pane, a `data:` URL, a snapshot, or anywhere the
    HTML has been moved without its folder. The failure mode is the worst kind:
    the page renders perfectly and the play button just does nothing.

    Embedding makes the file self-contained, so it works over file://, http://,
    copied to another machine, or emailed. MP3 rather than the source WAV
    because it is ~10x smaller (35 KB vs 345 KB per clip, so ~2 MB total rather
    than ~24 MB) and is the one codec every browser decodes.
    """
    if not embed:
        return fallback
    import soundfile as sf

    data, sr = sf.read(str(path))
    buf = io.BytesIO()
    sf.write(buf, data, sr, format="MP3")
    return "data:audio/mpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _resolve(p: str | Path) -> Path:
    """
    Resolve a manifest path.

    Manifests normally store repo-relative paths, but `--out-dir` may point
    outside the repo (a scratch dir on the pod volume, say), in which case the
    driver records an absolute one. Handle both rather than silently producing
    a path that does not exist and reporting "no clips found".
    """
    path = Path(p)
    return path if path.is_absolute() else _REPO_ROOT / path


def _collect(root: Path) -> tuple[list[dict], dict]:
    """Gather every playable clip. Prefers scores.json, falls back to manifest."""
    samples: list[dict] = []
    corpus_text: dict[str, dict] = {}
    for arm_dir in sorted(root.glob("arm_*")):
        src = arm_dir / "scores.json"
        if not src.exists():
            src = arm_dir / "manifest.json"
        if not src.exists():
            continue
        payload = json.loads(src.read_text(encoding="utf-8"))
        meta = payload["meta"]
        for row in payload["rows"]:
            if row.get("status") != "ok" or not row.get("output"):
                continue
            wav = _resolve(row["output"])
            if not wav.exists():
                continue
            corpus_text.setdefault(row["item_id"], {
                "cer_reference": row["cer_reference"],
                "category": row["category"],
                "stresses": row.get("stresses", []),
            })
            samples.append({
                "arm": meta["arm"],
                "model": meta["model"],
                "representation": meta["representation"],
                "lora": meta.get("lora", False),
                "reference_id": row["reference_id"],
                "reference_path": row["reference_path"],
                "item_id": row["item_id"],
                "input_text": row["input_text"],
                "wav": wav,
                "cer": row.get("cer"),
                "speaker_sim": row.get("speaker_sim"),
            })
    return samples, corpus_text


_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Urdu bake-off &mdash; blind listening</title>
<style>
 :root {{ color-scheme: dark; --bg:#0f1115; --fg:#e8eaf0; --mut:#9aa3b2;
          --card:#181b22; --line:#272b35; --acc:#7aa2f7; }}
 body {{ background:var(--bg); color:var(--fg); margin:0; padding:24px;
         font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; }}
 h1 {{ font-size:20px; margin:0 0 4px; }}
 .sub {{ color:var(--mut); font-size:13px; margin-bottom:20px; }}
 .warn {{ background:#2a1f14; border:1px solid #5c4423; border-radius:8px;
          padding:12px 14px; margin:16px 0; font-size:13px; }}
 .item {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:16px; margin-bottom:22px; }}
 .urdu {{ font-size:19px; direction:rtl; text-align:right; margin:2px 0 10px;
          font-family:"Noto Nastaliq Urdu","Jameel Noori Nastaleeq",serif; line-height:2.1; }}
 .tag {{ display:inline-block; background:#20242e; color:var(--mut); border-radius:4px;
         padding:1px 7px; font-size:11px; margin-right:5px; }}
 .ref {{ border-left:3px solid var(--acc); padding-left:12px; margin:12px 0 16px; }}
 .cand {{ border-top:1px solid var(--line); padding:12px 0 4px; }}
 .lbl {{ font-weight:600; color:var(--acc); margin-right:10px; }}
 audio {{ vertical-align:middle; height:34px; }}
 .crit {{ display:flex; flex-wrap:wrap; gap:14px; margin:8px 0 0; }}
 .crit label {{ font-size:12px; color:var(--mut); }}
 select, textarea {{ background:#11141a; color:var(--fg); border:1px solid var(--line);
                     border-radius:5px; padding:3px 6px; font:inherit; font-size:13px; }}
 textarea {{ width:100%; margin-top:8px; min-height:34px; }}
 .bar {{ position:sticky; top:0; background:var(--bg); padding:10px 0 14px;
         border-bottom:1px solid var(--line); margin-bottom:18px; z-index:9; }}
 button {{ background:var(--acc); color:#0b0d12; border:0; border-radius:6px;
           padding:8px 15px; font:inherit; font-weight:600; cursor:pointer; }}
 #prog {{ color:var(--mut); font-size:13px; margin-left:12px; }}
</style>

<h1>Urdu bake-off &mdash; blind listening</h1>
<div class="sub">{n_samples} clips &middot; {n_items} sentences &middot; {n_refs} reference speaker(s)</div>

<div class="warn">
  <b>This is the decision.</b> CER and speaker cosine are only a screen &mdash; ECAPA is
  English-trained and out-of-distribution for this voice, and VoxCPM2 once passed CER and
  nearly passed cosine while still sounding like a stranger. Score what you hear.
  Clip labels are randomized; the mapping lives in <code>key.json</code>, which you should
  not open until you are done.
  <br><br>
  Score each clip 1&ndash;5 (1 = bad, 5 = indistinguishable from a native speaker /
  from the reference). Leave a clip blank if you cannot judge it. <b>Export</b> when finished.
</div>

<div class="bar">
  <button onclick="exportScores()">Export scores JSON</button>
  <span id="prog"></span>
</div>

<div id="items"></div>

<script>
const DATA = {data_json};
const CRITERIA = {criteria_json};

function render() {{
  const root = document.getElementById('items');
  for (const item of DATA) {{
    const d = document.createElement('div');
    d.className = 'item';
    let h = `<div>${{item.stresses.map(s => `<span class="tag">${{s}}</span>`).join('')}}
             <span class="tag">${{item.reference_id}} reference</span></div>
             <div class="urdu">${{item.text}}</div>
             <div class="ref"><b>Reference speaker</b> (the voice being cloned)<br>
             <audio controls preload="none" src="${{item.reference}}"></audio></div>`;
    for (const c of item.clips) {{
      h += `<div class="cand"><span class="lbl">${{c.label}}</span>
            <audio controls preload="none" src="${{c.src}}"></audio>
            <div class="crit">` +
        CRITERIA.map(([k, name]) =>
          `<label>${{name}}
             <select data-t="${{c.token}}" data-k="${{k}}">
               <option value="">&ndash;</option>${{[1,2,3,4,5].map(v =>
                 `<option value="${{v}}">${{v}}</option>`).join('')}}
             </select></label>`).join('') +
        `</div><textarea placeholder="Comments on ${{c.label}} (optional)"
                 data-t="${{c.token}}" data-k="comment"></textarea></div>`;
    }}
    d.innerHTML = h;
    root.appendChild(d);
  }}
  document.addEventListener('change', progress);
  document.addEventListener('input', progress);
  progress();
}}

function collect() {{
  const out = {{}};
  for (const el of document.querySelectorAll('[data-t]')) {{
    if (!el.value) continue;
    (out[el.dataset.t] ||= {{}})[el.dataset.k] =
      el.dataset.k === 'comment' ? el.value : Number(el.value);
  }}
  return out;
}}

function progress() {{
  const scored = Object.values(collect())
    .filter(v => CRITERIA.some(([k]) => v[k] != null)).length;
  const total = DATA.reduce((n, i) => n + i.clips.length, 0);
  document.getElementById('prog').textContent = `${{scored}} / ${{total}} clips scored`;
}}

function exportScores() {{
  const blob = new Blob([JSON.stringify(
    {{ scored_at: new Date().toISOString(), scores: collect() }}, null, 2)],
    {{ type: 'application/json' }});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'listen_scores.json';
  a.click();
}}

render();
</script>
"""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results-dir", default=str(RESULTS))
    p.add_argument("--seed", type=int, default=None,
                   help="Shuffle seed. Omit for a fresh random order.")
    p.add_argument("--no-embed", dest="embed", action="store_false",
                   help="Reference blind/*.wav by path instead of embedding the "
                        "audio. Smaller file, but only plays when opened next to "
                        "its blind/ folder.")
    p.set_defaults(embed=True)
    args = p.parse_args()

    root = Path(args.results_dir)
    samples, corpus_text = _collect(root)
    if not samples:
        raise SystemExit(
            f"no playable clips under {root} — run eval/run_urdu_bakeoff.py first"
        )

    blind_dir = root / "blind"
    if blind_dir.exists():
        shutil.rmtree(blind_dir)
    blind_dir.mkdir(parents=True)

    rng = random.Random(args.seed if args.seed is not None else secrets.randbits(32))

    # Blind every clip: opaque token filename, mapping held only in key.json.
    key: dict[str, dict] = {}
    for s in samples:
        token = secrets.token_hex(8)
        shutil.copy2(s["wav"], blind_dir / f"{token}.wav")
        s["token"] = token
        key[token] = {
            "arm": s["arm"], "model": s["model"], "representation": s["representation"],
            "lora": s["lora"], "item_id": s["item_id"], "reference_id": s["reference_id"],
            "input_text": s["input_text"], "cer": s["cer"], "speaker_sim": s["speaker_sim"],
            "source_wav": str(s["wav"]),
        }

    # One row per (item, reference), clips shuffled and relabelled per row so
    # position carries no information across rows either.
    groups: dict[tuple[str, str], list[dict]] = {}
    for s in samples:
        groups.setdefault((s["item_id"], s["reference_id"]), []).append(s)

    data = []
    #: The reference clip repeats on every row; encode it once per speaker
    #: rather than 13 times, which would triple the page for no benefit.
    ref_cache: dict[str, str] = {}
    for (item_id, ref_id), clips in sorted(groups.items()):
        rng.shuffle(clips)
        ref_path = _resolve(clips[0]["reference_path"])
        # Copy the reference in too, so the page is self-contained and works
        # over file:// without climbing out of the results directory.
        ref_name = f"reference_{ref_id}.wav"
        if ref_path.exists() and not (blind_dir / ref_name).exists():
            shutil.copy2(ref_path, blind_dir / ref_name)
        if ref_path.exists():
            ref_src = ref_cache.setdefault(
                ref_id, _audio_src(ref_path, args.embed, f"blind/{ref_name}")
            )
        else:
            ref_src = ""
        info = corpus_text.get(item_id, {})
        data.append({
            "item_id": item_id,
            "reference_id": ref_id,
            "reference": ref_src,
            "text": info.get("cer_reference", ""),
            "stresses": info.get("stresses", []),
            "clips": [
                {"label": f"Sample {i + 1}", "token": c["token"],
                 "src": _audio_src(blind_dir / f"{c['token']}.wav", args.embed,
                                   f"blind/{c['token']}.wav")}
                for i, c in enumerate(clips)
            ],
        })

    (root / "key.json").write_text(
        json.dumps({"_warning": "UNBLINDING KEY — do not open until scoring is done.",
                    "key": key}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    page = _PAGE.format(
        n_samples=len(samples), n_items=len(corpus_text),
        n_refs=len({s["reference_id"] for s in samples}),
        data_json=json.dumps(data, ensure_ascii=False),
        criteria_json=json.dumps(CRITERIA, ensure_ascii=False),
    )
    out = root / "listen.html"
    out.write_text(page, encoding="utf-8")

    arms = sorted({s["arm"] for s in samples})
    print(f"{len(samples)} clips from arms {', '.join(arms)}", file=sys.stderr)
    print(f"{len(data)} rows ({len(corpus_text)} sentences x "
          f"{len({s['reference_id'] for s in samples})} reference(s))", file=sys.stderr)
    print(f"\nOpen: {out}", file=sys.stderr)
    print(f"Key (do not open yet): {root / 'key.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
