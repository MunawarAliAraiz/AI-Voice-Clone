/**
 * Audio player.
 *
 * Replaces the native `<audio controls>`, whose chrome cannot be themed and
 * looked foreign against the rest of the UI. Wraps a real `<audio>` element so
 * Range requests and the signed media URLs keep working exactly as before —
 * this is presentation only.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { IconDownload, IconPause, IconPlay } from './icons';
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
      // A rejected play() is the only signal for a codec/disposition failure on
      // mobile, where the element silently refuses to start. Swallowing it is
      // what made the dead play button impossible to diagnose from the UI.
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

  const pct = duration > 0 ? (current / duration) * 100 : 0;

  // The signed URL already carries `?t=`, so the download opt-in is always `&`.
  // Needed because the `download` attribute is ignored cross-origin — the
  // deployed frontend and backend are on different hosts.
  const downloadHref = `${src}${src.includes('?') ? '&' : '?'}download=1`;

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

      <a
        className="icon-btn"
        href={downloadHref}
        download={downloadName ?? true}
        aria-label="Download audio"
        title="Download"
      >
        <IconDownload size={compact ? 14 : 16} />
      </a>
    </div>
  );
}
