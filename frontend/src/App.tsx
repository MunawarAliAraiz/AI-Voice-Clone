import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './services/api';
import type { HistoryItem, LanguageInfo, VoiceProfile } from './types/api';
import { AudioEditorTab } from './components/AudioEditorTab';
import { Composer } from './components/Composer';
import { EnrollCard } from './components/EnrollCard';
import { HistoryPanel } from './components/HistoryPanel';
import { VoiceLibrary } from './components/VoiceLibrary';
import { IconAlert, IconMusic, IconSettings, IconX } from './components/icons';
import './App.css';

const PAGE_SIZE = 20;

export default function App() {
  const [activeTab, setActiveTab] = useState<'studio' | 'editor'>('studio');
  const [online, setOnline] = useState<boolean | null>(null);
  const [languages, setLanguages] = useState<LanguageInfo[]>([]);
  const [voices, setVoices] = useState<VoiceProfile[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (pageCount = 1) => {
    try {
      const [langs, vs, hist] = await Promise.all([
        api.languages(),
        api.listVoices(),
        api.history(1, PAGE_SIZE * pageCount),
      ]);
      setLanguages(langs.languages);
      setVoices(vs.profiles);
      setHistory(hist.items);
      setTotal(hist.total);
      setOnline(true);
      setError(null);
    } catch (e) {
      setOnline(false);
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh(1);
  }, [refresh]);

  const loadMore = useCallback(async () => {
    setLoadingMore(true);
    const next = pages + 1;
    await refresh(next);
    setPages(next);
    setLoadingMore(false);
  }, [pages, refresh]);

  const reload = useCallback(() => void refresh(pages), [refresh, pages]);

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

        {/* Main Application Tabs */}
        <div className="segmented" style={{ flex: '0 0 auto' }}>
          <button
            type="button"
            className={activeTab === 'studio' ? 'on' : ''}
            onClick={() => setActiveTab('studio')}
          >
            🎙️ Voice Studio
          </button>
          <button
            type="button"
            className={activeTab === 'editor' ? 'on' : ''}
            onClick={() => setActiveTab('editor')}
          >
            <IconMusic size={14} /> Audio Editor
          </button>
        </div>

        <div className="topbar-right">
          <ApiKeyControl onSaved={reload} />
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
          <button type="button" className="link" onClick={reload}>
            Retry
          </button>
        </div>
      )}

      {activeTab === 'studio' ? (
        <main className="grid">
          <div className="col">
            <EnrollCard languages={languages} onEnrolled={reload} />
            <VoiceLibrary voices={voices} onDeleted={reload} />
          </div>

          <div className="col">
            <Composer voices={voices} languages={languages} onGenerated={reload} />
            <HistoryPanel
              items={history}
              total={total}
              loading={loadingMore}
              hasMore={history.length < total}
              onLoadMore={() => void loadMore()}
              onChanged={reload}
            />
          </div>
        </main>
      ) : (
        <main>
          <AudioEditorTab onEnrolled={reload} />
        </main>
      )}
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
