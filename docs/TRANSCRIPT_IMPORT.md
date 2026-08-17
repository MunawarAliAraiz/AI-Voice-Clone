# Transcript import — design, decisions, and what is not built

Paste a YouTube link, get its captions, edit them, generate speech a part at a time.

Written up in-repo rather than left in a chat transcript or a plan file, because the *reasoning*
here is what a future session needs and the code alone does not carry it.

---

## The fact that shapes everything: no model here renders Hindi

This is a deliberate removal, not a gap:

- `hi` is not a `LanguageCode` member — `backend/app/domain/language.py`
- No catalog spec declares a Devanagari cell; `f5_indic` existed solely for Hindi and was deleted —
  `backend/app/inference/catalog.py`
- `backend/app/domain/routing.py` raises `NoRouteError` for `(ur, DEVANAGARI)` **on purpose**, with
  a comment saying accepting it "would quietly make the language field meaningless"
- OmniVoice claims only `(ur, ARABIC)`. VoxCPM2 claims `(en, LATIN)` and `(ur, LATIN)`.

So a Hindi transcript pasted into the editor produces a 422, and there is no built-in Hindi support
in any model to fall back on.

**What makes the feature work anyway:** Hindi and Urdu are the same spoken language in different
scripts. A Devanagari transcript is text OmniVoice could speak *if it were Perso-Arabic* — which is
what the Phase B transliterator does, just from a different source script.

**Hindi is therefore a SOURCE FORMAT, never a target language.** The deliberate removal stands.

---

## Decisions taken (owner, 2026-08-17)

| Question | Decision | Why not the alternative |
|---|---|---|
| Transcript source | **YouTube's own caption track** (yt-dlp) | Whisper ASR would be better on Urdu/Hindi and works when captions are absent, but costs a new runtime venv (`.venv-eval` is barred from the API), GPU time, and minutes per video instead of seconds. |
| Hindi | **Devanagari as a source script for the transliterator**, behind its own gate | Re-adding `hi` as a target language contradicts the removal, and no permissive model both lists Hindi and clones from reference audio. |
| Long transcripts | **Reuse `chunk_for_synthesis`** | Fixed time windows cut mid-sentence — exactly the prosody artifact that function exists to avoid. |

Two things the owner accepted knowingly: fetching captions is contrary to YouTube's Terms of
Service (personal use, their call), and auto-generated Urdu/Hindi captions are frequently poor —
which is *why* the transcript lands in an editable field rather than going straight to synthesis.

---

## What is built

### `backend/app/domain/youtube.py` — pure, no network

`parse_video_id(url)` is **the SSRF guard**, and it is a separate pure function for that reason.
The endpoint fetches server-side from a user-supplied string, which is that hole by default. So no
user-supplied URL is ever fetched: this extracts an **11-character video id** from a known host and
the caller builds its own request from that id alone. The user picks the video; never the scheme,
host, port or path.

It parses with `urlsplit` rather than a regex over the whole URL, because hostname matching in a
hand-rolled regex is where this class of guard usually fails —
`https://youtube.com.evil.test/watch?v=…` and `https://www.youtube.com@evil.test/…` both contain
the literal "youtube.com" and both are rejected on `.hostname`.

`cues_to_text()` joins cues with a **space**, emitting a newline only where the previous cue ended
on sentence punctuation (`.` `!` `?` `۔` `؟` `।` `॥`). This is not cosmetic: caption tracks are
timed for reading and split one sentence across three cues, while
`direction_analyze._split_units` now treats a newline as the longest pause there is. Joining every
cue with a newline would put a deliberate ~380 ms silence inside most sentences. **These two
changes landed hours apart and would have fought.**

### `backend/app/api/routers/transcript.py` — `POST /api/transcript/fetch`

Synchronous, not a job: the `jobs` table is the GPU queue (golden rule 8) and this never touches
the GPU. Queuing a network fetch behind a 60-second synthesis, and reporting an ETA derived from
VRAM residency, would both be nonsense.

Returns `needs_transliteration`, computed **server-side** from the catalog, so the UI never encodes
routing rules.

Errors are RFC 9457 with stable codes. A refusal is **502, not 500**: on a datacenter IP — which
every pod has — YouTube refusing is expected often enough that it must not read as an application
bug.

### Chunking

`chunk_for_synthesis` reused unchanged. `max_chars` comes from `Settings.transcript_chunk_chars`
rather than a `ModelSpec` field, and that is a deliberate compromise: the function's own docstring
says the value derives from a model's frame limit, `ModelSpec` carries no such field, and inventing
one would mean inventing numbers for four runtimes nobody has measured. **A documented default the
owner can tune is honest; a guess wearing a spec field is not.** The upgrade path is a *measured*
`ModelSpec.max_chars`.

`ends_on_sentence=False` is surfaced and badged in the UI — that is where a join artifact becomes
audible, and hiding it would make a known defect look like a model failure.

### `frontend/src/components/TranscriptPanel.tsx` — the Import tab

Ends at "put this in the editor", never at "generate this". The transcript box is **read-only**;
the editable copy lives in the Composer, because two editable copies of one text is how they drift
apart.

Text crosses tabs via a `pendingText` prop carrying `{text, token}` — a **monotonic token, not the
string**. Keying the effect on the string would silently ignore sending the same chunk twice, which
is a real thing to want when working through a transcript part by part.

---

## What is NOT built

- **Devanagari → Perso-Arabic.** The Import tab detects it, says so, and disables Send. The
  conversion itself needs: a source-script parameter and Devanagari exemplars in
  `runtimes/gemma_transliterator.py`, and `validate_transliteration`'s echo check extended (it
  currently only recognises a LATIN echo).
- **Its listening gate.** R4b measured the *reverse* hop compounding errors badly (مجھے →
  "majhay" → मझे). Direct Devanagari → Perso-Arabic is a different and probably easier mapping but
  **has never been measured here**. Add a Devanagari arm to `eval/run_roman_arabic_probe.py` and run
  the A3 protocol end to end. Synthesis is unseeded — sample repeatedly, listen blind, and remember
  numeric screens can only fail a candidate, never approve one.
- **Any browser verification.** Backend is unit-tested offline; the tab has only been built.

---

## The Roman-draft question (owner deciding)

Proposal: type/import **Roman Urdu**, keep it as the readable editable draft, convert to
Perso-Arabic for OmniVoice.

Storing both is free — `generation_history.resolved_text` already exists for exactly this and is
simply not exposed in `HistoryItem` or `frontend/src/types/api.ts`. The real cost is **staleness**:
edit the Roman after converting and the Perso-Arabic is stale, and silently synthesizing either is
golden rule 5's family.

| Approach | Edit in | Cost |
|---|---|---|
| Roman is truth, re-convert every edit | Roman | ~78 s Gemma load per edit round |
| Convert once, then edit the Perso-Arabic | Perso-Arabic | none, but harder to read |
| **Both kept; editing Roman marks Urdu stale and BLOCKS Generate** ← recommended | Roman | re-convert before generating |

Under the recommendation the workflow is: edit in Roman until happy → convert once → review →
generate. Conversion is the last step before generating, not the first, so the ~78 s is paid once
rather than per edit round.
