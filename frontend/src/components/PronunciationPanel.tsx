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
import { useState } from 'react';
import {
  useCreatePronunciationMutation,
  useDeletePronunciationMutation,
  usePronunciations,
  useUpdatePronunciationMutation,
} from '../hooks/queries';
import { ApiError } from '../services/api';
import type { PronunciationItem } from '../types/api';
import { IconAlert, IconCheck, IconSpinner, IconX } from './icons';

export function PronunciationPanel() {
  const { data, isLoading, error } = usePronunciations();
  const create = useCreatePronunciationMutation();
  const update = useUpdatePronunciationMutation();
  const remove = useDeletePronunciationMutation();

  const [word, setWord] = useState('');
  const [sayAs, setSayAs] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  const items = data?.items ?? [];

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
          {items.length > 0 && <span className="count">{items.length}</span>}
        </h2>
      </header>

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

      {!isLoading && !error && items.length === 0 && (
        <p className="muted center">
          No entries yet. A few common words are already handled without one.
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
