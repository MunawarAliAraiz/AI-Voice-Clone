/**
 * Voice enrollment — upload a clip or record one.
 *
 * Consent is a required field: the backend rejects the upload without it, and
 * voice recordings are biometric data in several jurisdictions.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from '../services/api';
import type { LanguageInfo } from '../types/api';
import { useRecorder } from '../hooks/useRecorder';
import { IconAlert, IconMic, IconSpinner, IconStop, IconUpload } from './icons';
import { AudioEditor, type AudioEditState } from './AudioEditor';
import { fmtSeconds } from '../lib/format';

interface Props {
  languages: LanguageInfo[];
  onEnrolled: () => void;
}

type Mode = 'upload' | 'record';
const TABS: { id: Mode; label: string }[] = [
  { id: 'upload', label: 'Upload' },
  { id: 'record', label: 'Record' },
];

export function EnrollCard({ languages, onEnrolled }: Props) {
  const [mode, setMode] = useState<Mode>('upload');
  const [name, setName] = useState('');
  const [language, setLanguage] = useState('ur');
  const [consent, setConsent] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [editOptions, setEditOptions] = useState<AudioEditState | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const rec = useRecorder();

  // Object URLs must be revoked or every re-render leaks one.
  const recUrl = useMemo(() => (rec.blob ? URL.createObjectURL(rec.blob) : null), [rec.blob]);
  useEffect(() => {
    return () => {
      if (recUrl) URL.revokeObjectURL(recUrl);
    };
  }, [recUrl]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);

    const picked = fileRef.current?.files?.[0] ?? selectedFile;
    const recordedFile = rec.blob ? new File([rec.blob], 'recording.wav', { type: 'audio/wav' }) : null;
    const activeFile = mode === 'record' ? recordedFile : picked;

    if (!activeFile) return setErr(mode === 'record' ? 'Record a clip first.' : 'Choose an audio file.');
    if (!name.trim()) return setErr('Give this voice a name.');
    if (!consent) return setErr('Please confirm you are authorized to clone this voice.');

    const fd = new FormData();
    fd.append('file', activeFile, mode === 'record' ? 'recording.wav' : (activeFile.name ?? 'audio.wav'));
    fd.append('name', name.trim());
    fd.append('language', language);
    fd.append('consent', 'true');

    if (editOptions) {
      fd.append('trim_start', String(editOptions.trimStart));
      if (editOptions.trimEnd !== null) fd.append('trim_end', String(editOptions.trimEnd));
      fd.append('speed', String(editOptions.speed));
      fd.append('pitch_semitones', String(editOptions.pitchSemitones));
      fd.append('gain_db', String(editOptions.gainDb));
      fd.append('fade_in_sec', String(editOptions.fadeInSec));
      fd.append('fade_out_sec', String(editOptions.fadeOutSec));
      fd.append('normalize_lufs', String(editOptions.normalizeLufs));
      fd.append('remove_silence', String(editOptions.removeSilence));
    }

    setBusy(true);
    try {
      await api.createVoice(fd);
      setName('');
      setConsent(false);
      setFileName(null);
      setSelectedFile(null);
      setEditOptions(null);
      if (fileRef.current) fileRef.current.value = '';
      rec.reset();
      onEnrolled();
    } catch (e2) {
      setErr(e2 instanceof ApiError ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  }

  /** Arrow-key navigation, as a real tablist requires. */
  function onTabKey(e: React.KeyboardEvent, i: number) {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    e.preventDefault();
    const next = (i + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length;
    const tab = TABS[next];
    if (tab) {
      setMode(tab.id);
      tabRefs.current[next]?.focus();
    }
  }

  const langs = languages.length ? languages : FALLBACK_LANGS;

  return (
    <form className="card" onSubmit={(e) => void submit(e)} aria-labelledby="enroll-h">
      <header className="card-head">
        <h2 id="enroll-h">Add a voice</h2>
      </header>

      <div className="tabs" role="tablist" aria-label="Reference audio source">
        {TABS.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => {
              tabRefs.current[i] = el;
            }}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={mode === t.id}
            aria-controls={`panel-${t.id}`}
            tabIndex={mode === t.id ? 0 : -1}
            className={mode === t.id ? 'on' : ''}
            onClick={() => setMode(t.id)}
            onKeyDown={(e) => onTabKey(e, i)}
          >
            {t.id === 'upload' ? <IconUpload size={14} /> : <IconMic size={14} />}
            {t.label}
          </button>
        ))}
      </div>

      {mode === 'upload' ? (
        <div role="tabpanel" id="panel-upload" aria-labelledby="tab-upload">
          <label className="field">
            <span className="field-label">Reference audio or video</span>
            <div className="file-drop">
              <input
                ref={fileRef}
                type="file"
                accept="audio/*,video/*,.wav,.mp3,.m4a,.aac,.flac,.ogg,.opus,.mp4,.mkv,.mov,.avi,.webm,.flv,.wmv,.wma,.3gp"
                onChange={(e) => {
                  const f = e.target.files?.[0] ?? null;
                  setFileName(f?.name ?? null);
                  setSelectedFile(f);
                }}
              />
              <IconUpload size={15} />
              <span>{fileName ?? 'Choose an audio or video file (WAV, MP3, MP4, MKV, etc.) — 6 to 15 seconds works best'}</span>
            </div>
          </label>
        </div>
      ) : (
        <div className="recorder" role="tabpanel" id="panel-record" aria-labelledby="tab-record">
          <div className="rec-row">
            <button
              type="button"
              className={`rec-btn ${rec.recording ? 'recording' : ''}`}
              onClick={() => (rec.recording ? rec.stop() : void rec.start())}
            >
              {rec.recording ? <IconStop size={15} /> : <IconMic size={15} />}
              {rec.recording ? `Stop · ${fmtSeconds(rec.seconds)}` : 'Start recording'}
            </button>

            {rec.recording && (
              <div className="meter" aria-hidden="true">
                {Array.from({ length: 14 }, (_, i) => (
                  <span
                    key={i}
                    style={{
                      transform: `scaleY(${Math.max(
                        0.12,
                        Math.min(1, rec.level * (0.55 + Math.sin(i * 1.3) * 0.45) * 2.2),
                      )})`,
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Render Built-in Audio Editor when a reference file is selected or recorded */}
      {((mode === 'upload' && selectedFile) || (mode === 'record' && rec.blob)) && (
        <AudioEditor
          file={
            mode === 'record'
              ? new File([rec.blob!], 'recording.wav', { type: 'audio/wav' })
              : selectedFile!
          }
          onApplyEdits={setEditOptions}
        />
      )}

      {mode === 'record' && (
        <>
          <p className="hint" aria-live="polite">
            {rec.recording
              ? 'Speak naturally in a quiet room.'
              : 'Aim for 6–15 seconds of clear, uninterrupted speech.'}
          </p>

          {rec.error && (
            <div className="inline-error" role="alert">
              <IconAlert size={14} /> {rec.error}
            </div>
          )}

          {recUrl && !rec.recording && (
            <div className="rec-preview">
              <audio controls src={recUrl} />
              <button type="button" className="link" onClick={rec.reset}>
                Discard
              </button>
            </div>
          )}
        </>
      )}

      <label className="field">
        <span className="field-label">Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. My voice" />
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

      <label className="consent">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        <span>I am authorized to clone this voice.</span>
      </label>

      {err && (
        <div className="inline-error" role="alert">
          <IconAlert size={14} /> {err}
        </div>
      )}

      <button className="btn primary" type="submit" disabled={busy} aria-busy={busy}>
        {busy ? <IconSpinner size={15} /> : null}
        {busy ? 'Uploading…' : 'Add voice'}
      </button>
    </form>
  );
}

const FALLBACK_LANGS: LanguageInfo[] = [
  { code: 'en', display_name: 'English', native_name: 'English', scripts: ['latin'], model_ids: [], requires_transform: false },
  { code: 'hi', display_name: 'Hindi (हिन्दी)', native_name: 'हिन्दी', scripts: ['latin'], model_ids: [], requires_transform: false },
  { code: 'ur', display_name: 'Urdu (اردو)', native_name: 'اردو', scripts: ['latin'], model_ids: [], requires_transform: false },
];
