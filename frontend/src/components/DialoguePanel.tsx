import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useJob, useModels } from '../hooks/queries';
import { api, ApiError, mediaUrl } from '../services/api';
import type { JobStatusResponse, LanguageInfo, ModelSummary, VoiceProfile } from '../types/api';
import { AudioPlayer } from './AudioPlayer';

type Speaker = '1' | '2' | '3';
type Snapshot = {
  text: string; speaker: Speaker; profileId: number; language: string;
  modelId: string | null; allowExperimental: boolean;
};
type Attempt = { jobId: number; snapshot: Snapshot };
type Line = {
  id: string; speaker: Speaker; language: string; text: string; pause: number;
  modelId: string | null; allowExperimental: boolean; personalUse: boolean;
  attempts: Attempt[];
};
type Draft = { version: 1; voices: Record<Speaker, number | null>; lines: Line[] };
type Assembly = {
  audio_url: string; duration_sec: number;
  spans: { job_id: number; start_sec: number; end_sec: number }[];
};
interface Props {
  voices: VoiceProfile[];
  languages: LanguageInfo[];
  onJobQueued?: (job: JobStatusResponse) => void;
  onOpenRecent?: () => void;
}

const SPEAKERS: Speaker[] = ['1', '2', '3'];
const controls = { display: 'flex', flexWrap: 'wrap' as const, gap: 'var(--space-2)', alignItems: 'center' };
const buttonStyle = { minHeight: 44 };

function newLine(speaker: Speaker = '1'): Line {
  return { id: crypto.randomUUID(), speaker, language: 'ur', text: '', pause: 0.25,
    modelId: null, allowExperimental: false, personalUse: false, attempts: [] };
}

function emptyDraft(): Draft {
  return { version: 1, voices: { '1': null, '2': null, '3': null }, lines: SPEAKERS.map(newLine) };
}

function loadDraft(value: unknown): Draft | null {
  try {
    if (typeof value !== 'object' || value === null) return null;
    const saved = value as Draft;
    if (saved.version !== 1 || !saved.voices || !Array.isArray(saved.lines) ||
        saved.lines.length < 1 || saved.lines.length > 50) return null;
    for (const speaker of SPEAKERS) {
      const id = saved.voices[speaker];
      if (id !== null && (!Number.isInteger(id) || Number(id) <= 0)) return null;
    }
    if (new Set(saved.lines.map((line) => line.id)).size !== saved.lines.length) return null;
    for (const line of saved.lines) {
      if (typeof line.id !== 'string' || !line.id || !SPEAKERS.includes(line.speaker) ||
          typeof line.text !== 'string' || line.text.length > 5000 ||
          typeof line.language !== 'string' || typeof line.pause !== 'number' ||
          !Number.isFinite(line.pause) || line.pause < 0 || line.pause > 60 ||
          (line.modelId !== null && typeof line.modelId !== 'string') ||
          typeof line.allowExperimental !== 'boolean' || typeof line.personalUse !== 'boolean' ||
          !Array.isArray(line.attempts)) return null;
      for (const attempt of line.attempts) {
        const snapshot = attempt.snapshot;
        if (!Number.isInteger(attempt.jobId) || attempt.jobId <= 0 || !snapshot ||
            typeof snapshot.text !== 'string' || !SPEAKERS.includes(snapshot.speaker) ||
            !Number.isInteger(snapshot.profileId) || snapshot.profileId <= 0 ||
            typeof snapshot.language !== 'string' ||
            (snapshot.modelId !== null && typeof snapshot.modelId !== 'string') ||
            typeof snapshot.allowExperimental !== 'boolean') return null;
      }
    }
    return saved;
  } catch { return null; }
}

// Serial writes survive tab unmounts. A reopened tab waits for them before GET.
let draftWrites: Promise<void> = Promise.resolve();
let unsavedDraft: Draft | null = null;
let draftWriteError = '';
let liveDraft: Draft | null = null;
const draftSubscribers = new Set<(value: Draft) => void>();

function persistDraft(draft: Draft): Promise<void> {
  unsavedDraft = draft;
  draftWrites = draftWrites.catch(() => {}).then(async () => {
    try {
      await api.saveDialogueDraft({ draft });
      if (unsavedDraft === draft) { unsavedDraft = null; draftWriteError = ''; }
    } catch (cause) {
      if (unsavedDraft === draft) draftWriteError = message(cause);
      throw cause;
    }
  });
  return draftWrites;
}

function snapshotOf(line: Line, draft: Draft): Snapshot | null {
  const profileId = draft.voices[line.speaker];
  return profileId == null ? null : {
    text: line.text.trim(), speaker: line.speaker, profileId, language: line.language,
    modelId: line.modelId, allowExperimental: line.allowExperimental,
  };
}

function matches(snapshot: Snapshot, current: Snapshot | null): boolean {
  return current !== null && snapshot.text === current.text && snapshot.speaker === current.speaker &&
    snapshot.profileId === current.profileId && snapshot.language === current.language &&
    snapshot.modelId === current.modelId && snapshot.allowExperimental === current.allowExperimental;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function DialoguePanel({ voices, languages, onJobQueued, onOpenRecent }: Props) {
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [permission, setPermission] = useState(false);
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [jobs, setJobs] = useState<Record<number, JobStatusResponse>>({});
  const [missingJobs, setMissingJobs] = useState<Set<number>>(new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [storageError, setStorageError] = useState('');
  const [batchBusy, setBatchBusy] = useState(false);
  const [assembling, setAssembling] = useState(false);
  const [assembly, setAssembly] = useState<{ result: Assembly; signature: string } | null>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const mounted = useRef(false);
  const loadedRef = useRef(false);
  const latestDraft = useRef(draft);
  const savedSignature = useRef('');
  latestDraft.current = draft;
  if (loadedRef.current) liveDraft = draft;
  const modelsQuery = useModels();
  const models = modelsQuery.data?.models ?? [];
  const queryClient = useQueryClient();
  const activeVoices = voices.filter((voice) => voice.is_active);

  const save = useCallback((value: Draft) => {
    const signature = JSON.stringify(value);
    if (signature === savedSignature.current && unsavedDraft === null) return;
    setSaving(true);
    void persistDraft(value).then(() => {
      savedSignature.current = signature;
      if (mounted.current && latestDraft.current === value) { setStorageError(''); setSaving(false); }
    }).catch((cause) => {
      if (mounted.current && latestDraft.current === value) {
        setStorageError(`Draft changes are not saved: ${message(cause)}. Keep the app open and retry saving.`);
        setSaving(false);
      }
    });
  }, []);

  useEffect(() => {
    mounted.current = true;
    const receive = (value: Draft) => {
      if (loadedRef.current) { latestDraft.current = value; setDraft(value); }
    };
    draftSubscribers.add(receive);
    return () => {
      mounted.current = false;
      draftSubscribers.delete(receive);
      if (loadedRef.current && (JSON.stringify(latestDraft.current) !== savedSignature.current || unsavedDraft !== null)) {
        // Flush the debounce on tab switches; the next mount waits for this write.
        void persistDraft(latestDraft.current).catch(() => {});
      }
    };
  }, []);

  useEffect(() => {
    let active = true;
    setLoadError('');
    void (async () => {
      try {
        let result: Awaited<ReturnType<typeof api.dialogueDraft>>;
        for (;;) {
          const writes = draftWrites;
          await writes.catch(() => {});
          if (!active) return;
          result = await api.dialogueDraft();
          if (writes === draftWrites) break;
        }
        if (!active) return;
        const saved = result.draft === null ? emptyDraft() : loadDraft(result.draft);
        if (!saved) throw new Error('The saved dialogue draft is invalid or uses an unsupported version. It has been preserved.');
        savedSignature.current = JSON.stringify(saved);
        // Retain edits in memory after a failed save, rather than replacing them with an older file.
        const recovered = unsavedDraft ?? saved;
        liveDraft = recovered;
        latestDraft.current = recovered;
        setDraft(recovered);
        if (draftWriteError) setStorageError(`Draft changes are not saved: ${draftWriteError}. Retry saving.`);
        loadedRef.current = true;
        setLoaded(true);
      } catch (cause) {
        if (active) setLoadError(`Dialogue draft could not be loaded: ${message(cause)}`);
      }
    })();
    return () => { active = false; };
  }, [loadAttempt]);

  useEffect(() => {
    if (!loaded) return;
    if (JSON.stringify(draft) === savedSignature.current && unsavedDraft === null) {
      setSaving(false);
      return;
    }
    setSaving(true);
    const timer = window.setTimeout(() => save(draft), 400);
    return () => window.clearTimeout(timer);
  }, [draft, loaded, save]);

  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);

  const updateJob = useCallback((job: JobStatusResponse) => {
    setJobs((current) => ({ ...current, [job.id]: job }));
  }, []);
  const markMissing = useCallback((jobId: number) => {
    setMissingJobs((current) => new Set(current).add(jobId));
  }, []);

  function patchLine(id: string, changes: Partial<Line>) {
    setDraft((current) => ({ ...current,
      lines: current.lines.map((line) => line.id === id ? { ...line, ...changes } : line),
    }));
    setErrors((current) => ({ ...current, [id]: '' }));
  }

  function validation(line: Line): string {
    if (!permission) return 'Confirm that you have permission to use the assigned voices.';
    const profileId = draft.voices[line.speaker];
    if (!activeVoices.some((voice) => voice.id === profileId)) return `Choose an available voice for Speaker ${line.speaker}.`;
    if (!line.text.trim()) return 'Enter the words for this line.';
    if (!languages.some((language) => language.code === line.language)) return 'Choose an available language.';
    if (!Number.isFinite(line.pause) || line.pause < 0 || line.pause > 60) return 'Pause must be between 0 and 60 seconds.';
    if (line.modelId) {
      const model = models.find((item) => item.id === line.modelId);
      if (!model) return 'Choose an available model or Auto.';
      if (!model.languages.some((support) => support.language === line.language)) return 'This model does not support the declared language.';
      if (model.experimental && !line.allowExperimental) return 'Accept the experimental model caveat or choose another model.';
      if (!model.commercial_use && !line.personalUse) return 'Confirm personal, non-commercial use or choose another model.';
      if (model.needs_reference_text && !activeVoices.find((voice) => voice.id === profileId)?.transcript?.trim()) {
        return 'This model needs a reference transcript. Add it to the selected voice or choose another model.';
      }
    }
    return '';
  }

  function recordAttempt(lineId: string, job: JobStatusResponse, snapshot: Snapshot) {
    const append = (current: Draft): Draft => ({ ...current, lines: current.lines.map((line) => line.id === lineId
      ? { ...line, attempts: line.attempts.some((attempt) => attempt.jobId === job.id)
        ? line.attempts : [...line.attempts, { jobId: job.id, snapshot }] } : line) });
    if (mounted.current) {
      setDraft(append);
      updateJob(job);
    } else {
      // A request accepted after switching tabs must still retain its line association.
      const recovered = append(liveDraft ?? unsavedDraft ?? latestDraft.current);
      liveDraft = recovered;
      latestDraft.current = recovered;
      void persistDraft(recovered).catch(() => {});
      for (const receive of draftSubscribers) receive(recovered);
    }
    void queryClient.invalidateQueries({ queryKey: ['jobs'] });
    onJobQueued?.(job);
  }

  async function generateLine(line: Line, retry = false): Promise<boolean> {
    const problem = validation(line);
    const snapshot = snapshotOf(line, draft);
    if (problem || !snapshot) {
      setErrors((current) => ({ ...current, [line.id]: problem || 'Assign a voice first.' }));
      setError('Review the highlighted dialogue line before generating.');
      return false;
    }
    setPending((current) => new Set(current).add(line.id));
    setErrors((current) => ({ ...current, [line.id]: '' }));
    setError('');
    try {
      const previous = line.attempts[line.attempts.length - 1];
      const job = retry && previous && matches(previous.snapshot, snapshot)
        ? await api.retryJob(previous.jobId)
        : await api.generate({
          text: snapshot.text, profile_id: snapshot.profileId, language: snapshot.language,
          model_id: snapshot.modelId, allow_experimental: snapshot.allowExperimental,
          title: `Dialogue · Speaker ${snapshot.speaker} · ${snapshot.text.slice(0, 60)}`,
          output_format: 'wav',
        });
      recordAttempt(line.id, job, snapshot);
      return true;
    } catch (cause) {
      setErrors((current) => ({ ...current, [line.id]: message(cause) }));
      setError('A dialogue line could not be queued. Other accepted jobs continue in Recent.');
      return false;
    } finally {
      setPending((current) => { const next = new Set(current); next.delete(line.id); return next; });
    }
  }

  function inFlight(line: Line): boolean {
    const last = line.attempts[line.attempts.length - 1];
    const job = last ? jobs[last.jobId] : undefined;
    return pending.has(line.id) || Boolean(last && ((!job && !missingJobs.has(last.jobId)) ||
      job?.status === 'queued' || job?.status === 'running'));
  }

  function ready(line: Line): boolean {
    const last = line.attempts[line.attempts.length - 1];
    const job = last ? jobs[last.jobId] : undefined;
    return Boolean(last && matches(last.snapshot, snapshotOf(line, draft)) &&
      job?.status === 'succeeded' && !job.is_fake && job.result && 'audio_url' in job.result);
  }

  async function generateMissing() {
    const todo = draft.lines.filter((line) => !ready(line) && !inFlight(line));
    const problems = Object.fromEntries(todo.map((line) => [line.id, validation(line)]).filter(([, value]) => value));
    if (Object.keys(problems).length) {
      setErrors(problems); setError('Review the highlighted dialogue lines before generating.'); return;
    }
    setBatchBusy(true);
    try { for (const line of todo) { if (!await generateLine(line)) break; } }
    finally { setBatchBusy(false); }
  }

  async function cancel(jobId: number) {
    try {
      await api.cancelJob(jobId);
      await queryClient.invalidateQueries({ queryKey: ['job', jobId] });
      await queryClient.invalidateQueries({ queryKey: ['jobs'] });
    } catch (cause) { setError(message(cause)); }
  }

  function move(index: number, delta: number) {
    setDraft((current) => {
      const lines = [...current.lines];
      const item = lines.splice(index, 1)[0];
      if (item) lines.splice(index + delta, 0, item);
      return { ...current, lines };
    });
  }

  const assemblySignature = JSON.stringify(draft.lines.map((line) => ({
    line: snapshotOf(line, draft), jobId: line.attempts[line.attempts.length - 1]?.jobId, pause: line.pause,
  })));
  const allReady = draft.lines.every(ready);

  async function assemble() {
    if (!allReady) return;
    setAssembling(true); setError('');
    const signature = assemblySignature;
    try {
      const result = await api.assembleDialogue({ lines: draft.lines.map((line) => ({
        job_id: line.attempts[line.attempts.length - 1]!.jobId, pause_after_sec: line.pause,
      })) });
      setAssembly({ result, signature });
    } catch (cause) { setError(message(cause)); }
    finally { setAssembling(false); }
  }

  if (!loaded) return (
    <section className="card" aria-labelledby="dialogue-heading" style={{ padding: 'var(--space-5)' }}>
      <header className="card-head"><h2 id="dialogue-heading">Scripted dialogue</h2></header>
      {loadError ? <>
        <p className="inline-error" role="alert">{loadError}</p>
        <button className="btn-sm" style={buttonStyle} onClick={() => setLoadAttempt((value) => value + 1)}>Retry loading draft</button>
      </> : <p className="muted" role="status">Loading your saved dialogue draft…</p>}
    </section>
  );

  return (
    <section aria-labelledby="dialogue-heading" style={{ display: 'grid', gap: 'var(--space-4)' }}>
      <div className="card" style={{ padding: 'var(--space-5)' }}>
        <header className="card-head"><h2 id="dialogue-heading">Scripted dialogue</h2></header>
        <p className="muted">Assign voices, write the conversation, then generate each line. You can revise one line and keep the others.</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 200px), 1fr))', gap: 'var(--space-3)' }}>
          {SPEAKERS.map((speaker) => (
            <label className="field" key={speaker}>
              <span className="field-label">Speaker {speaker} voice</span>
              <div className="select-wrap"><select value={draft.voices[speaker] ?? ''}
                disabled={batchBusy} onChange={(event) => setDraft((current) => ({ ...current, voices: {
                  ...current.voices, [speaker]: event.target.value ? Number(event.target.value) : null,
                } }))}>
                <option value="">Choose an enrolled voice</option>
                {draft.voices[speaker] != null && !activeVoices.some((voice) => voice.id === draft.voices[speaker]) &&
                  <option value={draft.voices[speaker]!}>Previously assigned voice is unavailable</option>}
                {activeVoices.map((voice) => <option key={voice.id} value={voice.id}>{voice.name}</option>)}
              </select></div>
            </label>
          ))}
        </div>
        <label style={{ ...controls, marginTop: 'var(--space-4)', minHeight: 44 }}>
          <input type="checkbox" checked={permission} disabled={batchBusy || pending.size > 0}
            onChange={(event) => setPermission(event.target.checked)} />
          I have permission to use these voices.
        </label>
        <p className="hint muted" role="status">{saving ? 'Saving draft…' : storageError ? 'Draft has unsaved changes.' : 'Draft saved on this device.'} Voice audio and accepted jobs stay in your local library and Recent.</p>
        {storageError && <div className="inline-error" role="alert">{storageError}
          <button className="btn-sm ghost" style={buttonStyle} disabled={saving} onClick={() => save(draft)}>Retry saving</button>
        </div>}
        {modelsQuery.error && <p className="inline-error" role="alert">Models could not be loaded: {message(modelsQuery.error)}</p>}
        {error && <div className="inline-error" role="alert" tabIndex={-1} ref={errorRef}
          style={{ display: 'block' }}>{error}
          {Object.keys(errors).filter((id) => errors[id]).map((id) => (
            <div key={id}><a href={`#dialogue-text-${id}`} className="link">{errors[id]}</a></div>
          ))}
        </div>}
      </div>

      {draft.lines.map((line, index) => {
        const selected = models.find((model) => model.id === line.modelId);
        const compatible = models.filter((model) => model.languages.some((support) => support.language === line.language));
        const last = line.attempts[line.attempts.length - 1];
        const job = last ? jobs[last.jobId] : undefined;
        const stale = last && !matches(last.snapshot, snapshotOf(line, draft));
        const busy = inFlight(line);
        return (
          <article className="card" key={line.id} aria-labelledby={`dialogue-line-${line.id}`}
            style={{ padding: 'var(--space-5)', minWidth: 0 }}>
            <header style={{ ...controls, justifyContent: 'space-between', marginBottom: 'var(--space-3)' }}>
              <h3 id={`dialogue-line-${line.id}`} style={{ margin: 0 }}>Line {index + 1}</h3>
              <div style={controls}>
                <button className="btn-sm ghost" style={buttonStyle} disabled={index === 0} onClick={() => move(index, -1)} aria-label={`Move line ${index + 1} earlier`}>Move up</button>
                <button className="btn-sm ghost" style={buttonStyle} disabled={index === draft.lines.length - 1} onClick={() => move(index, 1)} aria-label={`Move line ${index + 1} later`}>Move down</button>
                <button className="btn-sm danger" style={buttonStyle} disabled={draft.lines.length === 1 || busy || batchBusy}
                  onClick={() => setDraft((current) => ({ ...current, lines: current.lines.filter((item) => item.id !== line.id) }))} aria-label={`Remove line ${index + 1}`}>Remove</button>
              </div>
            </header>
            <div className="row">
              <label className="field"><span className="field-label">Speaker</span>
                <div className="select-wrap"><select disabled={batchBusy} value={line.speaker} onChange={(event) => patchLine(line.id, { speaker: event.target.value as Speaker })}>
                  {SPEAKERS.map((speaker) => <option key={speaker} value={speaker}>Speaker {speaker}</option>)}
                </select></div>
              </label>
              <label className="field"><span className="field-label">Language</span>
                <div className="select-wrap"><select disabled={batchBusy} value={line.language} onChange={(event) => patchLine(line.id, { language: event.target.value, modelId: null, allowExperimental: false, personalUse: false })}>
                  {!languages.some((language) => language.code === line.language) && <option value={line.language}>Choose a language</option>}
                  {languages.map((language) => <option key={language.code} value={language.code}>{language.display_name}</option>)}
                </select></div>
              </label>
              <label className="field"><span className="field-label">Model</span>
                <div className="select-wrap"><select disabled={batchBusy} value={line.modelId ?? ''} onChange={(event) => patchLine(line.id, { modelId: event.target.value || null, allowExperimental: false, personalUse: false })}>
                  <option value="">Auto · verified routing</option>
                  {line.modelId && !compatible.some((model) => model.id === line.modelId) && <option value={line.modelId}>Previous model is unavailable</option>}
                  {compatible.map((model) => <option key={model.id} value={model.id}>{modelLabel(model)}</option>)}
                </select></div>
              </label>
              <label className="field"><span className="field-label">Pause after line (seconds)</span>
                <input type="number" min={0} max={60} step={0.05} value={line.pause} onChange={(event) => patchLine(line.id, { pause: Number(event.target.value) })} />
              </label>
            </div>
            {selected?.experimental && <label style={{ ...controls, minHeight: 44, marginTop: 'var(--space-3)' }}>
              <input type="checkbox" checked={line.allowExperimental} disabled={batchBusy} onChange={(event) => patchLine(line.id, { allowExperimental: event.target.checked })} />
              Use this experimental model: {selected.caveat || 'Accuracy and voice identity have not passed the release gate.'}
            </label>}
            {selected && !selected.commercial_use && <label style={{ ...controls, minHeight: 44 }}>
              <input type="checkbox" checked={line.personalUse} disabled={batchBusy} onChange={(event) => patchLine(line.id, { personalUse: event.target.checked })} />
              This generation is for personal, non-commercial use.
            </label>}
            <label className="field" style={{ marginTop: 'var(--space-3)' }} htmlFor={`dialogue-text-${line.id}`}>
              <span className="field-label">Words for Speaker {line.speaker}</span>
              <textarea id={`dialogue-text-${line.id}`} dir="auto" value={line.text} maxLength={5000} disabled={batchBusy}
                aria-invalid={Boolean(errors[line.id])} aria-describedby={errors[line.id] ? `dialogue-error-${line.id}` : undefined}
                onChange={(event) => patchLine(line.id, { text: event.target.value })} />
            </label>
            {errors[line.id] && <p id={`dialogue-error-${line.id}`} className="inline-error" role="alert">{errors[line.id]}</p>}
            <div style={{ ...controls, marginTop: 'var(--space-3)' }}>
              <button className="btn-sm" style={buttonStyle} disabled={busy || batchBusy || !permission || modelsQuery.isPending}
                aria-busy={pending.has(line.id)} onClick={() => void generateLine(line)}>
                {pending.has(line.id) ? 'Queueing…' : last ? 'Generate this line again' : 'Generate this line'}
              </button>
              {job && (job.status === 'failed' || job.status === 'cancelled') && !stale &&
                <button className="btn-sm ghost" style={buttonStyle} disabled={busy || batchBusy || !permission}
                  onClick={() => void generateLine(line, true)}>Retry original job</button>}
              {stale && <span className="tag warn">Inputs changed · generate an updated take</span>}
            </div>
            {last && <DialogueAttempt key={last.jobId} attempt={last} onStatus={updateJob} onMissing={markMissing} onCancel={cancel} />}
            {line.attempts.length > 1 && <PastAttempts attempts={line.attempts.slice(0, -1)} onStatus={updateJob} />}
          </article>
        );
      })}

      <div className="card" style={{ padding: 'var(--space-5)' }}>
        <div style={controls}>
          <button className="btn-sm ghost" style={buttonStyle} disabled={draft.lines.length >= 50}
            onClick={() => setDraft((current) => ({ ...current, lines: [...current.lines, newLine()] }))}>Add line</button>
          <button className="btn primary" style={buttonStyle} disabled={batchBusy || !permission || modelsQuery.isPending ||
            draft.lines.every((line) => ready(line) || inFlight(line))} aria-busy={batchBusy}
            onClick={() => void generateMissing()}>{batchBusy ? 'Queueing lines…' : 'Generate missing or changed lines'}</button>
          <button className="btn-sm" style={buttonStyle} disabled={!allReady || assembling}
            aria-busy={assembling} onClick={() => void assemble()}>{assembling ? 'Assembling…' : 'Assemble dialogue'}</button>
        </div>
        <p className="hint muted">Assembly uses the latest matching take for every line, in this order, with the pauses above. Review each voice before sharing.</p>
        {onOpenRecent && <button className="link" style={buttonStyle} onClick={onOpenRecent}>Open all generations in Recent</button>}
        {assembly && <div className="result">
          <h3>Assembled dialogue · {assembly.result.duration_sec.toFixed(1)} seconds</h3>
          {assembly.signature !== assemblySignature && <p className="tag warn">Earlier assembly · draft has changed</p>}
          <AudioPlayer src={mediaUrl(assembly.result.audio_url)} label="Assembled dialogue" downloadName="dialogue.wav" />
        </div>}
      </div>
    </section>
  );
}

function modelLabel(model: ModelSummary): string {
  return `${model.display_name}${model.experimental ? ' · Experimental' : ''}${model.commercial_use ? '' : ' · Non-commercial'}`;
}

function DialogueAttempt({ attempt, onStatus, onCancel, onMissing }: {
  attempt: Attempt; onStatus: (job: JobStatusResponse) => void; onCancel?: (id: number) => Promise<void>;
  onMissing?: (id: number) => void;
}) {
  const query = useJob(attempt.jobId);
  const queryClient = useQueryClient();
  const completed = useRef(false);
  const [cancelBusy, setCancelBusy] = useState(false);
  const job = query.data;
  useEffect(() => {
    if (query.error instanceof ApiError && query.error.status === 404) onMissing?.(attempt.jobId);
  }, [query.error, attempt.jobId, onMissing]);
  useEffect(() => {
    if (!job) return;
    onStatus(job);
    if (job.status === 'succeeded' && !completed.current) {
      completed.current = true;
      void queryClient.invalidateQueries({ queryKey: ['history'] });
      void queryClient.invalidateQueries({ queryKey: ['jobs'] });
    }
  }, [job, onStatus, queryClient]);
  if (query.error) return <div className="inline-error" role="alert">Job #{attempt.jobId}: {message(query.error)}
    <button className="btn-sm ghost" style={buttonStyle} onClick={() => void query.refetch()}>Refresh job</button></div>;
  if (!job) return <p className="muted" role="status">Loading job #{attempt.jobId}…</p>;
  const output = job.result && 'audio_url' in job.result ? job.result : null;
  return <div className="result" style={{ marginTop: 'var(--space-3)' }}>
    <p role="status">Job #{job.id} · {job.status}{job.route ? ` · ${job.route.model_display_name}` : ''}
      {job.eta_sec != null && (job.status === 'queued' || job.status === 'running') ? ` · estimated ${Math.round(job.eta_sec)}s` : ''}</p>
    {job.error && <p className="inline-error" role="alert">{job.error.detail}</p>}
    {job.status === 'succeeded' && job.is_fake && <p className="inline-error">Test audio · excluded from dialogue assembly.</p>}
    {job.status === 'succeeded' && output && !job.is_fake && <>
      <p className="hint muted" dir="auto">Generated text: {attempt.snapshot.text}</p>
      <AudioPlayer src={mediaUrl(output.audio_url)} label={`Speaker ${attempt.snapshot.speaker}: ${attempt.snapshot.text}`}
        downloadName={`dialogue-job-${job.id}.wav`} compact />
    </>}
    {job.status === 'queued' && onCancel && <button className="btn-sm danger" style={buttonStyle} disabled={cancelBusy}
      onClick={() => { setCancelBusy(true); void onCancel(job.id).finally(() => setCancelBusy(false)); }}>
      {cancelBusy ? 'Cancelling…' : 'Cancel queued line'}
    </button>}
  </div>;
}

function PastAttempts({ attempts, onStatus }: { attempts: Attempt[]; onStatus: (job: JobStatusResponse) => void }) {
  const [open, setOpen] = useState(false);
  return <details style={{ marginTop: 'var(--space-3)' }} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary style={{ cursor: 'pointer', minHeight: 44 }}>Earlier takes ({attempts.length})</summary>
    {open && attempts.map((attempt) => <DialogueAttempt key={attempt.jobId} attempt={attempt} onStatus={onStatus} />)}
  </details>;
}
