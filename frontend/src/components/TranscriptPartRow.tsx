/**
 * One part of an imported transcript: a two-line preview that opens into an
 * editor.
 *
 * WHY COLLAPSED BY DEFAULT
 * -------------------------
 * A 15-minute video is 23 parts of ~600 characters. Rendered open, with four
 * buttons each, that was 92 controls and a page nobody could scan — and a
 * two-hour video is six times worse. Collapsed, the list is a table of
 * contents; the actions live inside the one part you opened, which is the only
 * part they can apply to anyway.
 *
 * WHY THE ORIGINAL STAYS ON SCREEN
 * ---------------------------------
 * Three of the four conversions have never passed a listening gate, and the
 * one that has still produces valid Urdu words that mean something else — a
 * whole model was rejected over exactly that. A conversion you cannot compare
 * against its source is a suggestion you can only believe or ignore. Showing
 * both is what makes it checkable.
 *
 * WHAT IS EDITABLE, AND WHAT IS NOT
 * ----------------------------------
 * The caption is READ-ONLY; only the conversion can be edited. This is the
 * distinction `docs/TRANSCRIPT_IMPORT.md` draws when it says the transcript box
 * is read-only and the editable copy lives in the Composer: two editable copies
 * of the same text is how they drift. A conversion under review is not a second
 * copy of the transcript — it is a suggestion being corrected before it leaves.
 */
import { memo } from 'react';
import type { TranscriptChunk } from '../types/api';
import type { PartStatus, TranscriptParts } from '../hooks/useTranscriptParts';
import { IconAlert, IconCheck, IconChevronDown, IconChevronUp, IconCopy, IconSpinner } from './icons';

interface Props {
  chunk: TranscriptChunk;
  parts: TranscriptParts;
  expanded: boolean;
  onToggle: () => void;
  selected: boolean;
  selectMode: boolean;
  onSelect: () => void;
  /** Absent when this server cannot convert — the actions hide rather than
   *  appearing and then failing. */
  onConvert?: (target: 'roman' | 'perso_arabic') => void;
  converting: boolean;
  convertingLabel: string;
  onSendToEditor: (text: string) => void;
  onCopy: (text: string, key: string) => void;
  copied: boolean;
  /** True for Devanagari, which no voice can read: Send stays blocked until
   *  this part has actually been converted. */
  requiresConversion: boolean;
}

const STATUS_LABEL: Record<PartStatus, string> = {
  original: 'Not converted',
  converted: 'Converted',
  edited: 'Edited',
  rejected: 'Could not convert',
};

function TranscriptPartRowImpl({
  chunk, parts, expanded, onToggle, selected, selectMode, onSelect,
  onConvert, converting, convertingLabel, onSendToEditor, onCopy, copied,
  requiresConversion,
}: Props) {
  const part = parts.get(chunk.index);
  const status = parts.status(chunk.index);
  const outgoing = parts.outgoing(chunk.index);
  const hasConversion = part?.converted != null;
  // A rejected part carries NO text by contract, so there is nothing to send.
  const sendBlocked = requiresConversion && status !== 'converted' && status !== 'edited';

  return (
    <li className={`part ${expanded ? 'open' : ''}`}>
      <div className="part-head">
        {selectMode && (
          <input
            type="checkbox"
            checked={selected}
            onChange={onSelect}
            aria-label={`Select part ${chunk.index + 1}`}
          />
        )}
        <button
          type="button"
          className="part-toggle"
          onClick={onToggle}
          aria-expanded={expanded}
        >
          <span className="part-index">{chunk.index + 1}</span>
          <span className={`part-dot ${status}`} title={STATUS_LABEL[status]} />
          <span className="part-status">{STATUS_LABEL[status]}</span>
          {!chunk.ends_on_sentence && (
            <span className="tag warn" title="Cut mid-sentence to fit — the join may be audible">
              cut
            </span>
          )}
          <span className="muted part-chars">{chunk.text.length}</span>
          {expanded ? <IconChevronUp size={13} /> : <IconChevronDown size={13} />}
        </button>
      </div>

      {/* The preview shows what this part WILL hand onward, so a converted or
          edited part previews its own text rather than the caption it came
          from. Two lines, clamped — enough to recognise a part, not enough to
          turn the list back into the wall of text it replaced. */}
      {!expanded && (
        <p className="part-preview" dir="auto">
          {outgoing}
        </p>
      )}

      {expanded && (
        <div className="part-body">
          <div className="part-field">
            <span className="field-label">
              {hasConversion ? 'Original caption' : 'Caption'}
            </span>
            <p className="part-source" dir="auto">
              {chunk.text}
            </p>
          </div>

          {part?.rejection && (
            <p className="chunk-rejected" role="status">
              <IconAlert size={13} /> {part.rejection}
            </p>
          )}

          {hasConversion && (
            <div className="part-field">
              <span className="field-label">
                {part?.target === 'roman' ? 'Roman Urdu' : 'Urdu script'}
                {status === 'edited' && <span className="tag">edited</span>}
              </span>
              {/* EDITABLE. The model's output is a draft; a wrong word here is
                  still a real word, so the person who can spot it needs to be
                  able to fix it without leaving the list. */}
              <textarea
                className="part-edit"
                dir="auto"
                rows={4}
                value={outgoing}
                onChange={(e) => parts.edit(chunk.index, e.target.value)}
                aria-label={`Converted text for part ${chunk.index + 1}`}
              />
            </div>
          )}

          <div className="part-actions">
            {onConvert && (
              <>
                <button
                  type="button"
                  className="btn-sm"
                  onClick={() => onConvert('roman')}
                  disabled={converting}
                  title="Convert only this part to Roman Urdu"
                >
                  → Roman
                </button>
                <button
                  type="button"
                  className="btn-sm"
                  onClick={() => onConvert('perso_arabic')}
                  disabled={converting}
                  title="Convert only this part to Urdu script"
                >
                  → Urdu script
                </button>
              </>
            )}
            {status === 'edited' && (
              <button
                type="button"
                className="btn-sm ghost"
                onClick={() => parts.revert(chunk.index)}
                title="Discard your edit and go back to the model's output"
              >
                Undo edit
              </button>
            )}
            <button
              type="button"
              className="btn-sm"
              onClick={() => onSendToEditor(outgoing)}
              disabled={sendBlocked}
              title={
                sendBlocked
                  ? 'Devanagari cannot be generated — convert this part first'
                  : 'Put this part in the editor'
              }
            >
              Send to editor
            </button>
            <button
              type="button"
              className="btn-sm ghost"
              onClick={() => onCopy(outgoing, `c${chunk.index}`)}
            >
              {copied ? <IconCheck size={13} /> : <IconCopy size={13} />}
              {copied ? 'Copied' : 'Copy'}
            </button>
            {converting && (
              <span className="muted chunk-progress">
                <IconSpinner size={13} /> {convertingLabel}
              </span>
            )}
          </div>
        </div>
      )}
    </li>
  );
}

/**
 * Memoized: a transcript is up to 200 parts and the panel re-renders on every
 * poll of a running conversion. Compared on what the row actually draws —
 * a new `parts` object identity every render would defeat this, so the
 * comparator reads through it rather than comparing it.
 */
export const TranscriptPartRow = memo(TranscriptPartRowImpl, (a, b) =>
  a.chunk === b.chunk &&
  a.expanded === b.expanded &&
  a.selected === b.selected &&
  a.selectMode === b.selectMode &&
  a.converting === b.converting &&
  a.convertingLabel === b.convertingLabel &&
  a.copied === b.copied &&
  a.requiresConversion === b.requiresConversion &&
  Boolean(a.onConvert) === Boolean(b.onConvert) &&
  a.parts.get(a.chunk.index) === b.parts.get(b.chunk.index),
);
