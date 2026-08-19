# The Convert tab — design, decisions, and what is not built

> **The YouTube fetch was removed 2026-08-19, and the tab renamed "Import" → "Convert".** YouTube
> hard-blocks datacenter IPs (RunPod) with "Sign in to confirm you're not a bot", and getting past it
> needs a fragile stack (a logged-in cookies file + Deno + the EJS challenge solver + new caption-format
> handling) that breaks every time YouTube changes. Manual paste is simpler and reliable. So the tab now
> takes text the user PASTES (`POST /api/transcript/prepare`, pure CPU: chunk + detect script) and the
> yt-dlp fetch, the SSRF guard (`domain/youtube.py`), the track picker, and chapter grouping are all
> gone. **Everything below about the transliterator, why Hindi is a source format, and the chunking
> rules still applies** — only the *input* changed from a URL to pasted text. (English→Urdu
> *translation*, a different operation, is a planned follow-up.)

Paste a Hindi (Devanagari), Roman Urdu, or Urdu-script script; convert its writing system a part at a
time; send it to the editor.

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

## What was built (the YouTube implementation — REMOVED 2026-08-19)

> Everything in this section describes the **YouTube fetch that was removed** (see the note at the
> top). It is kept for the *reasoning* — the SSRF argument, the newline-as-pause rule, why chapters
> chunk the way they do — which still governs the code that replaced it. **What exists now** is
> `POST /api/transcript/prepare`: it takes the pasted text directly, detects the script with
> `profile_text`, and chunks it with `chunk_for_synthesis` (paragraphs preserved as pauses, `index`
> global). `domain/youtube.py`, `parse_video_id`, the track picker, and chapter grouping are gone.

### `backend/app/domain/youtube.py` — pure, no network *(removed)*

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

### Chapters (2026-08-18)

yt-dlp already returns a video's chapters and they used to be dropped on the floor. They now drive
**chunk-per-chapter**: `parse_chapters` (tolerant, never raises — third-party data) reads them,
`group_cues` assigns each cue to a chapter by `start_sec` (`bisect_right`, half-open boundaries),
and `cues_to_text` + `chunk_for_synthesis` run **per group**, so a part never straddles a chapter —
a chapter boundary is a real content boundary and also the paragraph break (`"\n\n"`, ~380 ms of
deliberate silence) that CLAUDE.md's newline rule is about.

Non-obvious properties, each with a test:

- **No chapters → byte-identical to before.** `group_cues(cues, []) == [(None, cues)]`, one group,
  no join, so the `text` and `chunks` are exactly what they were. This is the invariant that keeps
  chapters an enhancement rather than a new requirement.
- **`index` is renumbered globally** across groups. `chunk_for_synthesis` numbers from 0 per call,
  so per-chapter chunking would otherwise yield three parts all called `0` — and the UI keys
  per-part state and conversion remaps on `chunk.index`, so a collision lands part 8's conversion on
  part 1. The exact silent wrong-answer bug `batchIndexes` exists to prevent.
- **No per-chunk `start_sec`.** The cheap version (stamp the chapter's start on all its parts) is a
  precise-looking wrong number — `normalize_whitespace` destroys the newlines `cues_to_text` emitted,
  so a chunk is not a substring of its group and offsets can't be recovered. Timestamps go on the
  **chapter heading only**, where they are true.
- **Script detected once, from the whole transcript**, and passed to every per-group call — an
  English-heavy chapter must not pick a different terminator set than the one beside it.
- **A short chapter still gets its own part.** `min_chars` merging is intra-call, so a 40-char
  chapter the author wrote becomes an unmergeable 40-char part rather than being folded into its
  neighbour and vanishing from the jump list. Surfaced, like `ends_on_sentence=False`, not hidden.
- **Truncation is reported, not just logged.** `transcript_max_chars` is applied while walking groups
  (not on the joined string, which would leave chunks referencing absent text) and sets
  `truncated: True` so the UI can say so — the old behaviour was a `logger.warning` the user never saw.

### `frontend/src/components/TranscriptPanel.tsx` — the Import tab

Ends at "put this in the editor", never at "generate this". The imported **caption is read-only** —
a conversion *under review* is editable (see "Where the two copies live" below), but the source it
was derived from is not, because two editable copies of one text is how they drift apart.

As of 2026-08-18 the tab renders parts as a collapsible table of contents (`TranscriptPartRow` +
`useTranscriptParts`) grouped by chapter, with a jump list and a bulk-select bar, rather than one
wall of fully-expanded parts. Actions live inside the one expanded part they can apply to.

Text crosses tabs via a `pendingText` prop carrying `{text, token}` — a **monotonic token, not the
string**. Keying the effect on the string would silently ignore sending the same chunk twice, which
is a real thing to want when working through a transcript part by part.

---

## Devanagari → Perso-Arabic: built, ungated

The conversion path landed 2026-08-17. `build_system_prompt` takes a source script, which selects
the header wording, the turn prefix and the exemplar set **together** — they are one decision, not
three, because a prompt that says "Hindi" over six `Roman:` turns is worse than either half alone.
`validate_transliteration`'s echo check lost its `Script.LATIN` condition, which had made a
Devanagari echo report as "replied in the wrong script": true, and it sent the user to fix the wrong
thing.

**The exemplars are derived, not written.** The Urdu side of all six is byte-identical to the Roman
set — the strings A3 run 3 passed on by ear. This is a safety property: authoring six new gold Urdu
strings would put unreviewed Urdu into the *prompt*, where an error does not merely score badly, it
teaches the model the error. (There is already an open task for native-speaker review of gold
strings written that way.) Writing a Devanagari *input* for a known-good Urdu *output* is a far
weaker claim. Five hard cases carry over unchanged; the sixth cannot — SMS orthography with dropped
vowels has no Devanagari form — so that slot demonstrates the **danda** (`।` U+0964), which has no
counterpart in the target.

**Presence, not dominance, picks the source set.** `detect_script` returns MIXED for the ordinary
shape of a Hindi caption carrying English words, and MIXED would fall back to Latin — showing the
model six `Roman:` examples for text it cannot read as Roman. `source_script_of` asks which exemplar
set has anything to *say* about the input, and the Latin set has nothing to say about Devanagari.
It is detected in the handler and never taken from the request: the user declares the language, the
code detects the script.

**One thing the exemplars deliberately do not decide:** an English loanword already spelled in
Devanagari (मीटिंग for *meeting*). Auto-generated Hindi captions are full of them and the Latin
contract's rule 2 has nothing to say — there are no Latin letters to preserve. Converting it to
میٹنگ and leaving it as मीटिंग are both defensible, and **nothing has measured which one OmniVoice
says better**. An exemplar would be inventing that answer, so there is none. The gate below decides
it.

## What is NOT built

- **The listening gate.** R4b measured the *reverse* hop compounding errors badly (مجھے →
  "majhay" → मझे). Direct Devanagari → Perso-Arabic is a different and probably easier mapping but
  **has never been measured here**. Add a Devanagari arm to `eval/run_roman_arabic_probe.py` and run
  the A3 protocol end to end. Synthesis is unseeded — sample repeatedly, listen blind, and remember
  numeric screens can only fail a candidate, never approve one.

  Until it passes, the Import tab keeps disabling Send on a Devanagari transcript and keeps saying
  the feature is still being validated. **That message is the truth, not a placeholder — do not
  remove it because the backend now works.** Every job result carries `source_script` for the same
  reason: a conversion produced by the ungated exemplar set must be identifiable as one.
- **The Devanagari path on a GPU.** The `latin → perso_arabic` hop is GPU-verified and resident;
  `.venv-gemma` was provisioned on the A40 pod 2026-08-17 and Gemma is now warmed at startup. What
  remains unrun on a GPU is specifically the **Devanagari** arm's listening gate (above).

---

## The Roman-draft question (DECIDED 2026-08-18)

Proposal was: type/import **Roman Urdu**, keep it as the readable editable draft, convert to
Perso-Arabic for OmniVoice.

Storing both is free — `generation_history.resolved_text` already exists for exactly this and is
simply not exposed in `HistoryItem` or `frontend/src/types/api.ts`. The real cost is **staleness**:
edit the Roman after converting and the Perso-Arabic is stale, and silently synthesizing either is
golden rule 5's family.

| Approach | Edit in | Cost |
|---|---|---|
| Roman is truth, re-convert every edit | Roman | ~~~78 s Gemma load per edit round~~ **obsolete** — see below |
| Convert once, then edit the Perso-Arabic | Perso-Arabic | none, but harder to read |
| **Both kept; editing Roman marks Urdu stale and BLOCKS Generate** ← chosen | Roman | re-convert before generating |

**Why the ~78 s objection is gone.** It assumed a load-convert-unload transliterator. Gemma has been
**resident since 2026-08-17** (idle-killed, warmed at startup — `TransliteratorScheduler`), so a
re-convert is **~5 s**, not ~78 s. That collapses the only cost the recommended row carried and is
why "re-convert every edit round" is now nearly frictionless rather than a compromise.

The chosen workflow: edit in Roman until happy → convert once → review → generate. Conversion is the
last step before generating, not the first.

### Where the two copies live, and why it is not one editable box

The earlier "the transcript box is **read-only**" is still true of the *source*, but it is only half
the picture now that conversions are edited in place:

- The **caption is read-only** and never mutated — it is what makes a conversion checkable (you can
  always see what it was derived from) and it is the one copy that must not drift.
- A **conversion under review is editable**, but it is not a second copy of the transcript — it is a
  suggestion being corrected before it leaves. `useTranscriptParts` keeps them apart:
  `source` (readonly) vs `draft`/`converted`, with `outgoing = draft ?? converted ?? source` as the
  single answer to "what does this part hand onward". Editing marks the part `edited`; a Perso-Arabic
  under review that gets its Roman edited goes **stale** and is blocked from Generate until
  re-converted (golden rule 5).

### Convert-on-generate lives in the CLIENT, on purpose

When the owner picks an Urdu-script-only model (OmniVoice declares only `(ur, ARABIC)`) and types
Roman Urdu, `resolve()` returns `NoRouteError` and rightly refuses to swap the chosen model. The
Composer handles this **client-side**: on Generate it runs the conversion job, shows the Perso-Arabic
for review, and one more tap generates. `resolve()` and `TransformKind` are **not** taught about the
transliterator — a `latin → perso_arabic` `TransformKind` would be exactly the routing-transform
shortcut golden rules 4/5 forbid, and `jobs/handlers/transliterate.py`'s output is "editable text a
human reads and corrects before generating — never a routing transform applied behind their back".
Do not re-derive that shortcut; the sequencing belongs in the UI where the review step can exist.
