import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ApiError } from './services/api';
import type { JobStatusResponse } from './types/api';
import { Composer } from './components/Composer';
import { EnrollCard } from './components/EnrollCard';
import { HistoryPanel } from './components/HistoryPanel';
import { JobsPanel } from './components/JobsPanel';
import { ToastStack, type ToastItem } from './components/Toast';
import { VoiceLibrary } from './components/VoiceLibrary';
import {
  useHistory,
  useInvalidateHistory,
  useInvalidateVoices,
  useLanguages,
  useVoices,
} from './hooks/queries';
import { IconAlert, IconFileAudio, IconHistory, IconMic, IconSettings, IconX } from './components/icons';
import './App.css';

// 477 lines behind a tab most sessions never open — split out of the main bundle.
const AudioEditorTab = lazy(() =>
  import('./components/AudioEditorTab').then((m) => ({ default: m.AudioEditorTab })),
);

const PAGE_SIZE = 20;
type Tab = 'studio' | 'recent' | 'editor';
const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: 'studio', label: 'Voice Studio', icon: <IconMic size={14} /> },
  { id: 'recent', label: 'Recent', icon: <IconHistory size={14} /> },
  { id: 'editor', label: 'Audio Editor', icon: <IconFileAudio size={14} /> },
];

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('studio');
  const [pageCount, setPageCount] = useState(1);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const toastId = useRef(0);
  const queryClient = useQueryClient();

  const languagesQ = useLanguages();
  const voicesQ = useVoices();
  const historyQ = useHistory(1, PAGE_SIZE * pageCount);
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

  const onJobSettled = useCallback(
    (job: JobStatusResponse) => {
      if (job.status === 'succeeded') {
        addToast('success', 'Generation complete.');
      } else if (job.status === 'failed') {
        addToast('error', job.error?.detail ?? 'Generation failed.');
      }
      // 'cancelled' gets no toast — the user just clicked Cancel, they know.
    },
    [addToast]
  );

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
        {activeTab === 'studio' && (
          <>
            <div className="col">
              <EnrollCard languages={languagesQ.data?.languages ?? []} onEnrolled={invalidateVoices} />
              <VoiceLibrary voices={voicesQ.data?.profiles ?? []} onDeleted={invalidateVoices} />
            </div>

            <div className="col">
              <Composer
                voices={voicesQ.data?.profiles ?? []}
                languages={languagesQ.data?.languages ?? []}
                onJobSettled={onJobSettled}
              />
              <HistoryPanel
                items={historyQ.data?.items ?? []}
                total={historyQ.data?.total ?? 0}
                loading={historyQ.isFetching}
                hasMore={(historyQ.data?.items.length ?? 0) < (historyQ.data?.total ?? 0)}
                onLoadMore={loadMore}
                onChanged={invalidateHistory}
              />
            </div>
          </>
        )}
        {activeTab === 'recent' && <JobsPanel />}
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
