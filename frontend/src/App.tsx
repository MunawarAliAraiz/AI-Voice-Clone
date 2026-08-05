import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, mediaUrl } from './services/api';
import type {
  HistoryItem,
  LanguageInfo,
  ScriptDetectResponse,
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
        <div className="topbar-right">
          <ApiKeyControl onSaved={refresh} />
          <div className={`status ${online ? 'ok' : 'down'}`}>
            {online === null ? 'connecting…' : online ? 'backend online' : 'backend offline'}
          </div>
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

function ApiKeyControl({ onSaved }: { onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState(localStorage.getItem('vcs_api_key') ?? '');
  function save() {
    if (key) localStorage.setItem('vcs_api_key', key);
    else localStorage.removeItem('vcs_api_key');
    setOpen(false);
    onSaved();
  }
  return (
    <div className="apikey">
      <button className="icon-btn" type="button" title="API key" onClick={() => setOpen((o) => !o)}>⚙</button>
      {open && (
        <div className="apikey-pop">
          <label>
            API key
            <input
              value={key}
              onChange={(e) => setKey(e.target.value)}
              type="password"
              placeholder="X-API-Key (optional)"
            />
          </label>
          <button className="primary" type="button" onClick={save}>Save</button>
        </div>
      )}
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
  const [mode, setMode] = useState<'upload' | 'record'>('upload');
  const [name, setName] = useState('');
  const [language, setLanguage] = useState('ur');
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const rec = useRecorder();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const fromFile = mode === 'upload' ? fileRef.current?.files?.[0] ?? null : null;
    const source: Blob | null = mode === 'record' ? rec.blob : fromFile;
    const filename = fromFile?.name ?? 'recording.wav';
    if (!source) return setErr(mode === 'record' ? 'Record a clip first.' : 'Choose a reference audio file.');
    if (!consent) return setErr('You must confirm you are authorized to clone this voice.');
    setBusy(true);
    setErr(null);
    try {
      const form = new FormData();
      form.append('file', source, filename);
      form.append('name', name || filename);
      form.append('language', language);
      form.append('consent', 'true');
      await api.createVoice(form);
      setName('');
      setConsent(false);
      rec.reset();
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
      <div className="tabs">
        <button type="button" className={mode === 'upload' ? 'on' : ''} onClick={() => setMode('upload')}>Upload</button>
        <button type="button" className={mode === 'record' ? 'on' : ''} onClick={() => setMode('record')}>Record</button>
      </div>

      {mode === 'upload' ? (
        <label>Reference audio<input ref={fileRef} type="file" accept="audio/*" /></label>
      ) : (
        <div className="recorder">
          {!rec.recording ? (
            <button type="button" className="rec-btn" onClick={() => void rec.start()}>
              ● Record
            </button>
          ) : (
            <button type="button" className="rec-btn recording" onClick={rec.stop}>
              ■ Stop ({fmtSeconds(rec.seconds)})
            </button>
          )}
          {rec.error && <div className="inline-error">{rec.error}</div>}
          {rec.blob && !rec.recording && (
            <div className="rec-preview">
              <audio controls src={URL.createObjectURL(rec.blob)} />
              <button type="button" className="link" onClick={rec.reset}>discard</button>
            </div>
          )}
        </div>
      )}

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

function fmtSeconds(s: number): string {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
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
  const [detect, setDetect] = useState<ScriptDetectResponse | null>(null);

  useEffect(() => {
    const first = voices[0];
    if (profileId === null && first) setProfileId(first.id);
  }, [voices, profileId]);

  // Debounced live script detection — powers the routability hint and dir.
  useEffect(() => {
    const t = text.trim();
    if (!t) {
      setDetect(null);
      return;
    }
    const h = window.setTimeout(() => {
      api.detectScript(t, language).then(setDetect).catch(() => setDetect(null));
    }, 400);
    return () => window.clearTimeout(h);
  }, [text, language]);

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
        dir={detect?.is_rtl ? 'rtl' : 'ltr'}
        placeholder={PLACEHOLDER[language] ?? 'Type your text…'}
      />
      {detect && (
        <div className={`detect ${detect.routable ? '' : 'bad'}`}>
          {detect.routable
            ? `${detect.script} · renders with ${detect.would_route_to?.model_display_name ?? '—'}`
            : detect.hint ?? 'This text cannot be routed.'}
        </div>
      )}
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

// ── Mic recording (decoded to WAV so libsndfile on the backend can read it;
//    MediaRecorder emits webm/opus, which soundfile cannot) ──────────────────

interface Recorder {
  recording: boolean;
  seconds: number;
  blob: Blob | null;
  error: string | null;
  start: () => Promise<void>;
  stop: () => void;
  reset: () => void;
}

function useRecorder(): Recorder {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mrRef = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<number | null>(null);

  const start = useCallback(async () => {
    setError(null);
    setBlob(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      chunks.current = [];
      mr.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const webm = new Blob(chunks.current, { type: mr.mimeType || 'audio/webm' });
        void webmToWav(webm).then(setBlob).catch((e) => setError(String(e)));
      };
      mr.start();
      mrRef.current = mr;
      setRecording(true);
      setSeconds(0);
      timer.current = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    } catch {
      setError('Microphone access was denied.');
    }
  }, []);

  const stop = useCallback(() => {
    mrRef.current?.stop();
    if (timer.current) window.clearInterval(timer.current);
    setRecording(false);
  }, []);

  const reset = useCallback(() => setBlob(null), []);
  return { recording, seconds, blob, error, start, stop, reset };
}

async function webmToWav(input: Blob): Promise<Blob> {
  const ctx = new AudioContext();
  const buf = await ctx.decodeAudioData(await input.arrayBuffer());
  await ctx.close();
  return encodeWav(buf);
}

function encodeWav(buffer: AudioBuffer): Blob {
  const ch = buffer.numberOfChannels;
  const len = buffer.length;
  // down-mix to mono
  const mono = new Float32Array(len);
  for (let c = 0; c < ch; c++) {
    const data = buffer.getChannelData(c);
    for (let i = 0; i < len; i++) mono[i] = (mono[i] ?? 0) + (data[i] ?? 0) / ch;
  }
  const sr = buffer.sampleRate;
  const out = new DataView(new ArrayBuffer(44 + len * 2));
  const wr = (o: number, s: string) => { for (let i = 0; i < s.length; i++) out.setUint8(o + i, s.charCodeAt(i)); };
  wr(0, 'RIFF'); out.setUint32(4, 36 + len * 2, true); wr(8, 'WAVE');
  wr(12, 'fmt '); out.setUint32(16, 16, true); out.setUint16(20, 1, true);
  out.setUint16(22, 1, true); out.setUint32(24, sr, true); out.setUint32(28, sr * 2, true);
  out.setUint16(32, 2, true); out.setUint16(34, 16, true);
  wr(36, 'data'); out.setUint32(40, len * 2, true);
  for (let i = 0; i < len; i++) {
    const s = Math.max(-1, Math.min(1, mono[i] ?? 0));
    out.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([out], { type: 'audio/wav' });
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
