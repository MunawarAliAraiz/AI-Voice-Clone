/**
 * Import a YouTube transcript, and hand pieces of it to the editor.
 *
 * WHY THE TRANSCRIPT IS EDITABLE AND NOT SYNTHESIZED DIRECTLY
 * ------------------------------------------------------------
 * Auto-generated captions — especially Urdu and Hindi ones — are a rough
 * draft. They drop punctuation, mishear names, and run sentences together.
 * Sending one straight to a model would present a machine's guess as the
 * user's words, so everything here ends at "put this in the editor", never at
 * "generate this".
 *
 * WHY CHUNKS ARE A LIST YOU WORK THROUGH
 * ---------------------------------------
 * A one-hour video is tens of thousands of characters and every model here has
 * a frame limit. The server splits it with `chunk_for_synthesis`, which packs
 * whole sentences and only falls back to a clause or word boundary when a
 * sentence will not fit — chunks that were cut that way are BADGED, because
 * that is exactly where a join artifact becomes audible.
 */
import { useEffect, useState } from 'react';
import { api, ApiError } from '../services/api';
import type { TranscriptResponse } from '../types/api';
import { IconAlert, IconCopy, IconCheck, IconSearch, IconSpinner } from './icons';
import { fmtDuration } from '../lib/format';
import { useScriptConversion } from '../hooks/useScriptConversion';
import { useTranscriptParts } from '../hooks/useTranscriptParts';
import { TranscriptPartRow } from './TranscriptPartRow';
import { useSystemStatus } from '../hooks/queries';

type Target = 'roman' | 'perso_arabic';

interface Props {
  /** Puts text into the Composer and switches to it. */
  onSendToEditor: (text: string) => void;
}

export function TranscriptPanel({ onSendToEditor }: Props) {
  const [url, setUrl] = useState('');
  const [language, setLanguage] = useState('');
  const [data, setData] = useState<TranscriptResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  // Roman by default. A caption YouTube's ASR guessed at is a DRAFT, and the
  // whole reason to convert a transcript is to read and fix it — going
  // straight to Urdu script skips the step this feature exists for. The choice
  // stays the user's, because a caption good enough to use unedited should not
  // have to detour through a second conversion.
  const [target, setTarget] = useState<Target>('roman');
  const conversion = useScriptConversion();
  const system = useSystemStatus();
  //: Which CHUNK indexes the running conversion covers, in submission order.
  //:
  //: Required because a result item's `index` is its position IN THE BATCH,
  //: not in the transcript. Converting part 8 on its own returns index 0, and
  //: without this map that result would be written onto part 1 — a silent
  //: wrong-answer bug, since both are plausible Roman Urdu and nothing would
  //: look broken.
  const [batchIndexes, setBatchIndexes] = useState<number[]>([]);
  //: The target the RUNNING conversion used, which is not always the picker's
  //: current value — a per-part button chooses its own, and the summary below
  //: must name what actually happened rather than what is selected now.
  const [lastTarget, setLastTarget] = useState<Target>('roman');
  //: Which parts are open. Controlled rather than native <details> because the
  //: chapter jump list needs to open one programmatically, and nested native
  //: disclosures fight that.
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());

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
  //: Which part a single-part conversion is for, so its own row can show the
  //: progress instead of the panel-level control claiming to be busy.
  const [busyIndex, setBusyIndex] = useState<number | null>(null);

  const startConversion = (indexes: number[], to: Target = target) => {
    if (!data) return;
    // Filtered, not asserted: an index with no chunk would otherwise send
    // `undefined` to the server as a chunk, and the batch positions would then
    // no longer line up with `batchIndexes` — which is exactly the misalignment
    // this whole mechanism exists to prevent.
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

  // `null` while loading rather than `true`: offering a feature and then
  // failing is worse than a control that appears a moment late.
  // `script_conversion?` and not just `data?` — the frontend and backend deploy
  // independently (a local UI against a pod), so a server that predates this
  // field is a real case. Without the second `?` it throws and takes the panel
  // down rather than merely hiding a button.
  const canConvert = system.data?.script_conversion?.available ?? null;
  const cannotConvertReason = system.data?.script_conversion?.reason ?? null;

  // Devanagari cannot be spoken at all; Urdu script can, but is harder to
  // proofread. One is a blocker and the other is an offer — the copy says so.
  const needsConversion = data?.needs_transliteration ?? false;
  const offerConversion = data != null && (needsConversion || data.script === 'arabic');

  // ONE record per part, replacing the `converted` / `rejectedIndexes` /
  // `busyIndex` maps that were drifting toward four parallel structures keyed
  // by the same index. See useTranscriptParts for why, and for why `status`
  // and `outgoing` are derived rather than stored.
  const parts = useTranscriptParts();

  useEffect(() => {
    if (!conversion.result) return;
    parts.applyConversion(batchIndexes, conversion.ok, conversion.rejected, lastTarget);
    // `conversion.ok` is a fresh array per result, so this fires once per job.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversion.result, conversion.ok, conversion.rejected, batchIndexes]);

  async function fetchTranscript(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      conversion.reset();
      setBatchIndexes([]);
      setExpanded(new Set());
      setSelected(new Set());
      setSelectMode(false);
      const fetched = await api.fetchTranscript(url.trim(), language || undefined);
      parts.reset(fetched.chunks);
      setData(fetched);
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
    <section className="card" aria-labelledby="tr-h">
      <header className="card-head">
        <h2 id="tr-h">Import from YouTube</h2>
      </header>

      <p className="hint">
        Fetches the video's own caption track. Auto-generated captions are a rough draft —
        read it before you generate from it.
      </p>

      <form className="transcript-form" onSubmit={fetchTranscript}>
        <label className="field">
          <span className="field-label">Video URL</span>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.youtube.com/watch?v=…"
            inputMode="url"
            required
          />
        </label>
        <label className="field transcript-lang">
          <span className="field-label">Caption language</span>
          <input
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            placeholder="auto"
            maxLength={16}
            aria-label="Preferred caption language code"
          />
        </label>
        <button type="submit" className="btn" disabled={loading || !url.trim()}>
          {loading ? <IconSpinner size={14} /> : <IconSearch size={14} />}
          {loading ? 'Fetching…' : 'Fetch'}
        </button>
      </form>

      {error && (
        <div className="inline-error" role="alert">
          <IconAlert size={14} /> {error}
        </div>
      )}

      {data && (
        <>
          <div className="transcript-meta">
            {data.title && <strong>{data.title}</strong>}
            {data.duration_sec != null && (
              <>
                <span className="dot" />
                <span>{fmtDuration(data.duration_sec)}</span>
              </>
            )}
            <span className="dot" />
            <span className="route-chip">
              {data.chosen_track.language}
              {data.chosen_track.is_auto_generated ? ' (auto)' : ''}
            </span>
            {/* Auto captions are materially worse in these languages, and
                saying so is more useful than a quality score nobody can act on. */}
            {data.chosen_track.is_auto_generated && (
              <span className="tag warn">machine-written — expect errors</span>
            )}
          </div>

          {/* NOT a warning about a failure — a statement about what is
              possible. No model here renders Devanagari (routing rejects it
              deliberately), so this text needs converting before it can be
              spoken at all. Decided server-side; the UI just reports it. */}
          {offerConversion && (
            <div className="convert-panel">
              <div className="convert-why">
                <IconAlert size={14} />
                <span>
                  {needsConversion ? (
                    <>
                      This transcript is in Devanagari, which no voice here can read. Convert
                      it before you generate from it.
                    </>
                  ) : (
                    <>
                      This transcript is already in Urdu script and can be generated as-is.
                      Convert it to Roman Urdu if you would rather read and edit it that way.
                    </>
                  )}
                </span>
              </div>

              {/* The choice is between two different INTENTIONS, so the labels
                  name the intention rather than the script. "Urdu script"
                  alone does not tell you it skips the editing step. */}
              <fieldset className="convert-target" disabled={conversion.running}>
                <legend className="field-label">Convert to</legend>
                {(['roman', 'perso_arabic'] as Target[]).map((value) => (
                  <label key={value} className="convert-option">
                    <input
                      type="radio"
                      name="convert-target"
                      value={value}
                      checked={target === value}
                      onChange={() => setTarget(value)}
                    />
                    <span>
                      <strong>{value === 'roman' ? 'Roman Urdu' : 'Urdu script'}</strong>
                      <em>
                        {value === 'roman'
                          ? 'easier to read and fix before generating'
                          : 'ready to generate, harder to proofread'}
                      </em>
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
                {/* One model load for the whole transcript is the entire point
                    of doing this as a batch, and saying so sets the right
                    expectation for a first run that takes minutes. */}
                <span className="muted">
                  {conversion.running
                    ? 'All parts convert in one pass — the model loads at most once.'
                    : /* The honest number, up front — a user who knows it is
                         seven minutes will not read the spinner as a hang.
                         MIRRORS app/jobs/estimate.py's two-term model; if that
                         is re-solved, this must be too. Duplicated rather than
                         fetched because it is needed BEFORE anything is
                         enqueued, and a round-trip to price a button is worse
                         than a constant with a pointer to its source. */
                      `About ${Math.max(
                        1,
                        Math.round((data.chunks.length * 7.03 + data.text.length * 0.0202) / 60),
                      )} min for all ${data.chunks.length} — one pass, one model load.`}
                </span>
              </div>

              {/* The server decided this at startup and composed the sentence.
                  Rendering its words verbatim means the user is told what this
                  card actually needs, not a generic "unavailable". */}
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
              {/* Counts the WHOLE transcript, not the last batch. With
                  per-part conversion the two diverge immediately: converting
                  one part would otherwise report "1 of 1 converted" while 22
                  parts sat untouched. */}
              {parts.convertedCount + parts.rejectedCount > 0 && !conversion.running && (
                <div className="convert-summary" role="status">
                  {parts.convertedCount} of {data.chunks.length} parts converted to{' '}
                  {lastTarget === 'roman' ? 'Roman Urdu' : 'Urdu script'}
                  {parts.rejectedCount > 0 && (
                    <>
                      {' '}— <strong>{parts.rejectedCount} could not be converted</strong> and
                      are marked below; use each one's Convert button to retry
                    </>
                  )}
                  . Read every part before you generate from it.
                </div>
              )}
            </div>
          )}

          <div className="transcript-actions">
            <button
              type="button"
              className="btn-sm"
              onClick={() => void copy(data.text, 'all')}
            >
              {copied === 'all' ? <IconCheck size={13} /> : <IconCopy size={13} />}
              {copied === 'all' ? 'Copied' : 'Copy all'}
            </button>
            <button
              type="button"
              className="btn-sm"
              onClick={() =>
                onSendToEditor(
                  // Whatever is actually usable: the converted parts if a
                  // conversion has run, the original otherwise. Rejected parts
                  // are LEFT OUT rather than silently passed through in a
                  // script nothing can speak.
                  parts.convertedCount
                    ? data.chunks
                        .map((c) =>
                          parts.status(c.index) === 'rejected'
                            ? undefined
                            : parts.outgoing(c.index),
                        )
                        .filter((t): t is string => Boolean(t))
                        // A blank line between parts: `direction_analyze`
                        // treats a newline as the longest pause there is, so
                        // this is a real paragraph break, not formatting.
                        .join('\n\n')
                    : data.text,
                )
              }
              disabled={needsConversion && parts.convertedCount === 0}
              title={
                needsConversion && parts.convertedCount === 0
                  ? 'Devanagari cannot be generated — convert it first'
                  : 'Put the whole transcript in the editor'
              }
            >
              Send all to editor
            </button>
            <span className="muted">
              {data.text.length.toLocaleString()} characters · {data.chunks.length} parts
            </span>
          </div>

          <textarea
            className="transcript-text"
            value={data.text}
            readOnly
            dir="auto"
            rows={8}
            aria-label="Full transcript"
          />

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
                    ? (target) => startConversion([chunk.index], target)
                    : undefined
                }
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
