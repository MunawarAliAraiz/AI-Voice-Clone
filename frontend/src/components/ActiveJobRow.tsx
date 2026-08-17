/**
 * One row for a job with no history row yet — queued, running, failed or
 * cancelled.
 *
 * Lives in its own module because BOTH Studio (an at-a-glance "what is still
 * cooking" strip under the Composer) and Recent render it. Two copies would
 * drift, and the queued/running states are exactly where a drift is invisible
 * until someone is waiting.
 */
import { memo } from 'react';
import type { JobStatusResponse } from '../types/api';
import { fmtDuration, relativeTime } from '../lib/format';
import { IconAlert, IconCheck, IconReset, IconSpinner, IconX } from './icons';

/**
 * Deliberately NOT rendered for succeeded jobs — those are history rows, and
 * rendering both would show every clip twice. That is why there is no audio
 * player here: a row in this group has no audio by definition.
 */
function ActiveJobRowImpl({
  job,
  onCancel,
  cancelling,
  onRetry,
  retrying,
  alreadyRetried = false,
}: {
  job: JobStatusResponse;
  onCancel?: () => void;
  // Optional: the Failed section renders rows that can never be cancelled.
  cancelling?: boolean;
  onRetry?: () => void;
  retrying?: boolean;
  /** True when a retry of this job already exists. Shows what happened
   *  instead of offering the button again. */
  alreadyRetried?: boolean;
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

      {/* JOB_INTERRUPTED's message literally reads "Re-submit it", and until
          this existed there was nothing to click — the only way back was to
          retype the text from the row still displaying it. Offered for any
          settled non-success, since a cancelled job is just as re-runnable. */}
      {(job.status === 'failed' || job.status === 'cancelled') &&
        onRetry &&
        (alreadyRetried ? (
          /* ALREADY RETRIED. The retry is a NEW row (the endpoint keeps
             history truthful rather than resurrecting this one), so without
             saying so here the button stays live on a failure that has
             already been re-queued — and four clicks produced four identical
             jobs with nothing to connect them. */
          <span className="muted">Retried — see In progress</span>
        ) : (
          <button
            type="button"
            className="btn-sm"
            disabled={retrying}
            onClick={onRetry}
            title="Queue this again with exactly the same text, voice and model"
          >
            {retrying ? <IconSpinner size={13} /> : <IconReset size={13} />}
            {retrying ? 'Re-queuing…' : 'Try again'}
          </button>
        ))}
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

/**
 * Memoized because both lists that render it now re-render every 2s while
 * anything is in flight, and a row whose job object has not changed has no
 * reason to re-render with it. `job` is a fresh object per fetch, so this only
 * pays off for rows whose FIELDS are unchanged — which is most of them, most
 * polls.
 */
export const ActiveJobRow = memo(ActiveJobRowImpl, (a, b) =>
  a.cancelling === b.cancelling &&
  a.retrying === b.retrying &&
  a.alreadyRetried === b.alreadyRetried &&
  a.job.id === b.job.id &&
  a.job.status === b.job.status &&
  a.job.title === b.job.title &&
  a.job.position === b.job.position &&
  a.job.eta_sec === b.job.eta_sec,
);
