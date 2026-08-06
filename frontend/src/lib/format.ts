/** Small formatting helpers shared across the UI. */

/** Seconds → `m:ss`. Used by the recorder timer and the player. */
export function fmtSeconds(total: number): string {
  const s = Math.max(0, Math.floor(total));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** A short duration for metadata lines: `3.7s`. */
export function fmtDuration(sec: number | null | undefined): string {
  if (sec == null) return '—';
  return sec >= 60 ? fmtSeconds(sec) : `${sec.toFixed(1)}s`;
}

/** ISO timestamp → `just now` / `4m ago` / `3h ago` / `12 Aug`. */
export function relativeTime(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return '';
  const diff = Math.floor((Date.now() - then) / 1000);
  if (diff < 45) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86_400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604_800) return `${Math.floor(diff / 86_400)}d ago`;
  return new Date(then).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

export type DayBucket = 'Today' | 'Yesterday' | 'Earlier';

/** Which group heading a generation belongs under. */
export function dayBucket(iso: string): DayBucket {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return 'Earlier';
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const t = startOfToday.getTime();
  if (then >= t) return 'Today';
  if (then >= t - 86_400_000) return 'Yesterday';
  return 'Earlier';
}

/** Hz → `48 kHz`. */
export function fmtSampleRate(hz: number | null | undefined): string {
  if (!hz) return '—';
  return `${Math.round(hz / 1000)} kHz`;
}
