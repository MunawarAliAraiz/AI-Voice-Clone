/**
 * The composer — pick a voice, declare a language, type, generate.
 *
 * The user declares the language; the app detects the script. It deliberately
 * does not guess Roman Urdu from English — both are Latin — so `dir` keys off
 * the DETECTED script, never `language === 'ur'`.
 *
 * Generation is async: `generate()` enqueues a job and returns 202
 * immediately; `useJob` polls it to a terminal status. Closing the tab (or
 * this component unmounting) no longer loses the clip — it lands in the
 * Recent tab regardless, because the job runs server-side independent of
 * this component's lifetime.
 */
import { useEffect, useRef, useState } from 'react';
import { isTerminal, useAnalyzeLlmMutation, useCancelJobMutation, useGenerateMutation, useInvalidateAfterJobSuccess, useJob, useModels } from '../hooks/queries';
import { api, ApiError, mediaUrl } from '../services/api';
import type { DirectedSegmentIn, DirectionAnalyzeResponse, JobStatusResponse, LanguageInfo, ScriptDetectResponse, VoiceProfile } from '../types/api';
import { AudioPlayer } from './AudioPlayer';
import { DirectionPanel } from './DirectionPanel';
import {
  IconAlert,
  IconCheck,
  IconChevronDown,
  IconChevronUp,
  IconCopy,
  IconHistory,
  IconPaste,
  IconSpark,
  IconSpinner,
  IconTrash,
  IconX,
} from './icons';

interface Props {
  voices: VoiceProfile[];
  languages: LanguageInfo[];
  /** Fires exactly once per job, the moment it first reaches a terminal status. */
  onJobSettled?: (job: JobStatusResponse) => void;
  /** Switches to the Recent tab. Undefined hides the pointer entirely. */
  onOpenRecent?: () => void;
}

/**
 * The picker's dropdown option label suffix for a non-recommended model.
 * `experimental` and `commercial_use` are independent axes — a model can be
 * non-commercial and fully verified (OmniVoice), or experimental and fully
 * commercial (the LoRA-less base-checkpoint arms) — so both are named when
 * both apply, rather than one hiding the other.
 */
function modelSuffix(m: { experimental: boolean; commercial_use: boolean }): string {
  const tags: string[] = [];
  if (m.experimental) tags.push('Experimental — lower accuracy');
  if (!m.commercial_use) tags.push('Non-commercial');
  return tags.length ? ` (${tags.join(', ')})` : '';
}

export function Composer({ voices, languages, onJobSettled, onOpenRecent }: Props) {
  const [profileId, setProfileId] = useState<number | null>(null);
  const [language, setLanguage] = useState('ur');
  // null = Auto (let /api/generate's resolve() pick). An explicit id is
  // honored or refused server-side — never silently swapped for something
  // else (same rule routing itself follows, see domain/routing.py::resolve).
  // Pre-filled per language (see DEFAULT_MODEL_BY_LANGUAGE/handleLanguageChange
  // below) rather than left on Auto — still just an explicit pick, not a
  // change to auto-routing semantics.
  const [modelId, setModelId] = useState<string | null>(
    DEFAULT_MODEL_BY_LANGUAGE['ur'] ?? null
  );
  const [speed, setSpeed] = useState<number>(1.0);
  const [stability, setStability] = useState<number>(() => {
    try {
      const saved = localStorage.getItem('vcs_stability');
      return saved !== null ? nearestStabilityOption(Number(saved)) : 70;
    } catch {
      return 70;
    }
  });
  const [expanded, setExpanded] = useState<boolean>(() => {
    try {
      return localStorage.getItem('vcs_textarea_expanded') === 'true';
    } catch {
      return false;
    }
  });
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [text, setText] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [detect, setDetect] = useState<ScriptDetectResponse | null>(null);

  // Runs automatically once there's text — no click needed to see what
  // Speech Direction would do. `applyDirection` below stays opt-in: seeing
  // the analysis is automatic, but affecting the actual generated audio
  // still requires the explicit toggle, so nothing changes without review.
  const [showDirection, setShowDirection] = useState(true);
  const [direction, setDirection] = useState<DirectionAnalyzeResponse | null>(null);
  const [directionLoading, setDirectionLoading] = useState(false);
  const [directionErr, setDirectionErr] = useState<string | null>(null);
  // Off by default even though the preview above is automatic — applying
  // Direction to the actual audio is a separate, explicit opt-in so the
  // user has seen the honesty chip (what this model honors/ignores) before
  // it affects real output. Not reset on collapse: an already-informed
  // choice stays in effect while the composer stays open.
  const [applyDirection, setApplyDirection] = useState(false);
  // Advanced per-segment overrides from the "Advanced" editor inside
  // DirectionPanel, keyed by segment index — SPARSE by construction (only
  // touched segments ever get a key). Lives here, not in DirectionPanel,
  // because it must survive into generate()'s payload and be clearable when
  // a 422 INVALID_DIRECTION_PLAN says the indices went stale.
  const [directionEdits, setDirectionEdits] = useState<Record<number, DirectedSegmentIn>>({});
  // Signature (index:text pairs) of the last analyzed plan's segmentation —
  // used to tell "the text changed enough that old edits may not line up
  // anymore" apart from "the same plan was refetched" (e.g. re-opening the
  // disclosure), which should NOT throw away what the user just customized.
  const lastSegSignature = useRef<string | null>(null);

  const [jobId, setJobId] = useState<number | null>(null);
  const generateMutation = useGenerateMutation();
  const cancelMutation = useCancelJobMutation();
  const { data: job } = useJob(jobId);
  const { data: modelsData } = useModels();
  const invalidateAfterSuccess = useInvalidateAfterJobSuccess();
  const settledJobId = useRef<number | null>(null);

  // "AI suggest" (LLM-backed classification) — an independent job id/poll
  // from the main generate job above. Its result never touches audio; it
  // only pre-fills `directionEdits` (handleEditSegment below), same as a
  // manual per-segment edit.
  const [llmJobId, setLlmJobId] = useState<number | null>(null);
  const [aiSuggestErr, setAiSuggestErr] = useState<string | null>(null);
  const analyzeLlmMutation = useAnalyzeLlmMutation();
  const { data: llmJob } = useJob(llmJobId);
  const settledLlmJobId = useRef<number | null>(null);

  useEffect(() => {
    const first = voices[0];
    if (profileId === null && first) setProfileId(first.id);
  }, [voices, profileId]);

  // Only models that actually claim the selected language, so the picker
  // can never offer a choice /api/generate would refuse with a 422 — same
  // "an explicit id is honored or refused, never silently swapped" contract
  // resolve() itself follows.
  const compatibleModels = (modelsData?.models ?? []).filter((m) =>
    m.languages.some((l) => l.language === language)
  );
  const selectedModel = modelId ? compatibleModels.find((m) => m.id === modelId) : undefined;

  // A manually-picked model that no longer supports the language (the user
  // switched languages after choosing one) falls back to Auto rather than
  // silently sending an id that would now 422.
  // Deliberately keyed on [language, modelsData], not compatibleModels: that
  // array is a fresh reference every render (it's a .filter() above), and
  // depending on it would re-run this effect every render instead of only
  // when the inputs it's derived from actually change.
  useEffect(() => {
    // Wait for the real list before judging compatibility — otherwise this
    // fires on mount against an empty compatibleModels (modelsData still
    // loading) and wipes the language-default pre-selection before the
    // fetch even resolves.
    if (!modelsData) return;
    if (modelId !== null && !compatibleModels.some((m) => m.id === modelId)) {
      setModelId(null);
    }
  }, [language, modelsData]);

  // Fire the settle callback (toast) exactly once per job, the turn it first
  // becomes terminal — not on every subsequent poll of the same finished job.
  useEffect(() => {
    if (!job || !isTerminal(job.status) || settledJobId.current === job.id) return;
    settledJobId.current = job.id;
    onJobSettled?.(job);
    if (job.status === 'succeeded') invalidateAfterSuccess();
  }, [job, onJobSettled, invalidateAfterSuccess]);

  // Adjust default speed when language changes (English speech defaults to 0.9x for natural pace)
  function handleLanguageChange(newLang: string) {
    setLanguage(newLang);
    if (newLang === 'en') {
      setSpeed(0.9);
    } else if (speed === 0.9) {
      setSpeed(1.0);
    }
    // Pre-fill the Model dropdown with the best option for the language
    // instead of leaving it on Auto — still just an explicit `model_id` pick
    // like a manual click (allow_experimental flows through the existing
    // selectedModel?.experimental derivation in generate(), unchanged), so
    // this is a UI convenience only and never widens auto-routing itself.
    setModelId(DEFAULT_MODEL_BY_LANGUAGE[newLang] ?? null);
  }

  function handleStabilityChange(val: number) {
    setStability(val);
    try {
      localStorage.setItem('vcs_stability', String(val));
    } catch {
      // storage unavailable
    }
  }

  function toggleExpanded() {
    const textarea = textareaRef.current;
    const selStart = textarea?.selectionStart;
    const selEnd = textarea?.selectionEnd;
    const scrollTop = textarea?.scrollTop;

    setExpanded((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('vcs_textarea_expanded', String(next));
      } catch {
        // ignore
      }
      return next;
    });

    requestAnimationFrame(() => {
      if (textarea) {
        if (selStart != null && selEnd != null) {
          textarea.setSelectionRange(selStart, selEnd);
        }
        if (scrollTop != null && !expanded) {
          textarea.scrollTop = scrollTop;
        }
        textarea.focus();
      }
    });
  }

  // Debounced live script detection — powers the routability hint and dir.
  useEffect(() => {
    const t = text.trim();
    if (!t) {
      setDetect(null);
      return;
    }
    const h = window.setTimeout(() => {
      api
        .detectScript(t, language)
        .then(setDetect)
        .catch(() => setDetect(null)); // hint only; a failure must not block typing
    }, 400);
    return () => window.clearTimeout(h);
  }, [text, language]);

  // Debounced direction analysis — same pattern as script detection, but only
  // while the "Direction (preview)" disclosure is open, so the preview call
  // doesn't fire on every keystroke for a panel nobody is looking at. A
  // failure here must never block typing or generation — it's rendered as a
  // small muted note inside DirectionPanel, never thrown.
  useEffect(() => {
    const t = text.trim();
    if (!showDirection || !t) {
      setDirection(null);
      setDirectionErr(null);
      setDirectionLoading(false);
      return;
    }
    setDirectionLoading(true);
    const h = window.setTimeout(() => {
      api
        .analyzeDirection(t, language, modelId, selectedModel?.experimental ?? false)
        .then((res) => {
          setDirection(res);
          setDirectionErr(null);
        })
        .catch((e) => {
          setDirection(null);
          setDirectionErr(e instanceof ApiError ? e.message : 'Direction preview unavailable.');
        })
        .finally(() => setDirectionLoading(false));
    }, 400);
    return () => window.clearTimeout(h);
  }, [text, language, showDirection, modelId, selectedModel]);

  // Drop stale Advanced edits when the underlying segmentation actually
  // changed (different segment count/text) — not on every re-fetch, so
  // closing and reopening "Direction (preview)" for the SAME text doesn't
  // throw away what the user just customized.
  useEffect(() => {
    if (!direction) return;
    const sig = direction.plan.segments.map((s) => `${s.index}:${s.text}`).join('|');
    if (lastSegSignature.current !== null && lastSegSignature.current !== sig) {
      setDirectionEdits({});
    }
    lastSegSignature.current = sig;
  }, [direction]);

  function handleEditSegment(segment: DirectedSegmentIn) {
    setDirectionEdits((prev) => ({ ...prev, [segment.index]: segment }));
  }

  function handleResetSegment(index: number) {
    setDirectionEdits((prev) => {
      if (!(index in prev)) return prev;
      const next = { ...prev };
      delete next[index];
      return next;
    });
  }

  function handleResetAllEdits() {
    setDirectionEdits({});
  }

  // Fire exactly once per LLM job, the turn it first goes terminal — mirrors
  // the main job's settle effect above. On success, pair each classified row
  // with the heuristic plan's segment at the same index (for `text`/
  // `pause_after_ms`, which the LLM never classifies) and feed it through
  // handleEditSegment exactly as a manual edit would — this IS the apply
  // step, no separate one.
  useEffect(() => {
    if (!llmJob || !isTerminal(llmJob.status) || settledLlmJobId.current === llmJob.id) return;
    settledLlmJobId.current = llmJob.id;

    if (llmJob.status === 'succeeded') {
      const result = llmJob.result;
      if (result && 'rows' in result && direction) {
        for (const row of result.rows) {
          const seg = direction.plan.segments.find((s) => s.index === row.index);
          if (!seg) continue; // stale index (text changed after the job was enqueued) — skip rather than guess
          handleEditSegment({
            index: row.index,
            emotion: row.emotion,
            intensity: row.intensity,
            energy: row.energy,
            rate: row.rate,
            pause_after_ms: seg.pause_after_ms,
          });
        }
        setAiSuggestErr(null);
      }
    } else if (llmJob.status === 'failed') {
      setAiSuggestErr(llmJob.error?.detail ?? 'AI suggestion failed.');
    }
    // 'cancelled' needs no message — the user asked for it.
  }, [llmJob, direction]);

  async function handleSuggestAi() {
    const t = text.trim();
    if (!t) return;
    setAiSuggestErr(null);
    try {
      const newJob = await analyzeLlmMutation.mutateAsync({ text: t, language });
      settledLlmJobId.current = null;
      setLlmJobId(newJob.id);
    } catch (e) {
      setAiSuggestErr(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function generate() {
    if (profileId === null) return setErr('Add and select a voice first.');
    if (!text.trim()) return setErr('Type something to say.');
    setErr(null);
    const editedSegments = Object.values(directionEdits);
    try {
      const newJob = await generateMutation.mutateAsync({
        profile_id: profileId,
        language,
        model_id: modelId,
        allow_experimental: selectedModel?.experimental ?? false,
        text: text.trim(),
        speed,
        stability,
        apply_direction: applyDirection,
        direction_plan:
          applyDirection && editedSegments.length > 0 ? { segments: editedSegments } : null,
      });
      settledJobId.current = null;
      setJobId(newJob.id);
    } catch (e) {
      if (e instanceof ApiError && e.code === 'INVALID_DIRECTION_PLAN') {
        // The text changed after the Advanced editor's overrides were made,
        // so the segment indices no longer line up server-side (rule 5: no
        // silent fallback — the backend refuses rather than guessing which
        // segment an edit meant). Clear the stale edits and tell the user
        // plainly rather than surfacing the raw 422.
        setDirectionEdits({});
        setErr(
          'Your Speech Direction edits no longer match this text — it changed after you customized segments. The edits were cleared; reopen "Direction (preview)" to review the current segments, then Generate again.'
        );
      } else {
        setErr(e instanceof ApiError ? e.message : String(e));
      }
    }
  }

  /** Ctrl/Cmd+Enter generates, the convention for a "send" textarea. */
  function onKeyDown(e: React.KeyboardEvent) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !busy) {
      e.preventDefault();
      void generate();
    }
  }

  const langs = languages.length ? languages : [];
  const rtl = detect?.is_rtl ?? false;
  const busy = generateMutation.isPending || (job != null && !isTerminal(job.status));
  // Text-editor tools. `copied` is a transient confirmation rather than a
  // toast: the feedback belongs on the button you just pressed, and a toast
  // for "copied" is noise next to a toast for "generation failed".
  const [copied, setCopied] = useState(false);
  const [clipboardError, setClipboardError] = useState<string | null>(null);

  async function copyText() {
    setClipboardError(null);
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access is refused outright on a non-secure origin and can be
      // denied by permission policy. Say so rather than looking like nothing
      // happened — no `.catch(() => {})` here.
      setClipboardError('Your browser blocked clipboard access.');
    }
  }

  async function pasteText() {
    setClipboardError(null);
    try {
      const clip = await navigator.clipboard.readText();
      // Appended at the caret rather than replacing: pasting over text you
      // already typed, with no undo, is the kind of "help" nobody wants.
      const el = textareaRef.current;
      const at = el?.selectionStart ?? text.length;
      const next = (text.slice(0, at) + clip + text.slice(el?.selectionEnd ?? at)).slice(0, 5000);
      setText(next);
    } catch {
      setClipboardError('Your browser blocked clipboard access.');
    }
  }

  const disabled = busy || !voices.length;
  const aiSuggestBusy =
    analyzeLlmMutation.isPending || (llmJob != null && !isTerminal(llmJob.status));

  return (
    <section className="card composer" aria-labelledby="composer-h">
      <header className="card-head">
        <h2 id="composer-h">Generate speech</h2>
        <div className="composer-tools">
          {text.length > 0 && <span className="count">{text.length} / 5000</span>}
          <button
            type="button"
            className="icon-btn tiny"
            onClick={() => void copyText()}
            disabled={!text}
            title="Copy text"
            aria-label="Copy text"
          >
            {copied ? <IconCheck size={13} /> : <IconCopy size={13} />}
          </button>
          <button
            type="button"
            className="icon-btn tiny"
            onClick={() => void pasteText()}
            title="Paste at the cursor"
            aria-label="Paste at the cursor"
          >
            <IconPaste size={13} />
          </button>
          <button
            type="button"
            className="icon-btn tiny danger"
            onClick={() => {
              setText('');
              textareaRef.current?.focus();
            }}
            disabled={!text}
            title="Clear text"
            aria-label="Clear text"
          >
            <IconTrash size={13} />
          </button>
        </div>
      </header>

      {clipboardError && (
        <div className="inline-error" role="alert">
          <IconAlert size={14} /> {clipboardError}
        </div>
      )}

      <div className="row">
        <label className="field">
          <span className="field-label">Voice</span>
          <div className="select-wrap">
            <select
              value={profileId ?? ''}
              onChange={(e) => setProfileId(Number(e.target.value))}
              disabled={!voices.length}
            >
              {voices.length === 0 && <option value="">No voices yet</option>}
              {voices.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </div>
        </label>

        <label className="field">
          <span className="field-label">Language</span>
          <div className="select-wrap">
            <select value={language} onChange={(e) => handleLanguageChange(e.target.value)}>
              {langs.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.display_name}
                </option>
              ))}
            </select>
          </div>
        </label>

        <label className="field">
          <span className="field-label">Model</span>
          <div className="select-wrap">
            <select
              value={modelId ?? ''}
              onChange={(e) => setModelId(e.target.value || null)}
              disabled={!compatibleModels.length}
              title={
                modelId === null
                  ? detect?.would_route_to?.rationale ?? 'Picked automatically for this language and text.'
                  : undefined
              }
            >
              <option value="">
                {compatibleModels.length
                  ? `Auto${detect?.routable && detect.would_route_to ? ` — ${detect.would_route_to.model_display_name}` : ''} (Recommended)`
                  : 'Auto (Recommended)'}
              </option>
              {compatibleModels.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.id === detect?.would_route_to?.model_id
                    ? `${m.display_name} (Recommended)`
                    : `${m.display_name}${modelSuffix(m)}`}
                </option>
              ))}
            </select>
          </div>
          {selectedModel?.caveat && <p className="hint muted">{selectedModel.caveat}</p>}
        </label>

        <label className="field">
          <span className="field-label">Speed</span>
          <div className="select-wrap">
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              <option value={0.75}>0.75x (Slower)</option>
              <option value={0.85}>0.85x (Relaxed)</option>
              <option value={0.9}>0.90x (Recommended EN)</option>
              <option value={1.0}>1.00x (Normal)</option>
              <option value={1.1}>1.10x (Fast)</option>
              <option value={1.25}>1.25x (Faster)</option>
            </select>
          </div>
        </label>

        <label className="field" style={{ gridColumn: '1 / -1' }}>
          <span className="field-label">Delivery style</span>
          <div className="select-wrap">
            <select
              value={stability}
              onChange={(e) => handleStabilityChange(Number(e.target.value))}
              aria-label="Delivery style"
            >
              {STABILITY_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        </label>
      </div>

      <div className="textarea-wrapper">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={expanded ? 16 : 5}
          style={{ height: expanded ? '360px' : '130px' }}
          maxLength={5000}
          dir={rtl ? 'rtl' : 'ltr'}
          placeholder={PLACEHOLDER[language] ?? 'Type your text…'}
          aria-label="Text to speak"
        />
        <button
          type="button"
          className="expand-toggle-btn"
          onClick={toggleExpanded}
          title={expanded ? 'Collapse text area' : 'Expand text area'}
          aria-label={expanded ? 'Show Less' : 'Show More'}
        >
          {expanded ? <IconChevronUp size={12} /> : <IconChevronDown size={12} />}
          <span>{expanded ? 'Show Less' : 'Show More'}</span>
        </button>
      </div>

      <div className="composer-foot">
        <div className="detect-slot" aria-live="polite">
          {detect && (
            <span className={`detect ${detect.routable ? '' : 'bad'}`}>
              {detect.routable ? <IconCheck size={13} /> : <IconAlert size={13} />}
              {detect.routable
                ? `${detect.script} · ${
                    modelId
                      ? compatibleModels.find((m) => m.id === modelId)?.display_name ?? modelId
                      : detect.would_route_to?.model_display_name ?? 'ready'
                  }`
                : (detect.hint ?? 'This text cannot be routed.')}
            </span>
          )}
        </div>
        <kbd className="kbd">Ctrl + ↵</kbd>
      </div>

      <button
        type="button"
        className="disclosure-btn"
        onClick={() => setShowDirection((s) => !s)}
        aria-expanded={showDirection}
      >
        {showDirection ? <IconChevronUp size={12} /> : <IconChevronDown size={12} />}
        <span>Direction (preview)</span>
      </button>

      {showDirection && (
        <DirectionPanel
          data={direction}
          loading={directionLoading}
          error={directionErr}
          applyDirection={applyDirection}
          onApplyDirectionChange={setApplyDirection}
          edits={directionEdits}
          onEditSegment={handleEditSegment}
          onResetSegment={handleResetSegment}
          onResetAllEdits={handleResetAllEdits}
          onSuggestAi={handleSuggestAi}
          aiSuggestLoading={aiSuggestBusy}
          aiSuggestError={aiSuggestErr}
        />
      )}

      {err && (
        <div className="inline-error" role="alert">
          <IconAlert size={14} /> {err}
        </div>
      )}

      <button
        className="btn primary"
        disabled={disabled}
        aria-busy={busy}
        onClick={() => void generate()}
      >
        {busy ? <IconSpinner size={15} /> : <IconSpark size={15} />}
        {busy ? 'Generating…' : 'Generate'}
      </button>

      {job && <JobStatusCard job={job} onCancel={() => cancelMutation.mutate(job.id)} />}

      {/* Studio no longer shows any past generation, so without this there is
          nothing on this screen telling you where your clips went. */}
      {onOpenRecent && (
        <button type="button" className="link composer-recent-link" onClick={onOpenRecent}>
          <IconHistory size={13} /> Your generations are in Recent
        </button>
      )}
    </section>
  );
}

function JobStatusCard({ job, onCancel }: { job: JobStatusResponse; onCancel: () => void }) {
  const r = job.route;
  // This card is only ever rendered for the main generate job (kind
  // 'synthesize'), which per JobStatusResponse's contract always has a route
  // from 'queued' onward — `analyze_llm` jobs (route: null) never reach here,
  // see handleSuggestAi/llmJob above. Guard anyway rather than asserting, so
  // a future misuse fails quiet instead of crashing on `r.rationale`.
  if (!r) return null;

  if (job.status === 'queued' || job.status === 'running') {
    return (
      <div className="result" aria-live="polite">
        <div className="result-head">
          <span className="route-chip solid" title={r.rationale}>
            {r.model_display_name}
            <span className="sep">·</span>
            {r.transform === 'none' ? 'direct' : r.transform}
          </span>
          {r.lossy && <span className="tag warn">lossy</span>}
          {r.experimental && <span className="tag warn" title={r.rationale}>experimental</span>}
        </div>
        <p className="hint center">
          {job.status === 'queued' ? (
            job.position != null && job.position > 0 ? (
              <>Queued — #{job.position + 1} in line{job.eta_sec != null && `, ~${Math.round(job.eta_sec)}s`}</>
            ) : (
              'Starting shortly…'
            )
          ) : (
            <>
              Generating…
              {job.eta_sec != null && ` ~${Math.round(job.eta_sec)}s left`}
              {' — first run after a restart loads the model, which can take a minute.'}
            </>
          )}
        </p>
        {job.status === 'queued' && (
          <button type="button" className="btn-sm danger" onClick={onCancel} style={{ margin: '0 auto' }}>
            <IconX size={13} /> Cancel
          </button>
        )}
      </div>
    );
  }

  if (job.status === 'failed') {
    return (
      <div className="inline-error" role="alert">
        <IconAlert size={14} />
        <span>
          <strong>{job.error?.code ?? 'GENERATION_FAILED'}</strong> — {job.error?.detail ?? 'Generation failed.'}
        </span>
      </div>
    );
  }

  if (job.status === 'cancelled') {
    return (
      <p className="hint center" role="status">
        Cancelled.
      </p>
    );
  }

  // succeeded. 'audio_url' distinguishes a TTSGenerateResponse from an
  // AnalyzeLlmResult — this card is only used for 'synthesize' jobs, but the
  // type is shared, so narrow explicitly rather than casting.
  if (!job.result || !('audio_url' in job.result)) return null;
  return (
    <div className="result">
      <div className="result-head">
        <span className="route-chip solid" title={r.rationale}>
          {r.model_display_name}
          <span className="sep">·</span>
          {r.transform === 'none' ? 'direct' : r.transform}
        </span>
        {r.lossy && <span className="tag warn">lossy</span>}
        {job.result.segment_count > 1 && (
          <span className="tag" title="Rendered as separately-synthesized, pause-joined segments">
            directed · {job.result.segment_count} segments
          </span>
        )}
        <span className="grow" />
        <span className="v-meta">
          {job.result.duration_sec != null && <span>{job.result.duration_sec.toFixed(1)}s</span>}
          {job.result.rtf != null && (
            <>
              <span className="dot" />
              <span>RTF {job.result.rtf.toFixed(2)}</span>
            </>
          )}
        </span>
      </div>
      <AudioPlayer autoPlay src={mediaUrl(job.result.audio_url)} label="generated audio" />
    </div>
  );
}

// "Stability" is how closely each generation sticks to the reference voice's
// exact vocal texture vs. varying naturally from run to run — not emotional
// tone (that's the separate Direction feature below). Plain-language
// buckets over a 0-100 raw dial so nobody has to guess what a percentage means.
const STABILITY_OPTIONS = [
  { value: 30, label: '🎭 More Expressive' },
  { value: 50, label: '🎨 Balanced' },
  { value: 70, label: '🎯 Consistent (Recommended)' },
  { value: 90, label: '🪨 Very Stable' },
];

function nearestStabilityOption(v: number): number {
  return STABILITY_OPTIONS.reduce(
    (best, o) => (Math.abs(o.value - v) < Math.abs(best - v) ? o.value : best),
    STABILITY_OPTIONS[0]!.value
  );
}

const PLACEHOLDER: Record<string, string> = {
  en: 'Hello, how are you today?',
  ur: 'Aap kaise hain? Aaj mausam bohat acha hai.',
};

// Pre-fills the Model dropdown per language instead of leaving it on Auto —
// ur -> OmniVoice (best pronunciation, still explicitly experimental), en ->
// VoxCPM 2 (the verified default). Still just an explicit model_id pick, same
// as a manual click; see handleLanguageChange and modelId's initial state.
const DEFAULT_MODEL_BY_LANGUAGE: Record<string, string> = {
  ur: 'omnivoice_urdu',
  en: 'voxcpm2',
};
