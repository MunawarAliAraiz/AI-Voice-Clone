/**
 * One row for a job with no history row yet — queued, running, failed or
 * cancelled.
 *
 * Lives in its own module because BOTH Studio (an at-a-glance "what is still
 * cooking" strip under the Composer) and Recent render it. Two copies would
 * drift, and the queued/running states are exactly where a drift is invisible
 * until someone is waiting.
 */
import type { JobStatusResponse } from '../types/api';
import { fmtDuration, relativeTime } from '../lib/format';
import { IconAlert, IconCheck, IconSpinner, IconX } from './icons';

/**
 * Deliberately NOT rendered for succeeded jobs — those are history rows, and
 * rendering both would show every clip twice. That is why there is no audio
 * player here: a row in this group has no audio by definition.
 */
export function ActiveJobRow({
  job,
  onCancel,
  cancelling,
}: {
  job: JobStatusResponse;
  onCancel?: () => void;
  cancelling: boolean;
}) {
  // `analyze_llm` jobs share the `jobs` table but have no route and no audio;
  // their UI lives entirely in the Composer's Advanced editor. Skip rather
  // than crash on `job.route.source_script`.
  if (!job.route) return null;

  const rtl = job.route.source_script === 'arabic';
  return (
    <li className="hist-row">
      <div className="h-head">
        <div className="h-titled">
          {job.title && <p className="h-title">{job.title}</p>}
          <p className="h-text" dir={rtl ? 'rtl' : 'ltr'}>
            {job.input_text || <span className="muted">(no text recorded)</span>}
          </p>
        </div>
        <StatusChip status={job.status} />
      </div>

      <div className="h-meta">
        {job.profile_name && <span className="who">{job.profile_name}</span>}
        <span className="route-chip" title={job.route.rationale}>
          {job.route.model_display_name}
        </span>
        <span className="dot" />
        <time dateTime={job.queued_at}>{relativeTime(job.queued_at)}</time>
        {job.status === 'queued' && job.position != null && (
          <>
            <span className="dot" />
            <span>{job.position === 0 ? 'next up' : `#${job.position + 1} in queue`}</span>
          </>
        )}
        {job.status === 'running' && job.eta_sec != null && (
          <>
            <span className="dot" />
            <span>~{fmtDuration(job.eta_sec)} left</span>
          </>
        )}
      </div>

      {job.status === 'queued' && onCancel && (
        <button type="button" className="btn-sm danger" disabled={cancelling} onClick={onCancel}>
          {cancelling ? <IconSpinner size={13} /> : <IconX size={13} />}
          {cancelling ? 'Cancelling…' : 'Cancel'}
        </button>
      )}

      {job.status === 'failed' && job.error && (
        <div className="inline-error" role="alert">
          <IconAlert size={14} />
          <span>
            <strong>{String(job.error.code)}</strong> — {String(job.error.detail)}
          </span>
        </div>
      )}
    </li>
  );
}

function StatusChip({ status }: { status: JobStatusResponse['status'] }) {
  const icon =
    status === 'succeeded' ? <IconCheck size={12} />
    : status === 'failed' ? <IconAlert size={12} />
    : status === 'cancelled' ? <IconX size={12} />
    : <IconSpinner size={12} />;
  return (
    <span className={`status-chip ${status}`} aria-label={`Status: ${status}`}>
      {icon}
      {status}
    </span>
  );
}
