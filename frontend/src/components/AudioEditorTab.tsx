/**
 * Standalone Audio Editor Tab Component.
 *
 * Dedicated studio tab for non-destructive audio editing, processing,
 * previewing, format conversion, and optional voice enrollment.
 */
import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../services/api';
import { AudioPlayer } from './AudioPlayer';
import { ClipRangeSelector } from './ClipRangeSelector';
import { IconAlert, IconCheck, IconFileAudio, IconSpark, IconSpinner, IconUpload } from './icons';
import { MAX_CLIENT_CLIP_SEC, decodeMediaFile, extractWavClip } from '../lib/clientAudioExtract';

interface Props {
  onEnrolled?: () => void;
}

// Plain-language buckets over the raw semitone/dB dials — nobody outside audio
// engineering knows what "+3 semitones" means, but "Higher" is unambiguous.
const PITCH_OPTIONS = [
  { value: -6, label: '🔉 Much Lower' },
  { value: -3, label: '🔉 Lower' },
  { value: 0, label: '🎤 Normal' },
  { value: 3, label: '🔊 Higher' },
  { value: 6, label: '🔊 Much Higher' },
];

const VOLUME_OPTIONS = [
  { value: -12, label: '🔈 Much Quieter' },
  { value: -6, label: '🔈 Quieter' },
  { value: 0, label: '🔉 Normal' },
  { value: 6, label: '🔊 Louder' },
  { value: 12, label: '📢 Much Louder' },
];

export function AudioEditorTab({ onEnrolled }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [duration, setDuration] = useState<number>(0);
  const [trimStart, setTrimStart] = useState<number>(0);
  const [trimEnd, setTrimEnd] = useState<number>(0);

  // Client-side extraction (Phase 3): decode the picked file in-browser and
  // work with an already-clipped WAV instead of uploading the full original
  // (which matters most for video — the original problem this tab had).
  // `originalName`/`originalSizeMb` are kept separately because `file` gets
  // replaced by the extracted clip, losing the source's own name/size.
  const [decodeState, setDecodeState] = useState<'idle' | 'decoding' | 'ready' | 'unsupported'>('idle');
  const [decodedBuffer, setDecodedBuffer] = useState<AudioBuffer | null>(null);
  const [clipStart, setClipStart] = useState(0);
  const [originalName, setOriginalName] = useState<string | null>(null);
  const [originalSizeMb, setOriginalSizeMb] = useState<number | null>(null);
  const decodeReq = useRef(0);
  const extractTimer = useRef<number | null>(null);
  const [speed, setSpeed] = useState<number>(1.0);
  const [pitch, setPitch] = useState<number>(0);
  const [gain, setGain] = useState<number>(0);
  const [fadeIn, setFadeIn] = useState<number>(0);
  const [fadeOut, setFadeOut] = useState<number>(0);
  const [normalize, setNormalize] = useState<boolean>(false);
  const [removeSilence, setRemoveSilence] = useState<boolean>(false);

  // Preview & Processing state
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [processing, setProcessing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [meta, setMeta] = useState<{ duration_sec?: number; peak_dbfs?: number; is_clipped?: boolean } | null>(null);

  // Optional enrollment state
  const [showEnroll, setShowEnroll] = useState<boolean>(false);
  const [voiceName, setVoiceName] = useState<string>('');
  const [language, setLanguage] = useState<string>('ur');
  const [consent, setConsent] = useState<boolean>(false);
  const [enrolling, setEnrolling] = useState<boolean>(false);
  const [enrolledSuccess, setEnrolledSuccess] = useState<boolean>(false);

  // When a file is selected, calculate initial duration
  useEffect(() => {
    if (!file) {
      setDuration(0);
      setTrimStart(0);
      setTrimEnd(0);
      setPreviewUrl(null);
      setMeta(null);
      return;
    }

    const url = URL.createObjectURL(file);
    const audio = new Audio(url);
    audio.onloadedmetadata = () => {
      if (Number.isFinite(audio.duration) && audio.duration > 0) {
        const d = Math.round(audio.duration * 10) / 10;
        setDuration(d);
        setTrimEnd(d);
        setTrimStart(0);
      }
    };
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [file]);

  // Re-extract whenever the decoded source or the chosen 30s window changes,
  // and swap `file` for the extracted clip — the trim/speed/pitch controls
  // above then operate on the small clip, not the original upload. Debounced
  // so dragging ClipRangeSelector doesn't spawn a worker per pixel.
  useEffect(() => {
    if (!decodedBuffer) return;
    if (extractTimer.current !== null) window.clearTimeout(extractTimer.current);
    extractTimer.current = window.setTimeout(() => {
      const clipLen = Math.min(MAX_CLIENT_CLIP_SEC, decodedBuffer.duration);
      extractWavClip(decodedBuffer, clipStart, clipStart + clipLen)
        .then((blob) => {
          setFile(new File([blob], 'clip.wav', { type: 'audio/wav' }));
        })
        .catch(() => setError('Could not extract the selected clip from this file.'));
    }, 150);
    return () => {
      if (extractTimer.current !== null) window.clearTimeout(extractTimer.current);
    };
  }, [decodedBuffer, clipStart]);

  function onFilePicked(f: File | null) {
    setFile(f);
    setOriginalName(f?.name ?? null);
    setOriginalSizeMb(f ? f.size / (1024 * 1024) : null);
    setDecodedBuffer(null);
    setClipStart(0);
    setDecodeState(f ? 'decoding' : 'idle');
    const reqId = ++decodeReq.current;
    if (!f) return;
    decodeMediaFile(f)
      .then((buf) => {
        if (decodeReq.current !== reqId) return; // superseded by a newer pick
        setDecodedBuffer(buf);
        setDecodeState('ready');
      })
      .catch(() => {
        if (decodeReq.current !== reqId) return;
        // decodeAudioData can't demux this container (mkv/avi/flv are the
        // known cases) — `file` stays the raw original and the existing
        // server-side ffmpeg path in processAudio/saveAsVoiceProfile handles
        // it, same as before this feature existed. Visible, not silent.
        setDecodeState('unsupported');
      });
  }

  function resetFile() {
    ++decodeReq.current; // invalidate any in-flight decode
    setFile(null);
    setOriginalName(null);
    setOriginalSizeMb(null);
    setDecodedBuffer(null);
    setClipStart(0);
    setDecodeState('idle');
  }

  async function processAudio() {
    if (!file) return;
    setProcessing(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('trim_start', String(trimStart));
      fd.append('trim_end', String(trimEnd));
      fd.append('speed', String(speed));
      fd.append('pitch_semitones', String(pitch));
      fd.append('gain_db', String(gain));
      fd.append('fade_in_sec', String(fadeIn));
      fd.append('fade_out_sec', String(fadeOut));
      fd.append('normalize_lufs', String(normalize));
      fd.append('remove_silence', String(removeSilence));

      const res = await api.previewEditVoice(fd);
      setPreviewUrl(res.preview_url);
      setMeta({
        duration_sec: res.duration_sec,
        peak_dbfs: res.peak_dbfs,
        is_clipped: res.is_clipped,
      });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Failed to process audio');
    } finally {
      setProcessing(false);
    }
  }

  function handleReset() {
    setTrimStart(0);
    setTrimEnd(duration);
    setSpeed(1.0);
    setPitch(0);
    setGain(0);
    setFadeIn(0);
    setFadeOut(0);
    setNormalize(false);
    setRemoveSilence(false);
    setPreviewUrl(null);
    setMeta(null);
    setError(null);
  }

  async function saveAsVoiceProfile(e: React.FormEvent) {
    e.preventDefault();
    if (!file || !voiceName.trim() || !consent) return;

    setEnrolling(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('name', voiceName.trim());
      fd.append('language', language);
      fd.append('consent', 'true');
      fd.append('trim_start', String(trimStart));
      fd.append('trim_end', String(trimEnd));
      fd.append('speed', String(speed));
      fd.append('pitch_semitones', String(pitch));
      fd.append('gain_db', String(gain));
      fd.append('fade_in_sec', String(fadeIn));
      fd.append('fade_out_sec', String(fadeOut));
      fd.append('normalize_lufs', String(normalize));
      fd.append('remove_silence', String(removeSilence));

      await api.createVoice(fd);
      setEnrolledSuccess(true);
      if (onEnrolled) onEnrolled();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save voice profile');
    } finally {
      setEnrolling(false);
    }
  }

  return (
    <div className="audio-editor-tab" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>
      <div className="card">
        <div className="card-head">
          <h2>
            <IconFileAudio size={20} />
            Audio Editor Studio
          </h2>
          <span className="count">Extracts audio from video, too</span>
        </div>

        {/* File Drop Area */}
        {!file ? (
          <div className="file-drop" style={{ padding: 'var(--space-8)', flexDirection: 'column', textAlign: 'center', gap: 'var(--space-3)' }}>
            <input
              type="file"
              accept="audio/*,video/*,.wav,.mp3,.m4a,.aac,.flac,.ogg,.opus,.mp4,.mkv,.mov,.avi,.webm,.flv,.wmv,.wma,.3gp"
              onChange={(e) => onFilePicked(e.target.files?.[0] ?? null)}
              aria-label="Upload an audio or video file for editing"
            />
            <IconUpload size={32} />
            <div style={{ width: '100%' }}>
              <strong style={{ display: 'block', fontSize: 'var(--text-md)', color: 'var(--text)' }}>
                Drop an audio or video file here, or click to browse
              </strong>
              <span className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                Video's audio track is extracted automatically, up to 30 seconds
              </span>
            </div>
          </div>
        ) : (
          <div>
            {decodeState === 'decoding' && (
              <p className="hint" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <IconSpinner size={12} /> Analyzing file in your browser…
              </p>
            )}

            {decodeState === 'unsupported' && (
              <p className="hint" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <IconAlert size={12} /> Couldn't preview this file's audio in your browser — it will be
                uploaded as-is and processed on the server instead.
              </p>
            )}

            {decodeState === 'ready' && decodedBuffer && decodedBuffer.duration > MAX_CLIENT_CLIP_SEC && (
              <ClipRangeSelector
                durationSec={decodedBuffer.duration}
                startSec={clipStart}
                onChangeStart={setClipStart}
              />
            )}

            {/* File Info Bar */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: 'var(--space-3) var(--space-4)',
                background: 'var(--panel-inset)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--line)',
                marginTop: 'var(--space-3)',
                marginBottom: 'var(--space-5)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', overflow: 'hidden' }}>
                <IconFileAudio size={20} />
                <div style={{ overflow: 'hidden' }}>
                  <strong style={{ display: 'block', fontSize: 'var(--text-md)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {originalName ?? file.name}
                  </strong>
                  <span className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                    {decodeState === 'ready'
                      ? `${duration > 0 ? duration.toFixed(1) : '…'}s clip · ${(originalSizeMb ?? 0).toFixed(2)} MB original file`
                      : `Duration: ${duration > 0 ? `${duration.toFixed(1)}s` : 'Loading…'} · ${(originalSizeMb ?? file.size / (1024 * 1024)).toFixed(2)} MB`}
                  </span>
                </div>
              </div>

              <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                <button type="button" className="btn-sm" onClick={handleReset} title="Reset sliders">
                  <IconSpark size={14} /> Reset
                </button>
                <button type="button" className="btn-sm danger" onClick={resetFile}>
                  Change File
                </button>
              </div>
            </div>

            {/* Editing Controls Grid */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 'var(--space-5)', marginBottom: 'var(--space-5)' }}>
              {/* Trimming Section */}
              <div style={{ background: 'var(--panel-inset)', padding: 'var(--space-4)', borderRadius: 'var(--radius-md)', border: '1px solid var(--line)' }}>
                <h4 style={{ margin: '0 0 var(--space-3)', fontSize: 'var(--text-sm)', color: 'var(--text)', fontWeight: 600 }}>
                  ✂️ Timeline Trimming
                </h4>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--text-sm)', color: 'var(--muted)', marginBottom: 'var(--space-2)' }}>
                  <span>Start: {trimStart.toFixed(1)}s</span>
                  <span>End: {trimEnd.toFixed(1)}s</span>
                </div>
                <div className="field" style={{ marginBottom: 'var(--space-3)' }}>
                  <label className="field-label">Trim Start (sec)</label>
                  <input
                    type="range"
                    min={0}
                    max={Math.max(0, trimEnd - 0.5)}
                    step={0.1}
                    value={trimStart}
                    onChange={(e) => setTrimStart(Number(e.target.value))}
                  />
                </div>
                <div className="field">
                  <label className="field-label">Trim End (sec)</label>
                  <input
                    type="range"
                    min={trimStart + 0.5}
                    max={duration || 30}
                    step={0.1}
                    value={trimEnd || duration}
                    onChange={(e) => setTrimEnd(Number(e.target.value))}
                  />
                </div>
              </div>

              {/* Speed & Pitch Section */}
              <div style={{ background: 'var(--panel-inset)', padding: 'var(--space-4)', borderRadius: 'var(--radius-md)', border: '1px solid var(--line)' }}>
                <h4 style={{ margin: '0 0 var(--space-3)', fontSize: 'var(--text-sm)', color: 'var(--text)', fontWeight: 600 }}>
                  🎚️ Speed & Pitch
                </h4>
                <div className="field" style={{ marginBottom: 'var(--space-3)' }}>
                  <label className="field-label">Playback Speed</label>
                  <div className="select-wrap">
                    <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
                      <option value={0.5}>0.5x (Slow Motion)</option>
                      <option value={0.75}>0.75x</option>
                      <option value={1.0}>1.0x (Normal)</option>
                      <option value={1.25}>1.25x</option>
                      <option value={1.5}>1.5x</option>
                      <option value={2.0}>2.0x (Fast)</option>
                    </select>
                  </div>
                </div>
                <div className="field">
                  <label className="field-label">Pitch</label>
                  <div className="select-wrap">
                    <select value={pitch} onChange={(e) => setPitch(Number(e.target.value))}>
                      {PITCH_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </div>

              {/* Volume & Fades Section */}
              <div style={{ background: 'var(--panel-inset)', padding: 'var(--space-4)', borderRadius: 'var(--radius-md)', border: '1px solid var(--line)' }}>
                <h4 style={{ margin: '0 0 var(--space-3)', fontSize: 'var(--text-sm)', color: 'var(--text)', fontWeight: 600 }}>
                  🔊 Volume & Fades
                </h4>
                <div className="field" style={{ marginBottom: 'var(--space-3)' }}>
                  <label className="field-label">Volume</label>
                  <div className="select-wrap">
                    <select value={gain} onChange={(e) => setGain(Number(e.target.value))}>
                      {VOLUME_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="row">
                  <div className="field">
                    <label className="field-label">Fade In: {fadeIn}s</label>
                    <input
                      type="range"
                      min={0}
                      max={5}
                      step={0.5}
                      value={fadeIn}
                      onChange={(e) => setFadeIn(Number(e.target.value))}
                    />
                  </div>
                  <div className="field">
                    <label className="field-label">Fade Out: {fadeOut}s</label>
                    <input
                      type="range"
                      min={0}
                      max={5}
                      step={0.5}
                      value={fadeOut}
                      onChange={(e) => setFadeOut(Number(e.target.value))}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Toggles Bar */}
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: 'var(--space-5)',
                padding: 'var(--space-4)',
                background: 'var(--panel-inset)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--line)',
                marginBottom: 'var(--space-5)',
              }}
            >
              <label className="consent" style={{ margin: 0 }}>
                <input
                  type="checkbox"
                  checked={normalize}
                  onChange={(e) => setNormalize(e.target.checked)}
                />
                <span>Even Out Loudness (broadcast standard)</span>
              </label>

              <label className="consent" style={{ margin: 0 }}>
                <input
                  type="checkbox"
                  checked={removeSilence}
                  onChange={(e) => setRemoveSilence(e.target.checked)}
                />
                <span>Remove Silence Automatically</span>
              </label>
            </div>

            {/* Action Bar */}
            <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center' }}>
              <button
                type="button"
                className="btn primary"
                onClick={() => void processAudio()}
                disabled={processing || decodeState === 'decoding'}
                style={{ flex: 1 }}
              >
                {processing || decodeState === 'decoding' ? <IconSpinner size={16} /> : <IconSpark size={16} />}
                {processing
                  ? 'Processing Audio with FFmpeg…'
                  : decodeState === 'decoding'
                    ? 'Analyzing file…'
                    : 'Process & Preview Edited Audio'}
              </button>

              {previewUrl && (
                <button
                  type="button"
                  className="btn-sm on"
                  onClick={() => setShowEnroll(!showEnroll)}
                >
                  {showEnroll ? 'Hide Profile Save' : '💾 Save as Voice Profile'}
                </button>
              )}
            </div>

            {error && (
              <div className="inline-error" style={{ marginTop: 'var(--space-3)' }}>
                <IconAlert size={16} /> {error}
              </div>
            )}

            {/* Processed Audio Result Player */}
            {previewUrl && (
              <div className="result">
                <div className="result-head" style={{ marginBottom: 'var(--space-3)' }}>
                  <strong style={{ fontSize: 'var(--text-md)', color: 'var(--accent)' }}>
                    ✨ Edited Audio Result
                  </strong>
                  {meta?.duration_sec && (
                    <span className="route-chip solid">
                      Duration: {meta.duration_sec.toFixed(1)}s
                    </span>
                  )}
                  {meta?.peak_dbfs !== undefined && (
                    <span className="route-chip">
                      Peak: {meta.peak_dbfs.toFixed(1)} dBFS
                    </span>
                  )}
                </div>

                <AudioPlayer
                  src={previewUrl}
                  label="Edited audio output"
                  downloadName={`${(originalName ?? file.name).replace(/\.[^/.]+$/, '')}-edited.wav`}
                />
              </div>
            )}

            {/* Save as Voice Profile Form */}
            {previewUrl && showEnroll && (
              <form
                onSubmit={(e) => void saveAsVoiceProfile(e)}
                style={{
                  marginTop: 'var(--space-5)',
                  padding: 'var(--space-5)',
                  background: 'var(--panel-inset)',
                  borderRadius: 'var(--radius-lg)',
                  border: '1px solid var(--accent-line)',
                }}
              >
                <h4 style={{ margin: '0 0 var(--space-3)', fontSize: 'var(--text-md)', color: 'var(--accent)' }}>
                  💾 Save Edited Audio as a Voice Profile
                </h4>

                <div className="row">
                  <div className="field">
                    <label className="field-label">Voice Profile Name</label>
                    <input
                      value={voiceName}
                      onChange={(e) => setVoiceName(e.target.value)}
                      placeholder="e.g. My Edited Voice"
                      required
                    />
                  </div>
                  <div className="field">
                    <label className="field-label">Primary Language</label>
                    <div className="select-wrap">
                      <select value={language} onChange={(e) => setLanguage(e.target.value)}>
                        <option value="ur">Urdu (Roman / Perso-Arabic)</option>
                        <option value="hi">Hindi</option>
                        <option value="en">English</option>
                      </select>
                    </div>
                  </div>
                </div>

                <label className="consent">
                  <input
                    type="checkbox"
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                    required
                  />
                  <span>I confirm I am authorized to use this reference voice.</span>
                </label>

                {enrolledSuccess ? (
                  <div className="inline-error" style={{ color: 'var(--success)' }}>
                    <IconCheck size={16} /> Voice profile enrolled successfully!
                  </div>
                ) : (
                  <button type="submit" className="btn primary sm" disabled={enrolling || !voiceName.trim() || !consent}>
                    {enrolling ? <IconSpinner size={14} /> : null}
                    {enrolling ? 'Saving Profile…' : 'Save Voice Profile'}
                  </button>
                )}
              </form>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
