/**
 * Recent — the job queue's own view: every generation attempt, including the
 * ones `HistoryPanel` structurally cannot show (still queued, running, or
 * failed before a `generation_history` row ever existed). `HistoryPanel`
 * stays the place to search/favorite/bulk-delete completed clips; this is
 * where "did my last request actually go through" gets answered.
 */
import { useState } from 'react';
import { dayBucket, fmtDuration, relativeTime, type DayBucket } from '../lib/format';
import { isTerminal, useCancelJobMutation, useJobsList } from '../hooks/queries';
import { mediaUrl } from '../services/api';
import type { JobStatusResponse } from '../types/api';
import { AudioPlayer } from './AudioPlayer';
import { IconAlert, IconCheck, IconSpinner, IconX } from './icons';

const PAGE_SIZE = 20;
const GROUPS: DayBucket[] = ['Today', 'Yesterday', 'Earlier'];

export function JobsPanel() {
  const [pageCount, setPageCount] = useState(1);
  const { data, isLoading, isFetching, error } = useJobsList(1, PAGE_SIZE * pageCount);
  const cancel = useCancelJobMutation();

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const hasMore = items.length < total;

  const grouped = new Map<DayBucket, JobStatusResponse[]>();
  for (const job of items) {
    const key = dayBucket(job.queued_at);
    const list = grouped.get(key);
    if (list) list.push(job);
    else grouped.set(key, [job]);
  }

  return (
    <section className="card history" aria-labelledby="jobs-h">
      <header className="card-head">
        <h2 id="jobs-h">
          Recent
          {total > 0 && <span className="count">{total}</span>}
        </h2>
      </header>

      {error && (
        <div className="inline-error" role="alert">
          <IconAlert size={14} /> Could not load recent jobs.
        </div>
      )}

      {isLoading ? (
        <p className="muted center">Loading…</p>
      ) : items.length === 0 ? (
        <div className="empty">
          <div className="empty-mark" aria-hidden="true">
            <span /><span /><span /><span />
          </div>
          <p className="empty-title">Nothing yet</p>
          <p className="empty-body">Generate something and it'll show up here immediately.</p>
        </div>
      ) : (
        <>
          {GROUPS.map((g) => {
            const rows = grouped.get(g);
            if (!rows?.length) return null;
            return (
              <div className="hist-group" key={g}>
                <div className="group-label">
                  <span>{g}</span>
                  <span className="rule" />
                  <span className="group-count">{rows.length}</span>
                </div>
                <ul className="hist">
                  {rows.map((job) => (
                    <JobRow
                      key={job.id}
                      job={job}
                      onCancel={() => cancel.mutate(job.id)}
                      cancelling={cancel.isPending && cancel.variables === job.id}
                    />
                  ))}
                </ul>
              </div>
            );
          })}

          <div className="hist-foot">
            <span className="muted">
              Showing {items.length} of {total}
            </span>
            {hasMore && (
              <button
                type="button"
                className="btn-sm"
                onClick={() => setPageCount((p) => p + 1)}
                disabled={isFetching}
              >
                {isFetching ? <IconSpinner size={13} /> : null}
                {isFetching ? 'Loading…' : 'Load more'}
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function JobRow({
  job,
  onCancel,
  cancelling,
}: {
  job: JobStatusResponse;
  onCancel: () => void;
  cancelling: boolean;
}) {
  // Recent lists every job kind on the shared `jobs` table (GET /api/jobs),
  // but this row only knows how to render a 'synthesize' job's route chip
  // and audio result. 'analyze_llm' jobs (route: null, result: an
  // AnalyzeLlmResult with no `audio_url`) never had a UI surface here and
  // still don't — the "AI suggest" flow lives entirely inside the Composer's
  // Advanced editor (Composer.tsx), never in Recent. Skip rather than crash
  // on `job.route.source_script` if one shows up in this list.
  if (!job.route) return null;
  if (job.result !== null && !('audio_url' in job.result)) return null;

  const rtl = job.route.source_script === 'arabic';
  return (
    <li className="hist-row">
      <div className="h-head">
        <p className="h-text" dir={rtl ? 'rtl' : 'ltr'}>
          {job.input_text || <span className="muted">(no text recorded)</span>}
        </p>
        <StatusChip status={job.status} />
      </div>

      <div className="h-meta">
        {job.profile_name && <span className="who">{job.profile_name}</span>}
        <span className="route-chip" title={job.route.rationale}>
          {job.route.model_display_name}
        </span>
        {job.route.lossy && <span className="tag warn">lossy</span>}
        {job.result != null && job.result.segment_count > 1 && (
          <span className="tag" title="Rendered as separately-synthesized, pause-joined segments">
            directed · {job.result.segment_count} segments
          </span>
        )}
        {job.result?.duration_sec != null && (
          <>
            <span className="dot" />
            <span>{fmtDuration(job.result.duration_sec)}</span>
          </>
        )}
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

      {job.status === 'queued' && (
        <button type="button" className="btn-sm danger" disabled={cancelling} onClick={onCancel}>
          {cancelling ? <IconSpinner size={13} /> : <IconX size={13} />}
          {cancelling ? 'Cancelling…' : 'Cancel'}
        </button>
      )}

      {job.status === 'failed' && job.error && (
        <div className="inline-error" role="alert">
          <IconAlert size={14} />
          <span>
            <strong>{job.error.code}</strong> — {job.error.detail}
          </span>
        </div>
      )}

      {job.status === 'succeeded' && job.result && (
        <AudioPlayer
          compact
          src={mediaUrl(job.result.audio_url)}
          label={(job.input_text ?? '').slice(0, 40)}
          downloadName={`${job.profile_name ?? 'voice'}-${job.result.id}.wav`}
        />
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

// Re-export so callers can check terminal-ness without importing the hooks
// module just for a type predicate.
export { isTerminal };
