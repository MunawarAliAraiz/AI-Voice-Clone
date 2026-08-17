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
import { useState } from 'react';
import { api, ApiError } from '../services/api';
import type { TranscriptResponse } from '../types/api';
import { IconAlert, IconCopy, IconCheck, IconSearch, IconSpinner } from './icons';
import { fmtDuration } from '../lib/format';

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

  async function fetchTranscript(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
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
          {data.needs_transliteration && (
            <div className="inline-error" role="status">
              <IconAlert size={14} />
              <span>
                This transcript is in Devanagari, which no voice here can read. Convert it to
                Urdu script first — that feature is still being validated, so for now copy the
                text and convert it yourself.
              </span>
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
              onClick={() => onSendToEditor(data.text)}
              disabled={data.needs_transliteration}
              title={
                data.needs_transliteration
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
                <p className="chunk-text" dir="auto">
                  {chunk.text}
                </p>
                <div className="chunk-actions">
                  <button
                    type="button"
                    className="btn-sm"
                    onClick={() => onSendToEditor(chunk.text)}
                    disabled={data.needs_transliteration}
                  >
                    Send to editor
                  </button>
                  <button
                    type="button"
                    className="btn-sm ghost"
                    onClick={() => void copy(chunk.text, `c${chunk.index}`)}
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
