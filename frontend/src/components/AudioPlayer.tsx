/**
 * Audio player.
 *
 * Replaces the native `<audio controls>`, whose chrome cannot be themed and
 * looked foreign against the rest of the UI. Wraps a real `<audio>` element so
 * Range requests and the signed media URLs keep working exactly as before —
 * this is presentation only.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { IconDownload, IconPause, IconPlay, IconSpinner } from './icons';
import { fmtSeconds } from '../lib/format';

interface Props {
  src: string;
  /** Compact variant for dense list rows. */
  compact?: boolean;
  autoPlay?: boolean;
  downloadName?: string;
  /** Accessible description, e.g. the text that was spoken. */
  label?: string;
}

export function AudioPlayer({ src, compact, autoPlay, downloadName, label }: Props) {
  const ref = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [downloadFmt, setDownloadFmt] = useState<string>('wav');
  const [downloading, setDownloading] = useState(false);

  // Reset when the source changes (e.g. a new generation lands).
  useEffect(() => {
    setPlaying(false);
    setCurrent(0);
    setDuration(0);
    setError(null);
  }, [src]);

  const toggle = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    if (el.paused) {
      void el.play().then(
        () => setError(null),
        (err: unknown) => {
          setPlaying(false);
          setError(err instanceof Error ? err.message : 'Playback failed');
        },
      );
    } else {
      el.pause();
    }
  }, []);

  const seek = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const el = ref.current;
    if (!el) return;
    const t = Number(e.target.value);
    el.currentTime = t;
    setCurrent(t);
  }, []);

  const handleDownload = useCallback(
    async (e: React.MouseEvent<HTMLAnchorElement>) => {
      e.preventDefault();
      if (downloading) return;
      setDownloading(true);

      const downloadUrl = `${src}${src.includes('?') ? '&' : '?'}download=1&format=${downloadFmt}`;
      const headers = new Headers();
      const key = localStorage.getItem('vcs_api_key') ?? '';
      if (key) headers.set('X-API-Key', key);
      headers.set('ngrok-skip-browser-warning', 'true');

      try {
        const res = await fetch(downloadUrl, { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const blobUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = blobUrl;
        const targetName = downloadName
          ? downloadName.replace(/\.[^/.]+$/, `.${downloadFmt}`)
          : `audio.${downloadFmt}`;
        a.download = targetName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(blobUrl);
      } catch (err) {
        console.error('Download error:', err);
        window.location.href = downloadUrl;
      } finally {
        setDownloading(false);
      }
    },
    [src, downloadFmt, downloadName, downloading],
  );

  const pct = duration > 0 ? (current / duration) * 100 : 0;
  const downloadHref = `${src}${src.includes('?') ? '&' : '?'}download=1&format=${downloadFmt}`;

  return (
    <div className={`player ${compact ? 'compact' : ''}`}>
      <audio
        ref={ref}
        src={src}
        autoPlay={autoPlay}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={(e) => setCurrent(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => {
          const d = e.currentTarget.duration;
          setDuration(Number.isFinite(d) ? d : 0);
        }}
        onError={() => {
          setPlaying(false);
          setError('Could not load audio');
        }}
      />

      <button
        type="button"
        className="play-btn"
        onClick={toggle}
        aria-label={playing ? `Pause${label ? `: ${label}` : ''}` : `Play${label ? `: ${label}` : ''}`}
      >
        {playing ? <IconPause size={compact ? 14 : 16} /> : <IconPlay size={compact ? 14 : 16} />}
      </button>

      <div className="player-track">
        <div className="track-fill" style={{ width: `${pct}%` }} />
        <input
          type="range"
          min={0}
          max={duration || 0}
          step={0.01}
          value={current}
          onChange={seek}
          aria-label="Seek"
          disabled={!duration}
        />
      </div>

      <span className="player-time">
        {error ? (
          <span className="player-error" role="alert" title={error}>
            {error}
          </span>
        ) : (
          <>
            {fmtSeconds(current)} / {duration ? fmtSeconds(duration) : '--:--'}
          </>
        )}
      </span>

      <div className="download-wrap" style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
        <select
          value={downloadFmt}
          onChange={(e) => setDownloadFmt(e.target.value)}
          aria-label="Download audio format"
          title="Choose audio format to download"
          style={{
            background: 'rgba(255, 255, 255, 0.07)',
            color: 'var(--muted)',
            border: '1px solid var(--line)',
            borderRadius: '4px',
            fontSize: compact ? '10px' : '11px',
            padding: '2px 4px',
            cursor: 'pointer',
          }}
        >
          <option value="wav">WAV</option>
          <option value="mp3">MP3</option>
          <option value="flac">FLAC</option>
          <option value="ogg">OGG</option>
          <option value="m4a">M4A</option>
        </select>
        <a
          className="icon-btn"
          href={downloadHref}
          onClick={handleDownload}
          download={downloadName ? downloadName.replace(/\.[^/.]+$/, `.${downloadFmt}`) : true}
          aria-label={`Download audio as ${downloadFmt.toUpperCase()}`}
          title={downloading ? 'Downloading...' : `Download as ${downloadFmt.toUpperCase()}`}
        >
          {downloading ? <IconSpinner size={compact ? 14 : 16} /> : <IconDownload size={compact ? 14 : 16} />}
        </a>
      </div>
    </div>
  );
}
