# Handoff — current state

Written so a fresh session (or a fresh pod, or a different person) can resume without
reconstructing anything. **Update this at every checkpoint.** The previous incarnation of this
project lost a day of planning because the only copy lived on a pod that was terminated.

Last updated: **2026-08-04**, during Wave 1.

---

## Where things stand

| Wave | Status |
|---|---|
| **0 — Contracts** | ✅ Done, pushed. 24 tests pass, ruff clean. |
| **X1 — Deletions** | ✅ Done, pushed. −5402 lines. |
| **1 — Research** | ✅ Substantially done. Every question that could change the architecture is answered. Two specs fully measured, one blocked on an HF gate, one installed but unmeasured. |
| 2 — Build | Not started. Blocked on Wave 1 for the runtimes only; everything else is unblocked. |
| 3 — Integration | Not started. |
| 4 — Review | Not started. |

Branch: **`rewrite/contracts`**, off `dev`.

## Resuming on a new pod

```bash
GH_USER=<user> GH_TOKEN=<token> bash scripts/pod-bootstrap.sh
```

Rebuilds caches, credentials, repo, backend env, and the research lab. If `/workspace` did NOT
carry over, model weights are gone and must be re-downloaded — that is bandwidth (~7 MB/s from
HuggingFace), not lost work.

Connection details for the current pod are in `.claude/remote.local.md` (gitignored — endpoints
change). Note SSH must use Windows `ssh.exe`; Git Bash cannot see the ssh-agent holding the key.

## ⚠️ Wave 1 was INTERRUPTED (2026-08-04)

All four research agents died at the same instant on a **Claude session limit**, not on any pod or
code failure. Nothing about the pod, the network, or the research approach was at fault.

State at interruption:

| Agent | Reached |
|---|---|
| R1 (F5) | Had launched a resumable background download on the pod (`curl -C -`, retrying). May still be running or finished — **check `/workspace/engines-lab/r1-f5/` before re-downloading anything.** |
| R2b (Chatterbox) | Barely started. |
| R3 (VoxCPM) | Installed, was introspecting the real `generate()` signature. |
| R4 (Urdu) | Was about to build the eval harness; transliteration answer NOT yet obtained. |

**Before relaunching any agent, inspect what is already on disk.** Roughly 43 GB was downloaded
(23 GB uv cache, 5 GB HF cache, ~20 GB of venvs). Re-downloading it costs ~70 minutes at 7 MB/s and
is pure waste. Check each lab directory and each venv first.

The four agent contexts are resumable within the same Claude session; across sessions, relaunch from
`docs/PHASE_A_RESULTS.md` plus whatever is on disk.

## Wave 1 — research in flight

Four agents in `/workspace/engines-lab/<name>/`, each with its own venv, GPU serialized via
`flock /workspace/engines-lab/.gpu.lock`.

| Agent | Target | The question that decides something |
|---|---|---|
| R1 | F5 + 3 checkpoints | How `ai4bharat/IndicF5` loads — raw checkpoint or `trust_remote_code`? Decides whether one runtime class serves all three F5 specs. |
| R2b | Chatterbox ML v3 | Runtime verification (R2's first pass was documentation-only — see below). |
| R3 | VoxCPM 2 | Re-measure ~8 GB / RTF 0.30 on **sm_86**; published figures are Ada. Largest spec, so admission control depends on it. |
| R4 | Urdu pipeline + eval harness | Does `ai4bharat-transliteration` have `ur` in the indic→roman direction? If not, the Perso-Arabic→Devanagari route collapses. |

Findings land in `docs/PHASE_A_RESULTS.md` as they arrive. **Commit them immediately** — they are
the durable output of Wave 1; the venvs and weights are just cache.

### Two lessons already learned

**1. Verification means execution.** R2 produced a report with three ❌ in its own summary table and
concluded "READY TO SHIP: All critical deliverables verified" — having never loaded the model.
Treat any research result claiming verification without a command transcript as unverified. The
plan's top-listed bad idea is Wave 3 implementing against documentation instead of verified
snippets, and this is exactly how that happens.

**2. `df` DOES NOT SHOW THE VOLUME QUOTA. Use `du -sh /workspace`.**

R2 reported "disk quota exceeded". That was dismissed on the basis of `df -h /workspace` reporting
164 TB free — but `/workspace` is a MooseFS mount, and `df` reports the whole **cluster**, not this
volume's quota. The volume was 50 GB and actual usage was ~49.3 GB:

```
23.0 GB  uv-cache
 5.0 GB  hf-cache
 1.2 GB  pip-cache
20.1 GB  engines-lab venvs (r1 4.5 + r2 3.3 + r3 8.9 + r4 3.4)
-------
49.3 GB  vs a 50 GB volume
```

R2's diagnosis was correct and the dismissal was wrong. The volume has since been raised to 200 GB.

**Check capacity with `du -sh /workspace` against the volume size shown in the RunPod console.**
Never with `df`. A wrong reading here sends you debugging dependency resolution for an hour when the
actual failure is "out of space".

Budget note: the uv cache alone reached 23 GB across four runtimes. Four ML stacks plus weights fit
in 200 GB, but not comfortably in 50 GB — `uv cache prune` is worth running between waves.

## Measured pod facts (2026-08-04)

- RTX A5000, 24564 MiB, sm_86, driver 580.159.04 · Ubuntu 24.04.3 · Python 3.12.3 · torch 2.8.0+cu128
- `/` = 30 GB **ephemeral** overlay (4.6 GB/s) · `/workspace` = MooseFS network volume (526 MB/s)
- Network: **~7 MB/s from HuggingFace, ~16 MB/s from PyPI.** Wave 1's wall clock is weight
  downloads, not compute — budget accordingly.
- Present: git, ffmpeg, uv, flock. Missing: **node, npm** (so frontend work happens locally), nvcc,
  espeak-ng (not needed by any chosen runtime).

## What is NOT blocked on Wave 1

Wave 1 gates only the runtime implementations. These can start now against the frozen contracts:

- **X1** ✅ done
- **B2** — `main.py`, `config.py`, `db/**`, `api/**`, media tokens. Depends on `SchedulerProtocol`,
  not on the scheduler; tests run against `tests/fakes/FakeScheduler`.
- **B3** — `domain/**`: `detect_script`, `resolve`, `split_sentences` are all `NotImplementedError`
  stubs with signatures fixed.
- **B1** — the scheduler itself, plus all five of its tests, which run against `FakeWorker` with no
  GPU. Only the three real runtime classes wait for Wave 1.
- **F1–F4** — the whole frontend, against `types/api.ts` and `lib/queryKeys.ts`.
- **D1** — Docker, CI.

## Non-negotiables (full detail in CLAUDE.md and docs/ARCHITECTURE.md)

1. `import torch` must not be reachable from `app.main`. Enforced by
   `test_no_torch_outside_runtimes`. Check by hand with **leading whitespace allowed** — the legacy
   engines import torch inside functions, so an anchored grep reports them clean while the invariant
   is broken.
2. Eviction only inside `_ensure_ready()`, only while holding the GPU-slot semaphore.
3. Routing is pure — no `is_loaded`, ever. That is what made a cold server answer with a sine wave.
4. No silent fallback. `NoRouteError` → 422 listing what would work.
5. Permissive licenses only.
6. Nothing routes until Phase A verifies it. `LanguageSupport.verified=False` is the default and the
   catalog currently resolves nothing — deliberately.

## Wave 1 outcome

| Spec | State |
|---|---|
| `f5_openbible_urdu` | ✅ Verified. Real Urdu audio generated. 6112 MB, 7.0 s load, RTF 0.21 |
| `voxcpm2` | ✅ Verified. 7300 MB, 124 s load, RTF 0.58. Hindi speaker sim **0.678 — below the 0.70 gate**, needs a re-run |
| `chatterbox_ml_v3` | ⚠️ MIT confirmed; venv installed (torch + chatterbox import OK); **nothing measured** |
| `f5_indic` | 🔒 Blocked on the HF gate |
| ~~`f5_openf5_en`~~ | ❌ Dropped — no permissive English F5 exists |

**Design changes forced by research** (the reason Wave 1 ran before Wave 2):

1. **Two F5 loader paths, not one runtime class.** IndicF5 needs
   `AutoModel(trust_remote_code=True)`; OpenBible-Urdu is a raw checkpoint through the stock loader.
2. **English cannot use F5** — routes to Chatterbox.
3. **Reference limit is ~12 s with silent truncation**, not ~6 s. The "8192" was an unrelated rotary
   table (~87 s).
4. **Blank `ref_text` silently loads Whisper** — 39.5 s cold, 5.1 s warm, per request.
5. **VRAM must be sampled concurrently**, not after the fact — post-hoc readings under-report peak
   ~5× because allocator caches are released between requests.
6. **Roman Urdu → Devanagari is one hop and higher quality than Roman → Perso-Arabic** — the
   transliterated route is the better path, not a licensing compromise.

Ready-to-use on the pod: `/workspace/engines-lab/r4-urdu/eval_harness.py` (Whisper large-v3 +
ECAPA-TDNN, both permissive) and `corpus/` with documented provenance.

## Next session — start here

Wave 2 backend agents are fully unblocked against the frozen contracts and need no GPU:
**B1** (scheduler + its five tests, against `FakeWorker`), **B2** (API layer, against
`FakeScheduler`), **B3** (domain implementations). Use Sonnet — this is implementation against frozen
contracts, which is what contracts are for. Two or three agents at a time, not eight: a session limit
kills all in-flight agents at once.

## Open items

- [ ] **Rotate the GitHub PAT.** It was pasted into a chat transcript and is permanently logged.
- [ ] Empty `LEGACY_TORCH_IMPORTERS` once the old engine layer is deleted (blocked on B1/B2/B3
      landing replacements — deleting it now would break the running app).
- [ ] `NOTICE` file with CC-BY-SA attribution for OpenBible-Urdu.
- [ ] `SettingsPage.tsx:88` says "Built with Tauri" — stale, F3's to fix.
- [ ] `main.py:81-83` still lists `tauri://` CORS origins — B2's to remove.
