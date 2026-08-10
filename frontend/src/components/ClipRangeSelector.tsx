/**
 * Lets the user drag-select which 30-second window of a longer source file
 * gets extracted client-side, when the file is too long to use whole.
 *
 * A single range input drives the *start* of a fixed-length window (rather
 * than two independent handles) — simpler to drag, impossible to produce an
 * inverted or oversized range, and fully keyboard-accessible for free.
 */
import { useMemo } from 'react';
import { fmtSeconds } from '../lib/format';
import { MAX_CLIENT_CLIP_SEC } from '../lib/clientAudioExtract';

interface Props {
  durationSec: number;
  startSec: number;
  onChangeStart: (startSec: number) => void;
}

export function ClipRangeSelector({ durationSec, startSec, onChangeStart }: Props) {
  const clipLen = Math.min(MAX_CLIENT_CLIP_SEC, durationSec);
  const maxStart = Math.max(0, durationSec - clipLen);
  const endSec = Math.min(durationSec, startSec + clipLen);
  const windowPct = useMemo(
    () => ({
      left: durationSec > 0 ? (startSec / durationSec) * 100 : 0,
      width: durationSec > 0 ? (clipLen / durationSec) * 100 : 100,
    }),
    [startSec, clipLen, durationSec],
  );

  return (
    <div className="clip-range-selector" style={{ marginTop: '10px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: 'var(--muted)', marginBottom: '6px' }}>
        <span>
          This clip is {fmtSeconds(durationSec)} long — only {MAX_CLIENT_CLIP_SEC}s can be used. Drag to
          pick which part.
        </span>
        <span>
          {fmtSeconds(startSec)}–{fmtSeconds(endSec)}
        </span>
      </div>

      <div
        style={{
          position: 'relative',
          height: '10px',
          borderRadius: '5px',
          background: 'rgba(255,255,255,0.08)',
          marginBottom: '8px',
        }}
        aria-hidden="true"
      >
        <div
          style={{
            position: 'absolute',
            top: 0,
            bottom: 0,
            left: `${windowPct.left}%`,
            width: `${windowPct.width}%`,
            borderRadius: '5px',
            background: 'var(--accent, #6366f1)',
          }}
        />
      </div>

      <input
        type="range"
        min={0}
        max={maxStart}
        step={0.1}
        value={startSec}
        onChange={(e) => onChangeStart(Number(e.target.value))}
        aria-label={`Select ${MAX_CLIENT_CLIP_SEC}-second clip start time`}
        title="Drag to choose which part of the file to use"
        style={{ width: '100%', accentColor: 'var(--accent, #6366f1)' }}
      />
    </div>
  );
}
