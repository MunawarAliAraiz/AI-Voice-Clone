/**
 * The pronunciation dictionary — words OmniVoice says wrong, and what to say
 * instead.
 *
 * Two things about this UI are not cosmetic:
 *
 * 1. **Disabled entries are shown, not hidden.** A disabled entry whose word
 *    matches a built-in suppresses that built-in — it is the only way to turn
 *    a shipped default off. Hiding disabled rows would hide the mechanism.
 * 2. **Disable and delete are different, and the copy says so.** Deleting your
 *    row restores the built-in; disabling it removes the built-in's effect.
 *
 * The word may be typed in either script — Latin for an English loanword
 * ("database"), Perso-Arabic for a word that came out of the transliterator
 * ("میٹنگ", which is read as "mating"). Matching is case-insensitive.
 */
import { useMemo, useState } from 'react';
import {
  useCreatePronunciationMutation,
  useDeletePronunciationMutation,
  usePronunciations,
  useUpdatePronunciationMutation,
} from '../hooks/queries';
import { ApiError } from '../services/api';
import type { PronunciationItem } from '../types/api';
import { IconAlert, IconCheck, IconSearch, IconSpinner, IconX } from './icons';

export function PronunciationPanel() {
  const { data, isLoading, error } = usePronunciations();
  const create = useCreatePronunciationMutation();
  const update = useUpdatePronunciationMutation();
  const remove = useDeletePronunciationMutation();

  const [word, setWord] = useState('');
  const [sayAs, setSayAs] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [exact, setExact] = useState(false);

  const all = data?.items ?? [];

  /*
   * Two match modes, because this list has two genuinely different questions
   * asked of it:
   *
   *   RELATIVE (default) — "what have I added about meetings?" Substring, over
   *     both the word and its replacement, so you can search by either half of
   *     a pair. This is browsing.
   *
   *   EXACT — "is `meeting` already in here, or is something ELSE changing how
   *     it sounds?" Substring is actively unhelpful for that: searching
   *     `meet` surfaces `meeting`, `meetings` and `meet` together and answers
   *     nothing. This is checking, and it is the question you ask right before
   *     adding an entry and hitting a 409.
   *
   * Case-insensitive in both modes, matching the server's UNIQUE index
   * (`key_text COLLATE NOCASE`) — a search that distinguishes case where
   * storage does not would report "not found" for a row that then collides.
   *
   * `localeCompare` with sensitivity 'base' rather than `===`, and 'base' is
   * load-bearing: it ignores case AND diacritics. 'accent' — which I used
   * first — keeps diacritics significant, and measuring it showed exactly the
   * failure that matters here: searching مِیٹِنگ found nothing while میٹنگ sat
   * in the list. This dictionary exists to hold diacritic respellings, so the
   * two forms of a word have to find each other or exact mode cannot answer
   * the one question it is for.
   */
  const items = useMemo(() => {
    const q = query.trim();
    if (!q) return all;
    if (exact) {
      return all.filter(
        (i) => i.key_text.localeCompare(q, undefined, { sensitivity: 'base' }) === 0,
      );
    }
    const needle = q.toLowerCase();
    return all.filter(
      (i) =>
        i.key_text.toLowerCase().includes(needle) ||
        i.replacement.toLowerCase().includes(needle),
    );
  }, [all, query, exact]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    create.mutate(
      { key_text: word, replacement: sayAs },
      {
        onSuccess: () => {
          setWord('');
          setSayAs('');
        },
        onError: (err) => {
          // A 409 is the ordinary "you already added this" case, not a
          // failure worth a stack trace — say it plainly.
          setFormError(
            err instanceof ApiError && err.status === 409
              ? `“${word}” is already in your dictionary. Edit that entry instead.`
              : err instanceof Error
                ? err.message
                : 'Could not save that entry.',
          );
        },
      },
    );
  }

  return (
    <section className="card pron-card" aria-labelledby="pron-h">
      <header className="card-head">
        <h2 id="pron-h">
          Pronunciation
          {all.length > 0 && (
            <span className="count">
              {/* Both numbers while filtering: a bare count that shrinks as
                  you type reads as "entries were deleted". */}
              {query.trim() ? `${items.length} / ${all.length}` : all.length}
            </span>
          )}
        </h2>
      </header>

      {all.length > 0 && (
        <div className="pron-search">
          {/* Reuses History's `.search` shell rather than a parallel one —
              two search boxes in one app that look different is a bug. */}
          <div className="search">
            <IconSearch size={14} />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={exact ? 'Exact word…' : 'Search word or spelling…'}
              dir="auto"
              aria-label="Search the dictionary"
            />
            {query && (
              <button
                type="button"
                className="icon-btn"
                onClick={() => setQuery('')}
                aria-label="Clear search"
              >
                <IconX size={13} />
              </button>
            )}
          </div>
          <label className="pron-exact">
            <input
              type="checkbox"
              checked={exact}
              onChange={(e) => setExact(e.target.checked)}
            />
            <span title="Match the whole word only, ignoring accents — the question you ask before adding an entry.">
              Exact
            </span>
          </label>
        </div>
      )}

      <p className="hint">
        If a word comes out wrong, add it here with a spelling that sounds right. Applies to
        your next generation — anything already queued keeps the text it was queued with.
      </p>

      <form className="pron-form" onSubmit={submit}>
        <label className="field">
          <span>Word as you type it</span>
          <input
            value={word}
            onChange={(e) => setWord(e.target.value)}
            placeholder="database"
            maxLength={100}
            required
          />
        </label>
        <label className="field">
          <span>Spelling that sounds right</span>
          <input
            value={sayAs}
            onChange={(e) => setSayAs(e.target.value)}
            placeholder="ڈیٹا بےس"
            maxLength={200}
            required
          />
        </label>
        <button type="submit" className="btn" disabled={create.isPending || !word || !sayAs}>
          {create.isPending ? <IconSpinner size={14} /> : <IconCheck size={14} />} Add
        </button>
      </form>

      {formError && (
        <div className="inline-error" role="alert">
          <IconAlert size={14} /> {formError}
        </div>
      )}

      {error && (
        <div className="inline-error" role="alert">
          <IconAlert size={14} /> Could not load your dictionary.
        </div>
      )}

      {isLoading && <p className="muted center">Loading…</p>}

      {!isLoading && !error && all.length === 0 && (
        <p className="muted center">
          No entries yet. A few common words are already handled without one.
        </p>
      )}

      {/* Distinct from the above on purpose: "you have none" and "none match
          what you typed" are different facts, and showing the first while a
          filter is active reads as data loss. */}
      {!isLoading && !error && all.length > 0 && items.length === 0 && (
        <p className="muted center">
          Nothing matches “{query.trim()}”
          {exact && ' exactly'}. {exact ? 'Try turning off Exact.' : 'Try a shorter search.'}
        </p>
      )}

      {items.length > 0 && (
        <ul className="pron-list">
          {items.map((item) => (
            <Row
              key={item.id}
              item={item}
              busy={update.isPending || remove.isPending}
              onToggle={() =>
                update.mutate({ id: item.id, body: { is_enabled: !item.is_enabled } })
              }
              onDelete={() => remove.mutate(item.id)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function Row({
  item,
  busy,
  onToggle,
  onDelete,
}: {
  item: PronunciationItem;
  busy: boolean;
  onToggle: () => void;
  onDelete: () => void;
}) {
  return (
    <li className={item.is_enabled ? 'pron-row' : 'pron-row off'}>
      <div className="pron-words">
        {/* `dir="auto"` per field, not on the row: the word may be Latin while
            the replacement is Perso-Arabic, and one direction for both would
            mangle whichever half disagreed. */}
        <span className="pron-key" dir="auto">
          {item.key_text}
        </span>
        <span className="pron-arrow" aria-hidden="true">
          →
        </span>
        <span className="pron-value" dir="auto">
          {item.replacement}
        </span>
      </div>
      <div className="pron-actions">
        <button
          type="button"
          className="icon-btn"
          onClick={onToggle}
          disabled={busy}
          title={
            item.is_enabled
              ? 'Turn off. If this word has a built-in spelling, turning it off stops that too.'
              : 'Turn on'
          }
          aria-pressed={item.is_enabled}
        >
          {item.is_enabled ? 'On' : 'Off'}
        </button>
        <button
          type="button"
          className="icon-btn danger"
          onClick={onDelete}
          disabled={busy}
          title="Remove. Any built-in spelling for this word applies again."
          aria-label={`Remove ${item.key_text}`}
        >
          <IconX size={14} />
        </button>
      </div>
    </li>
  );
}
