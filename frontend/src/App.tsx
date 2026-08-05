import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, mediaUrl } from './services/api';
import type {
  HistoryItem,
  LanguageInfo,
  TTSGenerateResponse,
  VoiceProfile,
} from './types/api';
import './App.css';

export default function App() {
  const [online, setOnline] = useState<boolean | null>(null);
  const [languages, setLanguages] = useState<LanguageInfo[]>([]);
  const [voices, setVoices] = useState<VoiceProfile[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [langs, vs, hist] = await Promise.all([
        api.languages(),
        api.listVoices(),
        api.history(1, 20),
      ]);
      setLanguages(langs.languages);
      setVoices(vs.profiles);
      setHistory(hist.items);
      setOnline(true);
      setError(null);
    } catch (e) {
      setOnline(false);
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="studio">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◈</span> Voice Clone Studio
        </div>
        <div className={`status ${online ? 'ok' : 'down'}`}>
          {online === null ? 'connecting…' : online ? 'backend online' : 'backend offline'}
        </div>
      </header>

      {error && <div className="banner error">{error}</div>}

      <main className="grid">
        <section className="col">
          <EnrollCard languages={languages} onEnrolled={refresh} />
          <VoiceLibrary voices={voices} onDeleted={refresh} />
        </section>

        <section className="col wide">
          <Composer voices={voices} languages={languages} onGenerated={refresh} />
          <HistoryPanel items={history} />
        </section>
      </main>
    </div>
  );
}

// ── Enrollment ────────────────────────────────────────────────────────────

function EnrollCard({
  languages,
  onEnrolled,
}: {
  languages: LanguageInfo[];
  onEnrolled: () => void;
}) {
  const [name, setName] = useState('');
  const [language, setLanguage] = useState('ur');
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) return setErr('Choose a reference audio file.');
    if (!consent) return setErr('You must confirm you are authorized to clone this voice.');
    setBusy(true);
    setErr(null);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('name', name || file.name);
      form.append('language', language);
      form.append('consent', 'true');
      await api.createVoice(form);
      setName('');
      setConsent(false);
      if (fileRef.current) fileRef.current.value = '';
      onEnrolled();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>Add a voice</h2>
      <label>Reference audio<input ref={fileRef} type="file" accept="audio/*" /></label>
      <label>Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. My voice" /></label>
      <label>
        Language
        <select value={language} onChange={(e) => setLanguage(e.target.value)}>
          {(languages.length ? languages : FALLBACK_LANGS).map((l) => (
            <option key={l.code} value={l.code}>{l.display_name}</option>
          ))}
        </select>
      </label>
      <label className="consent">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        I am authorized to clone this voice.
      </label>
      {err && <div className="inline-error">{err}</div>}
      <button disabled={busy} type="submit">{busy ? 'Uploading…' : 'Add voice'}</button>
    </form>
  );
}

function VoiceLibrary({ voices, onDeleted }: { voices: VoiceProfile[]; onDeleted: () => void }) {
  return (
    <div className="card">
      <h2>Voices <span className="count">{voices.length}</span></h2>
      {voices.length === 0 && <p className="muted">No voices yet. Add one above.</p>}
      <ul className="voice-list">
        {voices.map((v) => (
          <li key={v.id}>
            <div className="v-main">
              <strong>{v.name}</strong>
              <span className="tag">{v.language}</span>
              {v.is_clipped && <span className="tag warn">clipped</span>}
            </div>
            <div className="v-meta">
              {v.duration_sec?.toFixed(1)}s · {(v.sample_rate / 1000).toFixed(0)} kHz
              {v.peak_dbfs != null && ` · ${v.peak_dbfs} dBFS`}
            </div>
            <audio controls preload="none" src={mediaUrl(v.audio_url)} />
            <button className="link danger" onClick={() => void api.deleteVoice(v.id).then(onDeleted)}>
              delete
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── Compose + generate ──────────────────────────────────────────────────────

function Composer({
  voices,
  languages,
  onGenerated,
}: {
  voices: VoiceProfile[];
  languages: LanguageInfo[];
  onGenerated: () => void;
}) {
  const [profileId, setProfileId] = useState<number | null>(null);
  const [language, setLanguage] = useState('ur');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<TTSGenerateResponse | null>(null);

  useEffect(() => {
    const first = voices[0];
    if (profileId === null && first) setProfileId(first.id);
  }, [voices, profileId]);

  async function generate() {
    if (profileId === null) return setErr('Add and select a voice first.');
    if (!text.trim()) return setErr('Type something to say.');
    setBusy(true);
    setErr(null);
    setResult(null);
    try {
      const res = await api.generate({ profile_id: profileId, language, text: text.trim() });
      setResult(res);
      onGenerated();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const langs = languages.length ? languages : FALLBACK_LANGS;
  return (
    <div className="card">
      <h2>Generate speech</h2>
      <div className="row">
        <label>
          Voice
          <select
            value={profileId ?? ''}
            onChange={(e) => setProfileId(Number(e.target.value))}
            disabled={!voices.length}
          >
            {voices.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
          </select>
        </label>
        <label>
          Language
          <select value={language} onChange={(e) => setLanguage(e.target.value)}>
            {langs.map((l) => <option key={l.code} value={l.code}>{l.display_name}</option>)}
          </select>
        </label>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={4}
        placeholder={PLACEHOLDER[language] ?? 'Type your text…'}
      />
      {err && <div className="inline-error">{err}</div>}
      <button className="primary" disabled={busy} onClick={() => void generate()}>
        {busy ? 'Generating…' : 'Generate'}
      </button>

      {result && <ResultCard result={result} />}
    </div>
  );
}

function ResultCard({ result }: { result: TTSGenerateResponse }) {
  const r = result.route;
  return (
    <div className="result">
      <div className="chip" title={r.rationale}>
        {r.model_display_name} · {r.transform === 'none' ? 'direct' : r.transform}
        {r.lossy && <span className="tag warn">lossy</span>}
      </div>
      <audio controls autoPlay src={mediaUrl(result.audio_url)} />
      <div className="v-meta">
        {result.duration_sec?.toFixed(1)}s
        {result.rtf != null && ` · RTF ${result.rtf.toFixed(2)}`}
      </div>
      <a className="link" href={mediaUrl(result.audio_url)} download>
        Download
      </a>
    </div>
  );
}

function HistoryPanel({ items }: { items: HistoryItem[] }) {
  if (!items.length) return null;
  return (
    <div className="card">
      <h2>Recent</h2>
      <ul className="hist">
        {items.map((h) => (
          <li key={h.id}>
            <div className="h-text">{h.input_text}</div>
            <div className="v-meta">
              {h.profile_name} · {h.language} · {h.route.model_display_name}
            </div>
            <audio controls preload="none" src={mediaUrl(h.audio_url)} />
          </li>
        ))}
      </ul>
    </div>
  );
}

const FALLBACK_LANGS: LanguageInfo[] = [
  { code: 'en', display_name: 'English', native_name: 'English', scripts: ['latin'], model_ids: [], requires_transform: false },
  { code: 'hi', display_name: 'Hindi (हिन्दी)', native_name: 'हिन्दी', scripts: ['latin'], model_ids: [], requires_transform: false },
  { code: 'ur', display_name: 'Urdu (اردو)', native_name: 'اردو', scripts: ['latin'], model_ids: [], requires_transform: false },
];

const PLACEHOLDER: Record<string, string> = {
  en: 'Hello, how are you today?',
  hi: 'Aap kaise ho? Aaj mausam accha hai.',
  ur: 'Aap kaise hain? Aaj mausam bohat acha hai.',
};
