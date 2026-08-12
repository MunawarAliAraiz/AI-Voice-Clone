/**
 * Speech Direction preview. Renders `POST /api/direction/analyze`'s response:
 * the capability report (which fields the routed model honors, approximates,
 * or ignores — the whole point of this panel), the plan's dominant
 * emotion/intensity/rate, and, behind an "Advanced" disclosure, a per-segment
 * editor.
 *
 * Simple mode (what's always visible once the caller's own "Direction
 * (preview)" disclosure is open) is read-only: the chip, the summary, and the
 * apply checkbox. Advanced mode — segmentation, per-segment prosody — is the
 * "Advanced tab for pros" ROADMAP.md describes: editable selects for
 * emotion/intensity/energy/rate and a numeric pause_after_ms, never the
 * segment's text. There is deliberately no Tone control here: no current
 * runtime honors it, so it would be a confirmed no-op slider — the exact
 * pattern this project already deleted "Emotion"/"Style Exaggeration" for.
 *
 * This component never fails loudly — loading/error states render a small
 * muted note, never throw. The caller (Composer) owns fetching, debouncing,
 * AND the edit values themselves (`edits`) — this component only renders
 * what it's given and reports full, merged `DirectedSegmentIn` objects back
 * up via `onEditSegment`, never partial patches, so the caller never has to
 * know this component's internal field layout to build the sparse
 * `direction_plan` it sends to `POST /api/generate`.
 */
import { useState } from 'react';
import type { DirectedSegmentIn, DirectionAnalyzeResponse } from '../types/api';
import { IconChevronDown, IconChevronUp, IconReset, IconSpark, IconSpinner } from './icons';

interface Props {
  data: DirectionAnalyzeResponse | null;
  loading: boolean;
  error: string | null;
  /** Optional apply toggle. Omit both to keep this panel purely read-only
   *  (e.g. a future standalone preview with no generate action attached). */
  applyDirection?: boolean;
  onApplyDirectionChange?: (value: boolean) => void;
  /** Advanced per-segment overrides, keyed by segment index. Omit (along with
   *  onEditSegment) to render the segment list read-only. */
  edits?: Record<number, DirectedSegmentIn>;
  /** Fired with the FULL merged segment (all five prosody fields, never just
   *  the one that changed) — this component resolves "current value" itself
   *  from `edits` falling back to the analyzed default, so the caller can
   *  just overwrite `edits[segment.index]` with what it's given. */
  onEditSegment?: (segment: DirectedSegmentIn) => void;
  onResetSegment?: (index: number) => void;
  onResetAllEdits?: () => void;
  /**
   * "Let AI suggest emotion/tone" — enqueues the Qwen LLM analyzer job. Omit
   * (along with the two props below) to hide the trigger entirely, e.g. a
   * read-only render of this panel. This is a bulk action that pre-fills
   * `edits` for every segment at once via `onEditSegment` — it does not
   * bypass the manual editor, the LLM's output just lands there like any
   * other edit, still reviewable/adjustable/resettable.
   */
  onSuggestAi?: () => void;
  aiSuggestLoading?: boolean;
  aiSuggestError?: string | null;
}

// Honored first, ignored last — the ordering that makes "this model honors
// rate, ignores emotion" readable at a glance.
const SUPPORT_ORDER: Record<string, number> = { honored: 0, approximated: 1, ignored: 2 };

const EMOTIONS = [
  'neutral', 'happy', 'sad', 'anxious', 'angry', 'excited', 'calm', 'serious', 'questioning',
] as const;
const LEVELS = ['low', 'medium', 'high'] as const;
const RATES = ['slow', 'normal', 'fast'] as const;

export function DirectionPanel({
  data,
  loading,
  error,
  applyDirection,
  onApplyDirectionChange,
  edits,
  onEditSegment,
  onResetSegment,
  onResetAllEdits,
  onSuggestAi,
  aiSuggestLoading,
  aiSuggestError,
}: Props) {
  const [showAdvanced, setShowAdvanced] = useState(false);

  if (loading) return <p className="hint">Analyzing direction…</p>;
  if (error) return <p className="hint muted">{error}</p>;
  if (!data) return null;

  const { plan, capability } = data;
  const fields = [...capability.fields].sort(
    (a, b) => (SUPPORT_ORDER[a.support] ?? 99) - (SUPPORT_ORDER[b.support] ?? 99)
  );
  const rtl = plan.source_script === 'arabic';
  const editable = Boolean(onEditSegment);
  const editedCount = edits ? Object.keys(edits).length : 0;

  function effectiveSegment(seg: (typeof plan.segments)[number]): DirectedSegmentIn {
    return (
      edits?.[seg.index] ?? {
        index: seg.index,
        emotion: seg.emotion,
        intensity: seg.intensity,
        energy: seg.energy,
        rate: seg.rate,
        pause_after_ms: seg.pause_after_ms,
      }
    );
  }

  function handleFieldChange(
    seg: (typeof plan.segments)[number],
    patch: Partial<Omit<DirectedSegmentIn, 'index'>>
  ) {
    onEditSegment?.({ ...effectiveSegment(seg), ...patch, index: seg.index });
  }

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
          <div className="disclosure-row">
            <button
              type="button"
              className="disclosure-btn"
              onClick={() => setShowAdvanced((s) => !s)}
              aria-expanded={showAdvanced}
            >
              {showAdvanced ? <IconChevronUp size={12} /> : <IconChevronDown size={12} />}
              <span>
                {editable ? 'Advanced' : 'Segments'} ({plan.segments.length})
                {editedCount > 0 && ` · ${editedCount} edited`}
              </span>
            </button>
            {showAdvanced && editable && onSuggestAi && (
              <button
                type="button"
                className="btn-sm on"
                onClick={onSuggestAi}
                disabled={aiSuggestLoading}
                aria-busy={aiSuggestLoading}
                title="Classify emotion/intensity/energy/rate for every segment with the Qwen LLM analyzer, then review/adjust below before generating."
              >
                {aiSuggestLoading ? <IconSpinner size={12} /> : <IconSpark size={12} />}
                {aiSuggestLoading ? 'Asking AI…' : 'Let AI suggest emotion/tone'}
              </button>
            )}
            {showAdvanced && editable && editedCount > 0 && (
              <button type="button" className="btn-sm" onClick={onResetAllEdits}>
                <IconReset size={12} /> Reset all
              </button>
            )}
          </div>

          {showAdvanced && aiSuggestError && <p className="hint muted">{aiSuggestError}</p>}

          {showAdvanced && (
            <ul className="segment-list">
              {plan.segments.map((seg) => {
                const isEdited = Boolean(edits?.[seg.index]);
                const eff = effectiveSegment(seg);
                return (
                  <li key={seg.index} className="segment-row">
                    <p className="segment-text" dir={rtl ? 'rtl' : 'ltr'}>
                      {seg.text}
                    </p>

                    {editable ? (
                      <div className="segment-edit">
                        <div className="row segment-edit-grid">
                          <label className="field">
                            <span className="field-label">Emotion</span>
                            <div className="select-wrap">
                              <select
                                value={eff.emotion}
                                onChange={(e) => handleFieldChange(seg, { emotion: e.target.value })}
                              >
                                {EMOTIONS.map((v) => (
                                  <option key={v} value={v}>
                                    {v}
                                  </option>
                                ))}
                              </select>
                            </div>
                          </label>
                          <label className="field">
                            <span className="field-label">Intensity</span>
                            <div className="select-wrap">
                              <select
                                value={eff.intensity}
                                onChange={(e) => handleFieldChange(seg, { intensity: e.target.value })}
                              >
                                {LEVELS.map((v) => (
                                  <option key={v} value={v}>
                                    {v}
                                  </option>
                                ))}
                              </select>
                            </div>
                          </label>
                          <label className="field">
                            <span className="field-label">Energy</span>
                            <div className="select-wrap">
                              <select
                                value={eff.energy}
                                onChange={(e) => handleFieldChange(seg, { energy: e.target.value })}
                              >
                                {LEVELS.map((v) => (
                                  <option key={v} value={v}>
                                    {v}
                                  </option>
                                ))}
                              </select>
                            </div>
                          </label>
                          <label className="field">
                            <span className="field-label">Rate</span>
                            <div className="select-wrap">
                              <select
                                value={eff.rate}
                                onChange={(e) => handleFieldChange(seg, { rate: e.target.value })}
                              >
                                {RATES.map((v) => (
                                  <option key={v} value={v}>
                                    {v}
                                  </option>
                                ))}
                              </select>
                            </div>
                          </label>
                          <label className="field">
                            <span className="field-label">Pause after (ms)</span>
                            <input
                              type="number"
                              min={0}
                              max={5000}
                              step={50}
                              value={eff.pause_after_ms}
                              onChange={(e) => {
                                const raw = Number(e.target.value);
                                const clamped = Number.isFinite(raw)
                                  ? Math.min(5000, Math.max(0, Math.round(raw)))
                                  : 0;
                                handleFieldChange(seg, { pause_after_ms: clamped });
                              }}
                            />
                          </label>
                        </div>
                        {isEdited && (
                          <button
                            type="button"
                            className="btn-sm"
                            onClick={() => onResetSegment?.(seg.index)}
                          >
                            <IconReset size={12} /> Reset to detected
                          </button>
                        )}
                      </div>
                    ) : (
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
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
