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
| **1 — Research** | 🔄 In flight. See below. |
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

### Lesson already learned

R2 ran on Haiku, produced a report with three ❌ in its own summary table, and concluded "READY TO
SHIP: All critical deliverables verified" — having never loaded the model. It also misdiagnosed a
dependency-resolution failure as "disk quota exceeded" when the pod's overlay was 1% used.

Treat any research result claiming verification without a command transcript as unverified. The
plan's top-listed bad idea is Wave 3 implementing against documentation instead of verified
snippets, and this is exactly how that happens.

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

## Open items

- [ ] **Rotate the GitHub PAT.** It was pasted into a chat transcript and is permanently logged.
- [ ] Empty `LEGACY_TORCH_IMPORTERS` once the old engine layer is deleted (blocked on B1/B2/B3
      landing replacements — deleting it now would break the running app).
- [ ] `NOTICE` file with CC-BY-SA attribution for OpenBible-Urdu.
- [ ] `SettingsPage.tsx:88` says "Built with Tauri" — stale, F3's to fix.
- [ ] `main.py:81-83` still lists `tauri://` CORS origins — B2's to remove.
