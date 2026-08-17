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
import { useMemo, useState } from 'react';
import { api, ApiError } from '../services/api';
import type { TranscriptResponse } from '../types/api';
import { IconAlert, IconCopy, IconCheck, IconSearch, IconSpinner } from './icons';
import { fmtDuration } from '../lib/format';
import { useScriptConversion } from '../hooks/useScriptConversion';
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

  // Converted chunks, by input index. The original list is NEVER mutated: a
  // conversion is a suggestion, and the source has to stay visible beside it
  // for anyone to judge whether it is right.
  const converted = useMemo(() => {
    const byIndex = new Map<number, string>();
    for (const item of conversion.ok) {
      if (item.text) byIndex.set(item.index, item.text);
    }
    return byIndex;
  }, [conversion.ok]);

  const rejectedIndexes = useMemo(
    () => new Map(conversion.rejected.map((i) => [i.index, i.detail ?? 'Rejected'])),
    [conversion.rejected],
  );

  async function fetchTranscript(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      conversion.reset();
      setData(await api.fetchTranscript(url.trim(), language || undefined));
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
                  onClick={() => conversion.start(data.chunks.map((c) => c.text), target)}
                  title={canConvert === false ? (cannotConvertReason ?? '') : undefined}
                >
                  {conversion.running ? <IconSpinner size={14} /> : null}
                  {conversion.running
                    ? 'Converting…'
                    : `Convert all ${data.chunks.length} parts`}
                </button>
                {/* One model load for the whole transcript is the entire point
                    of doing this as a batch, and saying so sets the right
                    expectation for a first run that takes minutes. */}
                <span className="muted">
                  {conversion.running
                    ? 'All parts convert in one pass — the model loads at most once.'
                    : 'Converts every part in one pass.'}
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
              {conversion.result && (
                <div className="convert-summary" role="status">
                  Converted {conversion.result.ok_count} of{' '}
                  {conversion.result.ok_count + conversion.result.rejected_count} parts to{' '}
                  {conversion.result.target_script === 'roman' ? 'Roman Urdu' : 'Urdu script'}
                  {conversion.result.rejected_count > 0 && (
                    <>
                      {' '}— <strong>{conversion.result.rejected_count} could not be
                      converted</strong> and are marked below. Read every part before you
                      generate from it.
                    </>
                  )}
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
                  converted.size
                    ? data.chunks
                        .map((c) => converted.get(c.index))
                        .filter((t): t is string => Boolean(t))
                        // A blank line between parts: `direction_analyze`
                        // treats a newline as the longest pause there is, so
                        // this is a real paragraph break, not formatting.
                        .join('\n\n')
                    : data.text,
                )
              }
              disabled={needsConversion && converted.size === 0}
              title={
                needsConversion && converted.size === 0
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
          <ul className="transcript-chunks">
            {data.chunks.map((chunk) => (
              <li key={chunk.index} className="transcript-chunk">
                <div className="chunk-head">
                  <span className="chunk-index">{chunk.index + 1}</span>
                  {!chunk.ends_on_sentence && (
                    <span className="tag warn" title="Cut mid-sentence to fit — the join may be audible">
                      cut mid-sentence
                    </span>
                  )}
                  <span className="muted">{chunk.text.length} chars</span>
                </div>
                {/* The ORIGINAL stays on screen next to the conversion. Three
                    of the four conversions have never passed a listening gate,
                    and even the gated one produces valid Urdu words meaning
                    something else often enough that a whole model was rejected
                    for it — so the source is what makes the suggestion
                    checkable rather than merely believable. */}
                {converted.has(chunk.index) && (
                  <p className="chunk-source" dir="auto" title="What the captions said">
                    {chunk.text}
                  </p>
                )}
                <p className="chunk-text" dir="auto">
                  {converted.get(chunk.index) ?? chunk.text}
                </p>
                {rejectedIndexes.has(chunk.index) && (
                  <p className="chunk-rejected" role="status">
                    <IconAlert size={13} /> {rejectedIndexes.get(chunk.index)}
                  </p>
                )}
                <div className="chunk-actions">
                  <button
                    type="button"
                    className="btn-sm"
                    onClick={() => onSendToEditor(converted.get(chunk.index) ?? chunk.text)}
                    disabled={needsConversion && !converted.has(chunk.index)}
                  >
                    Send to editor
                  </button>
                  <button
                    type="button"
                    className="btn-sm ghost"
                    onClick={() =>
                      void copy(converted.get(chunk.index) ?? chunk.text, `c${chunk.index}`)
                    }
                  >
                    {copied === `c${chunk.index}` ? <IconCheck size={13} /> : <IconCopy size={13} />}
                    {copied === `c${chunk.index}` ? 'Copied' : 'Copy'}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
