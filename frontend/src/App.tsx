import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ApiError } from './services/api';
import { fmtDuration } from './lib/format';
import type { JobStatusResponse } from './types/api';
import { Composer } from './components/Composer';
import { EnrollCard } from './components/EnrollCard';
import { HistoryPanel } from './components/HistoryPanel';
import { ActiveJobRow } from './components/ActiveJobRow';
import { PronunciationPanel } from './components/PronunciationPanel';
import { TranscriptPanel } from './components/TranscriptPanel';
import { ToastStack, type ToastItem } from './components/Toast';
import { VoiceLibrary } from './components/VoiceLibrary';
import {
  useCancelJobMutation,
  useHistory,
  useInvalidateHistory,
  useInvalidateVoices,
  useJobsList,
  useLanguages,
  useVoices,
} from './hooks/queries';
import { IconAlert, IconDownload, IconFileAudio, IconHistory, IconMic, IconSettings, IconX } from './components/icons';
import './App.css';

// 477 lines behind a tab most sessions never open — split out of the main bundle.
const AudioEditorTab = lazy(() =>
  import('./components/AudioEditorTab').then((m) => ({ default: m.AudioEditorTab })),
);

const PAGE_SIZE = 20;
type Tab = 'studio' | 'recent' | 'import' | 'pronunciation' | 'editor';
const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: 'studio', label: 'Voice Studio', icon: <IconMic size={14} /> },
  { id: 'recent', label: 'Recent', icon: <IconHistory size={14} /> },
  { id: 'import', label: 'Import', icon: <IconDownload size={14} /> },
  { id: 'pronunciation', label: 'Pronunciation', icon: <IconSettings size={14} /> },
  { id: 'editor', label: 'Audio Editor', icon: <IconFileAudio size={14} /> },
];

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('studio');
  const [pageCount, setPageCount] = useState(1);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  // Text handed from the Import tab to the Composer. A monotonic token rather
  // than the bare string, so sending the SAME chunk twice still lands — see
  // Composer's `pendingText` prop.
  const [pendingText, setPendingText] = useState<{ text: string; token: number } | null>(null);
  const pendingToken = useRef(0);
  const toastId = useRef(0);
  const queryClient = useQueryClient();

  const languagesQ = useLanguages();
  const voicesQ = useVoices();
  const historyQ = useHistory(1, PAGE_SIZE * pageCount);
  const jobsQ = useJobsList(1, PAGE_SIZE);
  const cancelJob = useCancelJobMutation();

  // RECENT shows every job with no history row — queued, running, failed and
  // cancelled — because a failure is exactly what history structurally cannot
  // show and is worth keeping visible. A succeeded job is excluded: it already
  // appears as a history item, and both would list every clip twice.
  const recentJobs = (jobsQ.data?.items ?? []).filter((j) => j.status !== 'succeeded');

  // STUDIO's strip is narrower on purpose: only work actually in flight. A
  // terminal job is not "in progress", and filtering on `!== 'succeeded'` here
  // pinned every past failure under that heading permanently.
  const inFlightJobs = recentJobs.filter(
    (j) => j.status === 'queued' || j.status === 'running',
  );
  // FAILED IS ITS OWN SECTION, not a tail on "In progress". A failed job is
  // not in progress -- the Studio strip was already fixed for exactly this
  // (see above) and the Recent tab kept the bug: every past failure sat under
  // that heading permanently, next to jobs that really were running.
  //
  // The lifecycle the three sections describe: Try again moves a row from
  // Failed into In progress; it lands in Generated (the day groups below) or
  // back in Failed.
  const failedJobs = recentJobs.filter(
    (j) => j.status === 'failed' || j.status === 'cancelled',
  );
  //: Failed jobs whose retry ALREADY EXISTS, by the id they retried. Without
  //: this a failed row keeps its button after being retried, and four clicks
  //: produce four identical queued jobs.
  const retriedJobIds = new Set(
    (jobsQ.data?.items ?? [])
      .map((j) => j.retry_of_job_id)
      .filter((id): id is number => id != null),
  );
  const recentCount = (historyQ.data?.total ?? 0) + recentJobs.length;

  // MAX, not sum. `estimate.py` already folds queue wait into each job's
  // `eta_sec`, so the last job's estimate already counts the ones ahead of it —
  // adding them would count the same wait once per job.
  const etas = inFlightJobs.map((j) => j.eta_sec).filter((e): e is number => e != null);
  const batchEtaSec = etas.length > 0 ? Math.max(...etas) : null;
  const invalidateVoices = useInvalidateVoices();
  const invalidateHistory = useInvalidateHistory();

  const anyError = languagesQ.error ?? voicesQ.error ?? historyQ.error;
  const anySuccess = languagesQ.isSuccess && voicesQ.isSuccess && historyQ.isSuccess;
  const online = anyError ? false : anySuccess ? true : null;
  const error = anyError ? (anyError instanceof ApiError ? anyError.message : String(anyError)) : null;

  const loadMore = useCallback(() => setPageCount((p) => p + 1), []);

  const addToast = useCallback((tone: ToastItem['tone'], message: string) => {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, tone, message }]);
  }, []);
  const dismissToast = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  // Announce EVERY job that settles, from the polled list — not just the one
  // the Composer happens to be tracking.
  //
  // The bug this fixes: `onJobSettled` fires from `Composer`'s `useJob(jobId)`,
  // which follows only the MOST RECENT submission. Queue a second generation
  // and the first one's failure could never reach a toast, because nothing was
  // watching it any more. The same held for a job the server interrupted while
  // the page was elsewhere — it went from the in-progress strip to nothing,
  // silently, which is exactly what it looked like from the outside.
  //
  // Seeded on first arrival rather than starting empty: without that, opening
  // the app would toast every historical failure still inside the retention
  // window at once.
  const announcedJobs = useRef<Set<number> | null>(null);
  useEffect(() => {
    const items = jobsQ.data?.items;
    if (!items) return;

    const terminal = items.filter(
      (j) => j.status === 'failed' || j.status === 'cancelled' || j.status === 'succeeded',
    );

    if (announcedJobs.current === null) {
      announcedJobs.current = new Set(terminal.map((j) => j.id));
      return;
    }

    let anySucceeded = false;
    for (const job of terminal) {
      if (announcedJobs.current.has(job.id)) continue;
      announcedJobs.current.add(job.id);
      const name = job.title || job.input_text?.slice(0, 40) || null;
      if (job.status === 'succeeded') {
        anySucceeded = true;
        addToast('success', name ? `“${name}” is ready.` : 'Generation complete.');
      } else if (job.status === 'failed') {
        const why =
          (job.error?.detail as string | undefined) ?? 'Generation failed.';
        addToast('error', name ? `“${name}” failed — ${why}` : why);
      }
      // 'cancelled' stays silent: the user clicked Cancel, they know.
    }

    // THE VANISHING GENERATION. A succeeded job is deliberately excluded from
    // Recent because it belongs in History — but History is a SEPARATE query,
    // and the only thing refreshing it was the Composer, for the single job it
    // was tracking. Any other job — a retry, or the first of two queued
    // generations — left Recent the moment it succeeded and never arrived in
    // History, so it appeared in neither list until a reload refetched
    // history. It was never lost; it was just briefly invisible in both
    // places at once, which is worse.
    if (anySucceeded) invalidateHistory();
  }, [jobsQ.data, addToast, invalidateHistory]);

  // Acknowledge the enqueue immediately. Generate returns in milliseconds now,
  // so without this the only feedback for a job that takes a minute is a row
  // quietly appearing in a list further down the page.
  const onJobQueued = useCallback(
    (job: JobStatusResponse) => {
      const name = job.title || job.input_text?.slice(0, 40);
      addToast(
        'info',
        name
          ? `“${name}” is queued — you'll be notified when it's ready.`
          : "Queued — you'll be notified when it's ready.",
      );
    },
    [addToast]
  );

  const sendToEditor = useCallback((text: string) => {
    pendingToken.current += 1;
    setPendingText({ text, token: pendingToken.current });
    setActiveTab('studio');
  }, []);

  // API key changed: every prior response may have been scoped to different
  // credentials (or none). Nothing short of a full invalidation is safe.
  const onApiKeySaved = useCallback(() => {
    void queryClient.invalidateQueries();
  }, [queryClient]);

  return (
    <div className="studio">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true">
            <span className="logo-bar" />
            <span className="logo-bar" />
            <span className="logo-bar" />
          </span>
          <span className="brand-name">
            Voice Clone <span className="brand-thin">Studio</span>
          </span>
        </div>

        <div className="segmented" role="tablist" aria-label="Studio sections">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={activeTab === t.id}
              aria-controls={`panel-${t.id}`}
              aria-label={t.label}
              tabIndex={activeTab === t.id ? 0 : -1}
              className={activeTab === t.id ? 'on' : ''}
              onClick={() => setActiveTab(t.id)}
            >
              {t.icon}
              <span className="seg-label">{t.label}</span>
              {/* Everything you have generated lives behind this tab, and
                  nothing on Studio said so. The badge is the signpost. */}
              {t.id === 'recent' && recentCount > 0 && (
                <span className="seg-badge" aria-label={`${recentCount} generations`}>
                  {recentCount > 99 ? '99+' : recentCount}
                </span>
              )}
            </button>
          ))}
        </div>

        <div className="topbar-right">
          <ApiKeyControl onSaved={onApiKeySaved} />
          <div className={`status ${online === null ? '' : online ? 'ok' : 'down'}`}>
            <span className="status-dot" aria-hidden="true" />
            {online === null ? 'connecting' : online ? 'online' : 'offline'}
          </div>
        </div>
      </header>

      {error && (
        <div className="banner error" role="alert">
          <IconAlert size={15} />
          <span>{error}</span>
          <button
            type="button"
            className="link"
            onClick={() => void queryClient.invalidateQueries()}
          >
            Retry
          </button>
        </div>
      )}

      <main
        className={activeTab === 'studio' ? 'grid' : undefined}
        role="tabpanel"
        id={`panel-${activeTab}`}
        aria-labelledby={`tab-${activeTab}`}
      >
        {/* HIDDEN, NOT UNMOUNTED. `activeTab === 'studio' && …` destroyed the
            Composer's state every time you looked at another tab — text,
            title, voice, model and speed all reset, so glancing at Recent
            mid-compose lost the draft. Composer state is deliberately local
            component state (there is no store), which makes unmounting
            equivalent to discarding the draft.

            Only this panel is kept alive: it is the one you compose in, and
            it is cheap. The Audio Editor stays lazy and unmounted. */}
        <div className="tab-panel" hidden={activeTab !== 'studio'}>
          <>
            <div className="col">
              <EnrollCard languages={languagesQ.data?.languages ?? []} onEnrolled={invalidateVoices} />
              <VoiceLibrary voices={voicesQ.data?.profiles ?? []} onDeleted={invalidateVoices} />
            </div>

            <div className="col">
              <Composer
                voices={voicesQ.data?.profiles ?? []}
                languages={languagesQ.data?.languages ?? []}
                onJobQueued={onJobQueued}
                onOpenRecent={() => setActiveTab('recent')}
                pendingText={pendingText}
              />
              {/* Generate no longer blocks, so several clips can be in flight
                  at once and the Composer's single result card cannot show
                  them. This strip is where "did my last three go through" is
                  answered without leaving Studio. */}
              {inFlightJobs.length > 0 && (
                <section className="card" aria-labelledby="inflight-h">
                  <header className="card-head">
                    <h2 id="inflight-h">
                      In progress
                      <span className="count">{inFlightJobs.length}</span>
                    </h2>
                    {batchEtaSec != null && (
                      <span className="muted">~{fmtDuration(batchEtaSec)} left</span>
                    )}
                  </header>
                  <ul className="hist">
                    {inFlightJobs.map((job) => (
                      <ActiveJobRow
                        key={job.id}
                        job={job}
                        onCancel={() => cancelJob.mutate(job.id)}
                        cancelling={cancelJob.isPending && cancelJob.variables === job.id}
                      />
                    ))}
                  </ul>
                </section>
              )}
            </div>
          </>
        </div>
        {/* ONE list. History is the spine because it is durable; only
            unfinished jobs are overlaid, since those are the states a
            `generation_history` row cannot represent. Succeeded jobs are
            filtered out here — they are already history rows, and passing
            them would list every clip twice. */}
        {activeTab === 'recent' && (
          <HistoryPanel
            items={historyQ.data?.items ?? []}
            total={historyQ.data?.total ?? 0}
            loading={historyQ.isFetching}
            hasMore={(historyQ.data?.items.length ?? 0) < (historyQ.data?.total ?? 0)}
            onLoadMore={loadMore}
            onChanged={invalidateHistory}
            activeJobs={inFlightJobs}
            failedJobs={failedJobs}
            retriedJobIds={retriedJobIds}
            onCancelJob={(id) => cancelJob.mutate(id)}
            cancellingJobId={cancelJob.isPending ? (cancelJob.variables ?? null) : null}
          />
        )}
        {/* HIDDEN, NOT UNMOUNTED — same rule as the Studio panel above, for a
            worse version of the same reason. Unmounting discarded the fetched
            transcript AND the id of an in-flight conversion job, so glancing at
            another tab mid-convert lost a 23-part result the GPU was still
            producing. The job itself survived on the server; only the client
            forgot it was waiting, which is the most annoying possible way to
            lose work. */}
        <div className="tab-panel" hidden={activeTab !== 'import'}>
          <TranscriptPanel onSendToEditor={sendToEditor} />
        </div>
        {activeTab === 'pronunciation' && <PronunciationPanel />}
        {activeTab === 'editor' && (
          <Suspense fallback={<p className="hint center">Loading editor…</p>}>
            <AudioEditorTab onEnrolled={invalidateVoices} />
          </Suspense>
        )}
      </main>

      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}

function ApiKeyControl({ onSaved }: { onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState(() => localStorage.getItem('vcs_api_key') ?? '');
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  // Escape closes and returns focus; click-outside closes.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    function onDown(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onDown);
    };
  }, [open]);

  function save() {
    if (key) localStorage.setItem('vcs_api_key', key);
    else localStorage.removeItem('vcs_api_key');

    setOpen(false);
    triggerRef.current?.focus();
    onSaved();
  }

  return (
    <div className="apikey" ref={wrapRef}>
      <button
        ref={triggerRef}
        className="icon-btn"
        type="button"
        aria-label="Settings"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <IconSettings size={16} />
      </button>

      {open && (
        <div className="apikey-pop" role="dialog" aria-label="Settings">
          <div className="pop-head">
            <strong>Settings</strong>
            <button
              type="button"
              className="icon-btn tiny"
              onClick={() => setOpen(false)}
              aria-label="Close"
            >
              <IconX size={13} />
            </button>
          </div>

          <label className="field">
            <span className="field-label">API Key</span>
            <input
              value={key}
              onChange={(e) => setKey(e.target.value)}
              type="password"
              placeholder="X-API-Key"
              autoFocus
            />
          </label>
          <p className="hint">Required if the backend sets VCS_API_KEY.</p>

          <button className="btn primary sm" type="button" onClick={save}>
            Save & Reload
          </button>
        </div>
      )}
    </div>
  );
}
