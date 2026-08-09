/**
 * Recent generations.
 *
 * The predecessor rendered `null` when empty, capped at 20 with no way to see
 * more, and fired favourite/delete with no `.catch` — a failed action was
 * completely silent. This version groups by day, filters, paginates, updates
 * the star optimistically, and always surfaces failure.
 */
import { useMemo, useState } from 'react';
import { api, ApiError, mediaUrl } from '../services/api';
import type { HistoryItem } from '../types/api';
import { AudioPlayer } from './AudioPlayer';
import { IconAlert, IconCheckSquare, IconSearch, IconSpinner, IconStar, IconTrash, IconX } from './icons';
import { dayBucket, fmtDuration, relativeTime, type DayBucket } from '../lib/format';

interface Props {
  items: HistoryItem[];
  total: number;
  loading: boolean;
  hasMore: boolean;
  onLoadMore: () => void;
  onChanged: () => void;
}

type Filter = 'all' | 'favorites';
const GROUPS: DayBucket[] = ['Today', 'Yesterday', 'Earlier'];

export function HistoryPanel({ items, total, loading, hasMore, onLoadMore, onChanged }: Props) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  /** id → optimistic favourite value, pending confirmation from the server. */
  const [pendingFav, setPendingFav] = useState<Record<number, boolean>>({});
  const [confirming, setConfirming] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirmingBulk, setConfirmingBulk] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);

  const isFav = (h: HistoryItem) => pendingFav[h.id] ?? h.is_favorite;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((h) => {
      if (filter === 'favorites' && !(pendingFav[h.id] ?? h.is_favorite)) return false;
      if (!q) return true;
      return (
        h.input_text.toLowerCase().includes(q) ||
        (h.profile_name ?? '').toLowerCase().includes(q) ||
        h.route.model_display_name.toLowerCase().includes(q)
      );
    });
  }, [items, query, filter, pendingFav]);

  const grouped = useMemo(() => {
    const m = new Map<DayBucket, HistoryItem[]>();
    for (const h of filtered) {
      const k = dayBucket(h.created_at);
      const list = m.get(k);
      if (list) list.push(h);
      else m.set(k, [h]);
    }
    return m;
  }, [filtered]);

  async function toggleFav(h: HistoryItem) {
    const next = !isFav(h);
    setPendingFav((p) => ({ ...p, [h.id]: next }));
    setError(null);
    try {
      await api.setFavorite(h.id, next);
      onChanged();
    } catch (e) {
      // Roll the optimistic update back and say why.
      setPendingFav((p) => {
        const { [h.id]: _dropped, ...rest } = p;
        return rest;
      });
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function remove(id: number) {
    setDeleting(id);
    setError(null);
    try {
      await api.deleteHistory(id);
      setConfirming(null);
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setDeleting(null);
    }
  }

  function toggleSelectMode() {
    setSelectMode((on) => !on);
    setSelected(new Set());
    setConfirmingBulk(false);
  }

  function toggleSelected(id: number) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectAllShown() {
    setSelected(new Set(filtered.map((h) => h.id)));
  }

  async function removeSelected() {
    setBulkDeleting(true);
    setError(null);
    const ids = [...selected];
    // No bulk-delete endpoint on the backend — fire them individually, same
    // as single delete. Failures are collected rather than aborting the rest,
    // so one bad id doesn't strand every other selected item undeleted.
    const results = await Promise.allSettled(ids.map((id) => api.deleteHistory(id)));
    const failed = results.filter((r) => r.status === 'rejected').length;
    setBulkDeleting(false);
    setConfirmingBulk(false);
    setSelected(new Set());
    setSelectMode(false);
    onChanged();
    if (failed > 0) {
      setError(
        failed === ids.length
          ? 'Could not delete the selected generations.'
          : `Deleted ${ids.length - failed} of ${ids.length} — ${failed} failed.`
      );
    }
  }

  const showing = filtered.length;

  return (
    <section className="card history" aria-labelledby="history-h">
      <header className="card-head">
        <h2 id="history-h">
          History
          {total > 0 && <span className="count">{total}</span>}
        </h2>

        {items.length > 0 && (
          <div className="history-tools">
            <div className="search">
              <IconSearch size={14} />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search"
                aria-label="Search generations"
              />
              {query && (
                <button
                  type="button"
                  className="icon-btn tiny"
                  onClick={() => setQuery('')}
                  aria-label="Clear search"
                >
                  <IconX size={12} />
                </button>
              )}
            </div>

            <div className="segmented" role="group" aria-label="Filter generations">
              <button
                type="button"
                className={filter === 'all' ? 'on' : ''}
                aria-pressed={filter === 'all'}
                onClick={() => setFilter('all')}
              >
                All
              </button>
              <button
                type="button"
                className={filter === 'favorites' ? 'on' : ''}
                aria-pressed={filter === 'favorites'}
                onClick={() => setFilter('favorites')}
              >
                <IconStar size={13} /> Favorites
              </button>
            </div>

            <button
              type="button"
              className={`btn-sm ${selectMode ? 'on' : ''}`}
              aria-pressed={selectMode}
              onClick={toggleSelectMode}
            >
              <IconCheckSquare size={13} /> {selectMode ? 'Done' : 'Select'}
            </button>
          </div>
        )}
      </header>

      {selectMode && (
        <div className="bulkbar" role="toolbar" aria-label="Bulk actions">
          <label className="bulkbar-all">
            <input
              type="checkbox"
              checked={selected.size > 0 && selected.size === filtered.length}
              ref={(el) => {
                if (el) el.indeterminate = selected.size > 0 && selected.size < filtered.length;
              }}
              onChange={() => (selected.size === filtered.length ? setSelected(new Set()) : selectAllShown())}
              aria-label="Select all shown"
            />
            {selected.size > 0 ? `${selected.size} selected` : 'Select all'}
          </label>

          {selected.size > 0 && !confirmingBulk && (
            <button type="button" className="btn-sm danger" onClick={() => setConfirmingBulk(true)}>
              <IconTrash size={13} /> Delete {selected.size}
            </button>
          )}

          {confirmingBulk && (
            <div className="confirm bulk" role="alert">
              <span>Delete {selected.size} generation{selected.size === 1 ? '' : 's'}?</span>
              <div className="confirm-actions">
                <button
                  type="button"
                  className="btn-sm danger"
                  disabled={bulkDeleting}
                  onClick={() => void removeSelected()}
                >
                  {bulkDeleting ? <IconSpinner size={13} /> : null}
                  {bulkDeleting ? 'Deleting…' : 'Delete'}
                </button>
                <button
                  type="button"
                  className="btn-sm"
                  onClick={() => setConfirmingBulk(false)}
                  disabled={bulkDeleting}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="inline-error" role="alert">
          <IconAlert size={14} /> {error}
        </div>
      )}

      {items.length === 0 ? (
        <EmptyState
          title="No generations yet"
          body="Pick a voice, type something, and hit Generate — your clips will collect here."
        />
      ) : showing === 0 ? (
        <EmptyState
          title="Nothing matches"
          body={
            filter === 'favorites' && !query
              ? 'You have not starred any generations yet.'
              : `No generation matches “${query}”.`
          }
          action={
            <button
              type="button"
              className="link"
              onClick={() => {
                setQuery('');
                setFilter('all');
              }}
            >
              Clear filters
            </button>
          }
        />
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
                  {rows.map((h, i) => (
                    <li
                      key={h.id}
                      className={`hist-row ${deleting === h.id ? 'removing' : ''} ${selected.has(h.id) ? 'picked' : ''}`}
                      style={{ animationDelay: `${Math.min(i, 8) * 30}ms` }}
                    >
                      <div className="h-head">
                        {selectMode && (
                          <input
                            type="checkbox"
                            className="h-check"
                            checked={selected.has(h.id)}
                            onChange={() => toggleSelected(h.id)}
                            aria-label={`Select "${h.input_text.slice(0, 40)}"`}
                          />
                        )}
                        <p className="h-text" dir={isRtl(h.route.source_script) ? 'rtl' : 'ltr'}>
                          {h.input_text}
                        </p>
                        {!selectMode && (
                          <div className="h-actions">
                            <button
                              type="button"
                              className={`icon-btn star ${isFav(h) ? 'on' : ''}`}
                              aria-label={isFav(h) ? 'Remove from favorites' : 'Add to favorites'}
                              aria-pressed={isFav(h)}
                              onClick={() => void toggleFav(h)}
                            >
                              <IconStar size={15} />
                            </button>
                            <button
                              type="button"
                              className="icon-btn danger"
                              aria-label="Delete generation"
                              onClick={() => setConfirming(h.id)}
                            >
                              <IconTrash size={15} />
                            </button>
                          </div>
                        )}
                      </div>

                      <div className="h-meta">
                        {h.profile_name && <span className="who">{h.profile_name}</span>}
                        <span className="route-chip" title={h.route.rationale}>
                          {h.route.model_display_name}
                        </span>
                        {h.route.lossy && <span className="tag warn">lossy</span>}
                        <span className="dot" />
                        <span>{fmtDuration(h.duration_sec)}</span>
                        <span className="dot" />
                        <time dateTime={h.created_at}>{relativeTime(h.created_at)}</time>
                      </div>

                      {confirming === h.id ? (
                        <div className="confirm" role="alert">
                          <span>Delete this generation?</span>
                          <div className="confirm-actions">
                            <button
                              type="button"
                              className="btn-sm danger"
                              disabled={deleting === h.id}
                              onClick={() => void remove(h.id)}
                            >
                              {deleting === h.id ? <IconSpinner size={13} /> : null}
                              {deleting === h.id ? 'Deleting…' : 'Delete'}
                            </button>
                            <button
                              type="button"
                              className="btn-sm"
                              onClick={() => setConfirming(null)}
                              disabled={deleting === h.id}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <AudioPlayer
                          compact
                          src={mediaUrl(h.audio_url)}
                          label={h.input_text.slice(0, 40)}
                          downloadName={`${h.profile_name ?? 'voice'}-${h.id}.${h.output_format}`}
                        />
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}

          <div className="hist-foot">
            <span className="muted">
              Showing {showing} of {total}
            </span>
            {hasMore && !query && filter === 'all' && (
              <button type="button" className="btn-sm" onClick={onLoadMore} disabled={loading}>
                {loading ? <IconSpinner size={13} /> : null}
                {loading ? 'Loading…' : 'Load more'}
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-mark" aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
      </div>
      <p className="empty-title">{title}</p>
      <p className="empty-body">{body}</p>
      {action}
    </div>
  );
}

/** Script drives direction, never the language code — Roman Urdu is LTR. */
function isRtl(script: string): boolean {
  return script === 'arabic';
}
