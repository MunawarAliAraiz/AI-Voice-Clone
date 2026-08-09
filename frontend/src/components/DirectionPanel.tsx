/**
 * Speech Direction preview — read-only. Renders `POST /api/direction/analyze`'s
 * response: the capability report (which fields the routed model honors,
 * approximates, or ignores — the whole point of this panel), the plan's
 * dominant emotion/intensity/rate, and, behind its own disclosure, the
 * per-segment breakdown.
 *
 * This component never fails loudly — loading/error states render a small
 * muted note, never throw. The caller (Composer) owns fetching and debouncing;
 * this component only renders what it's given.
 */
import { useState } from 'react';
import type { DirectionAnalyzeResponse } from '../types/api';
import { IconChevronDown, IconChevronUp } from './icons';

interface Props {
  data: DirectionAnalyzeResponse | null;
  loading: boolean;
  error: string | null;
  /** Optional apply toggle. Omit both to keep this panel purely read-only
   *  (e.g. a future standalone preview with no generate action attached). */
  applyDirection?: boolean;
  onApplyDirectionChange?: (value: boolean) => void;
}

// Honored first, ignored last — the ordering that makes "this model honors
// rate, ignores emotion" readable at a glance.
const SUPPORT_ORDER: Record<string, number> = { honored: 0, approximated: 1, ignored: 2 };

export function DirectionPanel({
  data,
  loading,
  error,
  applyDirection,
  onApplyDirectionChange,
}: Props) {
  const [showSegments, setShowSegments] = useState(false);

  if (loading) return <p className="hint">Analyzing direction…</p>;
  if (error) return <p className="hint muted">{error}</p>;
  if (!data) return null;

  const { plan, capability } = data;
  const fields = [...capability.fields].sort(
    (a, b) => (SUPPORT_ORDER[a.support] ?? 99) - (SUPPORT_ORDER[b.support] ?? 99)
  );
  const rtl = plan.source_script === 'arabic';

  return (
    <div className="direction-panel">
      <p className="v-meta">
        <span>{capability.model_display_name}</span>
        <span className="dot" />
        <span>
          {plan.summary.emotion} · {plan.summary.intensity} intensity · {plan.summary.rate} rate
        </span>
      </p>

      <div className="cap-row">
        {fields.map((f) => (
          <span key={f.field} className={`tag cap-${f.support}`} title={f.rationale}>
            {f.field} · {f.support}
          </span>
        ))}
      </div>

      {onApplyDirectionChange && plan.segments.length > 0 && (
        <label className="consent" style={{ marginTop: 'var(--space-2)' }}>
          <input
            type="checkbox"
            checked={applyDirection ?? false}
            onChange={(e) => onApplyDirectionChange(e.target.checked)}
          />
          <span>
            Apply this direction — render {plan.segments.length} segment
            {plan.segments.length === 1 ? '' : 's'} separately with the pacing above, joined with
            real pauses. Fields marked "ignored" above still won't take effect.
          </span>
        </label>
      )}

      {plan.segments.length > 0 && (
        <>
          <button
            type="button"
            className="disclosure-btn"
            onClick={() => setShowSegments((s) => !s)}
            aria-expanded={showSegments}
          >
            {showSegments ? <IconChevronUp size={12} /> : <IconChevronDown size={12} />}
            <span>{showSegments ? 'Hide segments' : `Segments (${plan.segments.length})`}</span>
          </button>

          {showSegments && (
            <ul className="segment-list">
              {plan.segments.map((seg) => (
                <li key={seg.index} className="segment-row">
                  <p className="segment-text" dir={rtl ? 'rtl' : 'ltr'}>
                    {seg.text}
                  </p>
                  <div className="v-meta">
                    <span className="tag">{seg.emotion}</span>
                    <span className="tag">{seg.intensity}</span>
                    <span className="tag">{seg.rate}</span>
                    {seg.pause_after_ms > 0 && (
                      <>
                        <span className="dot" />
                        <span>{seg.pause_after_ms}ms pause</span>
                      </>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
