/**
 * The Convert tab: paste a script, convert its writing system, hand pieces to
 * the editor.
 *
 * WHAT THIS IS FOR
 * ----------------
 * You have a script in Hindi (Devanagari), Roman Urdu, or Urdu script, and you
 * want it in a form an Urdu voice can speak well. Devanagari cannot be spoken at
 * all; Roman Urdu is routable but a native listener hears an English accent
 * (finding A0), so converting it to Urdu script is usually what you want. This
 * splits the paste into review-sized parts and converts them through the Gemma
 * transliterator — never silently, because its failure mode is a real word that
 * means something else.
 *
 * WHY THE SOURCE IS DETECTED BUT NOT THE LANGUAGE
 * -----------------------------------------------
 * The server detects the SCRIPT (`profile_text`) — that is a fact about the
 * characters. It does NOT guess whether Latin text is Roman Urdu or English;
 * they are indistinguishable, and this codebase refuses that guess everywhere.
 * (English→Urdu is TRANSLATION, a different operation, and a planned follow-up.)
 */
import { useEffect, useState } from 'react';
import { api, ApiError } from '../services/api';
import type { PreparedTextResponse } from '../types/api';
import { IconAlert, IconCopy, IconCheck, IconSpinner, IconSpark } from './icons';
import { useScriptConversion } from '../hooks/useScriptConversion';
import { useTranscriptParts } from '../hooks/useTranscriptParts';
import { TranscriptPartRow } from './TranscriptPartRow';
import { useSystemStatus } from '../hooks/queries';

type Target = 'roman' | 'perso_arabic';

/** Mirrors `MAX_BATCH_CHUNKS` in `backend/app/domain/transliterate.py`, which
 *  `schemas/text.py` enforces. Checked here so an oversized selection is
 *  refused with a reason rather than sent and 422'd. */
const MAX_BATCH_CHUNKS = 200;

/** The conversions the server actually supports for a given detected script —
 *  `latin → roman` and `arabic → perso_arabic` are no-ops and absent, and
 *  nothing converts INTO Devanagari (it is a source format only). Mirrors
 *  `_CONVERSIONS` / `DEFAULT_TARGETS` in `backend/app/domain/transliterate.py`. */
const TARGETS_FOR: Record<string, Target[]> = {
  devanagari: ['roman', 'perso_arabic'],
  latin: ['perso_arabic'],
  arabic: ['roman'],
};

const TARGET_LABEL: Record<Target, { title: string; hint: string }> = {
  roman: { title: 'Roman Urdu', hint: 'easier to read and fix before generating' },
  perso_arabic: { title: 'Urdu script', hint: 'ready to generate, harder to proofread' },
};

interface Props {
  /** Puts text into the Composer and switches to it. */
  onSendToEditor: (text: string) => void;
}

export function TranscriptPanel({ onSendToEditor }: Props) {
  const [input, setInput] = useState('');
  const [data, setData] = useState<PreparedTextResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [target, setTarget] = useState<Target>('roman');
  const conversion = useScriptConversion();
  const system = useSystemStatus();
  //: Which CHUNK indexes the running conversion covers, in submission order. A
  //: result item's `index` is its position IN THE BATCH, not in the paste;
  //: without this map, converting part 8 alone (batch index 0) would land on
  //: part 1 — a silent wrong-answer bug, both being plausible Urdu.
  const [batchIndexes, setBatchIndexes] = useState<number[]>([]);
  //: The target the RUNNING conversion used — not always the picker's current
  //: value, since a per-part button chooses its own — so the summary can name
  //: what actually happened.
  const [lastTarget, setLastTarget] = useState<Target>('roman');
  //: Which part a single-part conversion is for, so its own row shows the
  //: progress instead of the panel-level control claiming to be busy.
  const [busyIndex, setBusyIndex] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  // ONE record per part — see useTranscriptParts for why status/outgoing are
  // derived rather than stored.
  const parts = useTranscriptParts();

  const canConvert = system.data?.script_conversion?.available ?? null;
  const cannotConvertReason = system.data?.script_conversion?.reason ?? null;

  // Devanagari cannot be spoken at all (a blocker); Latin and Arabic are
  // routable, so converting them is an OFFER. Every supported source has at
  // least one target, so the panel shows whenever there is data to convert.
  const needsConversion = data?.needs_transliteration ?? false;
  const validTargets = data ? (TARGETS_FOR[data.script] ?? []) : [];
  const offerConversion = validTargets.length > 0;

  // Default the picker to the source's first valid target the moment a paste is
  // prepared — Devanagari to Roman (readable), Roman Urdu to Urdu script.
  useEffect(() => {
    if (validTargets.length && !validTargets.includes(target)) {
      setTarget(validTargets[0]!);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.script]);

  useEffect(() => {
    if (!conversion.result) return;
    parts.applyConversion(batchIndexes, conversion.ok, conversion.rejected, lastTarget);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversion.result, conversion.ok, conversion.rejected, batchIndexes]);

  const toggleExpanded = (index: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (!next.delete(index)) next.add(index);
      return next;
    });

  const toggleSelected = (index: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (!next.delete(index)) next.add(index);
      return next;
    });

  //: A rejected part carries no text and a Devanagari part that was never
  //: converted cannot be spoken, so neither is sendable. Counted rather than
  //: silently dropped, so the button says how many will actually go.
  const sendableCount = (indexes: number[]) =>
    indexes.filter((i) => {
      const status = parts.status(i);
      if (status === 'rejected') return false;
      return !needsConversion || status === 'converted' || status === 'edited';
    }).length;

  //: Joined in PASTE order regardless of tick order, blank line between parts —
  //: `direction_analyze` reads that as the longest pause, which is what a jump
  //: between parts should sound like.
  const joinParts = (indexes: number[]) =>
    [...indexes]
      .sort((a, b) => a - b)
      .filter((i) => parts.status(i) !== 'rejected')
      .map((i) => parts.outgoing(i))
      .join('\n\n');

  const startConversion = (indexes: number[], to: Target = target) => {
    if (!data) return;
    // Filtered, not asserted: an index with no chunk would send `undefined` as
    // a chunk and desync the batch positions from `batchIndexes` — the exact
    // misalignment this mechanism exists to prevent.
    const present = indexes.filter((i) => data.chunks[i] !== undefined);
    if (!present.length) return;
    setBatchIndexes(present);
    setBusyIndex(present.length === 1 ? present[0]! : null);
    setLastTarget(to);
    conversion.start(
      present.map((i) => data.chunks[i]!.text),
      to,
    );
  };

  async function prepare(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim()) return;
    setLoading(true);
    setError(null);
    try {
      conversion.reset();
      setBatchIndexes([]);
      setExpanded(new Set());
      setSelected(new Set());
      setSelectMode(false);
      const prepared = await api.prepareText(input.trim());
      parts.reset(prepared.chunks);
      setData(prepared);
    } catch (err) {
      setData(null);
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function copy(text: string, key: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      setError('Could not copy — your browser blocked clipboard access.');
    }
  }

  return (
    <section className="card" aria-labelledby="cv-h">
      <header className="card-head">
        <h2 id="cv-h">Convert a script</h2>
      </header>

      <p className="hint">
        Paste a script in Hindi (Devanagari), Roman Urdu, or Urdu script. It's split into parts you
        can convert between writing systems and send to the editor. Read every part before you
        generate — a conversion can turn a word into a real word that means something else.
      </p>

      <form className="transcript-form" onSubmit={prepare}>
        <label className="field" style={{ flex: 1 }}>
          <span className="field-label">Your script</span>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Paste Hindi, Roman Urdu, or Urdu-script text here…"
            dir="auto"
            rows={6}
            required
          />
        </label>
      </form>
      <div className="transcript-actions">
        <button
          type="button"
          className="btn"
          disabled={loading || !input.trim()}
          onClick={(e) => void prepare(e)}
        >
          {loading ? <IconSpinner size={14} /> : <IconSpark size={14} />}
          {loading ? 'Loading…' : data ? 'Reload parts' : 'Load parts'}
        </button>
        {input.trim() && (
          <span className="muted">{input.trim().length.toLocaleString()} characters</span>
        )}
      </div>

      {error && (
        <div className="inline-error" role="alert">
          <IconAlert size={14} /> {error}
        </div>
      )}

      {data && (
        <>
          {offerConversion && (
            <div className="convert-panel">
              <div className="convert-why">
                <IconAlert size={14} />
                <span>
                  {data.script === 'devanagari' ? (
                    <>This is Hindi (Devanagari), which no voice here can read. Convert it before you
                    generate from it.</>
                  ) : data.script === 'latin' ? (
                    <>This is Roman Urdu. Convert it to Urdu script so an Urdu voice reads it
                    properly — Roman Urdu can come out sounding accented.</>
                  ) : (
                    <>This is already Urdu script and can be generated as-is. Convert it to Roman Urdu
                    if you would rather read and edit it that way.</>
                  )}
                </span>
              </div>

              {/* Only the targets this source can actually reach. A single valid
                  target still renders as one option so the choice is legible. */}
              <fieldset className="convert-target" disabled={conversion.running}>
                <legend className="field-label">Convert to</legend>
                {validTargets.map((value) => (
                  <label key={value} className="convert-option">
                    <input
                      type="radio"
                      name="convert-target"
                      value={value}
                      checked={target === value}
                      onChange={() => setTarget(value)}
                    />
                    <span>
                      <strong>{TARGET_LABEL[value].title}</strong>
                      <em>{TARGET_LABEL[value].hint}</em>
                    </span>
                  </label>
                ))}
              </fieldset>

              <div className="convert-actions">
                <button
                  type="button"
                  className="btn"
                  disabled={conversion.running || canConvert === false || canConvert === null}
                  onClick={() => startConversion(data.chunks.map((_, i) => i))}
                  title={canConvert === false ? (cannotConvertReason ?? '') : undefined}
                >
                  {conversion.running && busyIndex === null ? <IconSpinner size={14} /> : null}
                  {conversion.running && busyIndex === null
                    ? conversion.progressLabel
                    : `Convert all ${data.chunks.length} parts`}
                </button>
                <span className="muted">
                  {conversion.running
                    ? 'All parts convert in one pass — the model loads at most once.'
                    : /* The honest number up front — MIRRORS app/jobs/estimate.py's
                         two-term model; re-solve there, re-solve here. */
                      `About ${Math.max(
                        1,
                        Math.round((data.chunks.length * 7.03 + data.text.length * 0.0202) / 60),
                      )} min for all ${data.chunks.length} — one pass, one model load.`}
                </span>
              </div>

              {canConvert === false && (
                <div className="inline-error" role="status">
                  <IconAlert size={14} />
                  <span>{cannotConvertReason ?? 'Script conversion is not available here.'}</span>
                </div>
              )}
              {conversion.error && (
                <div className="inline-error" role="alert">
                  <IconAlert size={14} /> {conversion.error}
                </div>
              )}
              {parts.convertedCount + parts.rejectedCount > 0 && !conversion.running && (
                <div className="convert-summary" role="status">
                  {parts.convertedCount} of {data.chunks.length} parts converted to{' '}
                  {lastTarget === 'roman' ? 'Roman Urdu' : 'Urdu script'}
                  {parts.rejectedCount > 0 && (
                    <>
                      {' '}— <strong>{parts.rejectedCount} could not be converted</strong> and are
                      marked below; use each one's Convert button to retry
                    </>
                  )}
                  . Read every part before you generate from it.
                </div>
              )}
            </div>
          )}

          {/* SELECTION + ONE ACTION BAR — reuses HistoryPanel's `.bulkbar` shape
              and CSS verbatim, including the indeterminate select-all. */}
          {data.chunks.length > 1 && (
            <div className="transcript-actions">
              <button
                type="button"
                className={`btn-sm ${selectMode ? 'on' : ''}`}
                aria-pressed={selectMode}
                onClick={() => {
                  setSelectMode((on) => !on);
                  setSelected(new Set());
                }}
              >
                {selectMode ? 'Done' : 'Select parts'}
              </button>
            </div>
          )}

          {selectMode && (
            <div className="bulkbar" role="toolbar" aria-label="Bulk actions on parts">
              <label className="bulkbar-all">
                <input
                  type="checkbox"
                  checked={selected.size > 0 && selected.size === data.chunks.length}
                  ref={(el) => {
                    if (el) {
                      el.indeterminate = selected.size > 0 && selected.size < data.chunks.length;
                    }
                  }}
                  onChange={() =>
                    setSelected(
                      selected.size === data.chunks.length
                        ? new Set()
                        : new Set(data.chunks.map((c) => c.index)),
                    )
                  }
                  aria-label="Select all parts"
                />
                {selected.size > 0 ? `${selected.size} selected` : 'Select all'}
              </label>

              {selected.size > 0 && (
                <>
                  {offerConversion && canConvert && (
                    <>
                      {/* ONE call for the whole selection against a single model
                          residency — looping would pay N cold loads. */}
                      {validTargets.map((t) => (
                        <button
                          key={t}
                          type="button"
                          className="btn-sm"
                          disabled={conversion.running || selected.size > MAX_BATCH_CHUNKS}
                          onClick={() => startConversion([...selected].sort((a, b) => a - b), t)}
                        >
                          Convert {selected.size} → {TARGET_LABEL[t].title}
                        </button>
                      ))}
                    </>
                  )}
                  <button
                    type="button"
                    className="btn-sm"
                    onClick={() => onSendToEditor(joinParts([...selected]))}
                    disabled={!sendableCount([...selected])}
                    title={
                      sendableCount([...selected])
                        ? 'Put the selected parts in the editor, in order'
                        : 'None of the selected parts can be generated yet'
                    }
                  >
                    Send {sendableCount([...selected])} to editor
                  </button>
                  <button
                    type="button"
                    className="btn-sm ghost"
                    onClick={() => void copy(joinParts([...selected]), 'sel')}
                  >
                    {copied === 'sel' ? <IconCheck size={13} /> : <IconCopy size={13} />}
                    {copied === 'sel' ? 'Copied' : 'Copy'}
                  </button>
                  {selected.size > MAX_BATCH_CHUNKS && (
                    <span className="muted">
                      Convert handles {MAX_BATCH_CHUNKS} parts at a time — deselect{' '}
                      {selected.size - MAX_BATCH_CHUNKS}.
                    </span>
                  )}
                  {conversion.running && busyIndex === null && (
                    <span className="muted chunk-progress">
                      <IconSpinner size={13} /> {conversion.progressLabel}
                    </span>
                  )}
                </>
              )}
            </div>
          )}

          <div className="transcript-actions">
            <button
              type="button"
              className="btn-sm"
              onClick={() =>
                onSendToEditor(
                  // Whatever is actually usable: converted parts if a conversion
                  // has run, the original otherwise. Rejected parts are LEFT OUT
                  // rather than passed through in a script nothing can speak.
                  parts.convertedCount
                    ? data.chunks
                        .map((c) =>
                          parts.status(c.index) === 'rejected'
                            ? undefined
                            : parts.outgoing(c.index),
                        )
                        .filter((t): t is string => Boolean(t))
                        .join('\n\n')
                    : data.text,
                )
              }
              disabled={needsConversion && parts.convertedCount === 0}
              title={
                needsConversion && parts.convertedCount === 0
                  ? 'Devanagari cannot be generated — convert it first'
                  : 'Put the whole script in the editor'
              }
            >
              Send all to editor
            </button>
            <button type="button" className="btn-sm" onClick={() => void copy(data.text, 'all')}>
              {copied === 'all' ? <IconCheck size={13} /> : <IconCopy size={13} />}
              {copied === 'all' ? 'Copied' : 'Copy all'}
            </button>
            <span className="muted">
              {data.text.length.toLocaleString()} characters · {data.chunks.length} parts
            </span>
          </div>

          <h3 className="transcript-h3">Parts</h3>
          <ul className="parts">
            {data.chunks.map((chunk) => (
              <TranscriptPartRow
                key={chunk.index}
                chunk={chunk}
                parts={parts}
                expanded={expanded.has(chunk.index)}
                onToggle={() => toggleExpanded(chunk.index)}
                selected={selected.has(chunk.index)}
                selectMode={selectMode}
                onSelect={() => toggleSelected(chunk.index)}
                onConvert={
                  offerConversion && canConvert
                    ? (to) => startConversion([chunk.index], to)
                    : undefined
                }
                targets={validTargets}
                converting={conversion.running && busyIndex === chunk.index}
                convertingLabel={conversion.progressLabel}
                onSendToEditor={onSendToEditor}
                onCopy={(text, key) => void copy(text, key)}
                copied={copied === `c${chunk.index}`}
                requiresConversion={needsConversion}
              />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
