/**
 * Built-in Audio Editor Component.
 *
 * Provides non-destructive controls (trim, playback speed, pitch shift,
 * gain, fade in/out, LUFS normalization, silence removal) with live audio previewing.
 */
import { useEffect, useState } from 'react';
import { api, ApiError } from '../services/api';
import { AudioPlayer } from './AudioPlayer';
import { IconSpark, IconSpinner } from './icons';

export interface AudioEditState {
  trimStart: number;
  trimEnd: number | null;
  speed: number;
  pitchSemitones: number;
  gainDb: number;
  fadeInSec: number;
  fadeOutSec: number;
  normalizeLufs: boolean;
  removeSilence: boolean;
}

interface Props {
  file: File;
  onApplyEdits: (edits: AudioEditState) => void;
}

export function AudioEditor({ file, onApplyEdits }: Props) {
  const [duration, setDuration] = useState<number>(30);
  const [trimStart, setTrimStart] = useState<number>(0);
  const [trimEnd, setTrimEnd] = useState<number>(30);
  const [speed, setSpeed] = useState<number>(1.0);
  const [pitch, setPitch] = useState<number>(0);
  const [gain, setGain] = useState<number>(0);
  const [fadeIn, setFadeIn] = useState<number>(0);
  const [fadeOut, setFadeOut] = useState<number>(0);
  const [normalize, setNormalize] = useState<boolean>(false);
  const [removeSilence, setRemoveSilence] = useState<boolean>(false);

  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [loadingPreview, setLoadingPreview] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Measure initial file duration using Audio element
  useEffect(() => {
    const url = URL.createObjectURL(file);
    const audio = new Audio(url);
    audio.onloadedmetadata = () => {
      if (Number.isFinite(audio.duration) && audio.duration > 0) {
        setDuration(Math.round(audio.duration * 10) / 10);
        setTrimEnd(Math.round(audio.duration * 10) / 10);
      }
    };
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [file]);

  const edits: AudioEditState = {
    trimStart,
    trimEnd,
    speed,
    pitchSemitones: pitch,
    gainDb: gain,
    fadeInSec: fadeIn,
    fadeOutSec: fadeOut,
    normalizeLufs: normalize,
    removeSilence,
  };

  useEffect(() => {
    onApplyEdits(edits);
  }, [trimStart, trimEnd, speed, pitch, gain, fadeIn, fadeOut, normalize, removeSilence]);

  async function generatePreview() {
    setLoadingPreview(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('trim_start', String(trimStart));
      if (trimEnd !== null) fd.append('trim_end', String(trimEnd));
      fd.append('speed', String(speed));
      fd.append('pitch_semitones', String(pitch));
      fd.append('gain_db', String(gain));
      fd.append('fade_in_sec', String(fadeIn));
      fd.append('fade_out_sec', String(fadeOut));
      fd.append('normalize_lufs', String(normalize));
      fd.append('remove_silence', String(removeSilence));

      const res = await api.previewEditVoice(fd);
      setPreviewUrl(res.preview_url);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Failed to process audio preview');
    } finally {
      setLoadingPreview(false);
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
    setError(null);
  }

  return (
    <div className="audio-editor card-inset" style={{ padding: '14px', borderRadius: '8px', background: 'var(--panel-inset, rgba(15,23,42,0.6))', border: '1px solid var(--line, rgba(255,255,255,0.1))', marginTop: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <h4 style={{ margin: 0, fontSize: '13px', fontWeight: 600, color: 'var(--fg, #f8fafc)' }}>
          🛠️ Audio Editor Controls
        </h4>
        <button
          type="button"
          className="btn-text"
          onClick={handleReset}
          style={{ fontSize: '11px', color: 'var(--muted)', background: 'transparent', border: 'none', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: '4px' }}
        >
          <span>↺</span> Reset Defaults
        </button>
      </div>

      {/* Timeline Trim Sliders */}
      <div className="field-group" style={{ marginBottom: '12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: 'var(--muted)', marginBottom: '4px' }}>
          <span>Trim Start: {trimStart.toFixed(1)}s</span>
          <span>Trim End: {trimEnd?.toFixed(1) ?? duration.toFixed(1)}s</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
          <input
            type="range"
            min={0}
            max={Math.max(0, (trimEnd ?? duration) - 0.5)}
            step={0.1}
            value={trimStart}
            onChange={(e) => setTrimStart(Number(e.target.value))}
            aria-label="Trim Start"
            title="Trim Start Time"
            style={{ accentColor: 'var(--accent, #6366f1)', width: '100%' }}
          />
          <input
            type="range"
            min={trimStart + 0.5}
            max={duration || 30}
            step={0.1}
            value={trimEnd ?? duration}
            onChange={(e) => setTrimEnd(Number(e.target.value))}
            aria-label="Trim End"
            title="Trim End Time"
            style={{ accentColor: 'var(--accent, #6366f1)', width: '100%' }}
          />
        </div>
      </div>

      {/* Speed / Pitch / Volume Controls */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '10px', marginBottom: '12px' }}>
        {/* Speed Selector */}
        <div>
          <label style={{ fontSize: '11px', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
            Playback Speed
          </label>
          <select
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value))}
            style={{ width: '100%', fontSize: '11px', padding: '4px 6px', borderRadius: '4px', background: 'rgba(255,255,255,0.06)', color: 'inherit', border: '1px solid rgba(255,255,255,0.12)' }}
          >
            <option value={0.5}>0.5x</option>
            <option value={0.75}>0.75x</option>
            <option value={1.0}>1.0x (Normal)</option>
            <option value={1.25}>1.25x</option>
            <option value={1.5}>1.5x</option>
            <option value={2.0}>2.0x</option>
          </select>
        </div>

        {/* Pitch Shift Slider */}
        <div>
          <label style={{ fontSize: '11px', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
            Pitch: {pitch > 0 ? `+${pitch}` : pitch} semitones
          </label>
          <input
            type="range"
            min={-12}
            max={12}
            step={1}
            value={pitch}
            onChange={(e) => setPitch(Number(e.target.value))}
            style={{ width: '100%', accentColor: 'var(--accent, #6366f1)' }}
          />
        </div>

        {/* Volume/Gain Slider */}
        <div>
          <label style={{ fontSize: '11px', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
            Gain: {gain > 0 ? `+${gain}` : gain} dB
          </label>
          <input
            type="range"
            min={-12}
            max={12}
            step={1}
            value={gain}
            onChange={(e) => setGain(Number(e.target.value))}
            style={{ width: '100%', accentColor: 'var(--accent, #6366f1)' }}
          />
        </div>
      </div>

      {/* Fade In / Fade Out & Toggles */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '10px', marginBottom: '12px' }}>
        <div>
          <label style={{ fontSize: '11px', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
            Fade In: {fadeIn}s
          </label>
          <input
            type="range"
            min={0}
            max={5}
            step={0.5}
            value={fadeIn}
            onChange={(e) => setFadeIn(Number(e.target.value))}
            style={{ width: '100%', accentColor: 'var(--accent, #6366f1)' }}
          />
        </div>

        <div>
          <label style={{ fontSize: '11px', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
            Fade Out: {fadeOut}s
          </label>
          <input
            type="range"
            min={0}
            max={5}
            step={0.5}
            value={fadeOut}
            onChange={(e) => setFadeOut(Number(e.target.value))}
            style={{ width: '100%', accentColor: 'var(--accent, #6366f1)' }}
          />
        </div>
      </div>

      {/* Toggles: LUFS Normalization & Silence Removal */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '14px', marginBottom: '12px', fontSize: '11px' }}>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={normalize}
            onChange={(e) => setNormalize(e.target.checked)}
            style={{ accentColor: 'var(--accent, #6366f1)' }}
          />
          Normalize LUFS (-16 LUFS)
        </label>

        <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={removeSilence}
            onChange={(e) => setRemoveSilence(e.target.checked)}
            style={{ accentColor: 'var(--accent, #6366f1)' }}
          />
          Remove Silence
        </label>
      </div>

      {/* Preview Action */}
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '8px' }}>
        <button
          type="button"
          className="btn compact"
          onClick={() => void generatePreview()}
          disabled={loadingPreview}
          style={{ width: 'auto', padding: '6px 12px', fontSize: '11px' }}
        >
          {loadingPreview ? <IconSpinner size={12} /> : <IconSpark size={12} />}
          {loadingPreview ? 'Processing Preview…' : 'Preview Edited Audio'}
        </button>
      </div>

      {error && (
        <p className="inline-error" style={{ fontSize: '11px', marginTop: '8px' }}>
          {error}
        </p>
      )}

      {/* Live Preview Player */}
      {previewUrl && (
        <div style={{ marginTop: '10px' }}>
          <span style={{ fontSize: '11px', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>
            Edited Audio Preview:
          </span>
          <AudioPlayer src={previewUrl} compact label="Edited audio preview" />
        </div>
      )}
    </div>
  );
}
