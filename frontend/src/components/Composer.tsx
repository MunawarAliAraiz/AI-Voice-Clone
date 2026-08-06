/**
 * The composer — pick a voice, declare a language, type, generate.
 *
 * The user declares the language; the app detects the script. It deliberately
 * does not guess Roman Urdu from English — both are Latin — so `dir` keys off
 * the DETECTED script, never `language === 'ur'`.
 */
import { useEffect, useState } from 'react';
import { api, ApiError, mediaUrl } from '../services/api';
import type { LanguageInfo, ScriptDetectResponse, TTSGenerateResponse, VoiceProfile } from '../types/api';
import { AudioPlayer } from './AudioPlayer';
import { IconAlert, IconCheck, IconSpark, IconSpinner } from './icons';

interface Props {
  voices: VoiceProfile[];
  languages: LanguageInfo[];
  onGenerated: () => void;
}

export function Composer({ voices, languages, onGenerated }: Props) {
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
      api
        .detectScript(t, language)
        .then(setDetect)
        .catch(() => setDetect(null)); // hint only; a failure must not block typing
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

  /** Ctrl/Cmd+Enter generates, the convention for a "send" textarea. */
  function onKeyDown(e: React.KeyboardEvent) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !busy) {
      e.preventDefault();
      void generate();
    }
  }

  const langs = languages.length ? languages : [];
  const rtl = detect?.is_rtl ?? false;
  const disabled = busy || !voices.length;

  return (
    <section className="card composer" aria-labelledby="composer-h">
      <header className="card-head">
        <h2 id="composer-h">Generate speech</h2>
        {text.length > 0 && <span className="count">{text.length} / 5000</span>}
      </header>

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
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              {langs.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.display_name}
                </option>
              ))}
            </select>
          </div>
        </label>
      </div>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        rows={5}
        maxLength={5000}
        dir={rtl ? 'rtl' : 'ltr'}
        placeholder={PLACEHOLDER[language] ?? 'Type your text…'}
        aria-label="Text to speak"
      />

      <div className="composer-foot">
        <div className="detect-slot" aria-live="polite">
          {detect && (
            <span className={`detect ${detect.routable ? '' : 'bad'}`}>
              {detect.routable ? <IconCheck size={13} /> : <IconAlert size={13} />}
              {detect.routable
                ? `${detect.script} · ${detect.would_route_to?.model_display_name ?? 'ready'}`
                : (detect.hint ?? 'This text cannot be routed.')}
            </span>
          )}
        </div>
        <kbd className="kbd">Ctrl + ↵</kbd>
      </div>

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

      {busy && (
        <p className="hint center" aria-live="polite">
          First run after a restart loads the model — that one can take a minute.
        </p>
      )}

      {result && <ResultCard result={result} />}
    </section>
  );
}

function ResultCard({ result }: { result: TTSGenerateResponse }) {
  const r = result.route;
  return (
    <div className="result">
      <div className="result-head">
        <span className="route-chip solid" title={r.rationale}>
          {r.model_display_name}
          <span className="sep">·</span>
          {r.transform === 'none' ? 'direct' : r.transform}
        </span>
        {r.lossy && <span className="tag warn">lossy</span>}
        <span className="grow" />
        <span className="v-meta">
          {result.duration_sec != null && <span>{result.duration_sec.toFixed(1)}s</span>}
          {result.rtf != null && (
            <>
              <span className="dot" />
              <span>RTF {result.rtf.toFixed(2)}</span>
            </>
          )}
        </span>
      </div>
      <AudioPlayer autoPlay src={mediaUrl(result.audio_url)} label="generated audio" />
    </div>
  );
}

const PLACEHOLDER: Record<string, string> = {
  en: 'Hello, how are you today?',
  hi: 'Aap kaise ho? Aaj mausam accha hai.',
  ur: 'Aap kaise hain? Aaj mausam bohat acha hai.',
};
