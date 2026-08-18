/**
 * Per-part state for an imported transcript.
 *
 * WHY ONE RECORD INSTEAD OF FOUR MAPS
 * ------------------------------------
 * The panel grew `converted`, `rejectedIndexes` and `busyIndex` as three
 * parallel structures keyed by the same index, and adding "the user edited
 * this" would have made four. Four containers that must agree about the same
 * part is three chances for them to disagree — and the way they disagree is
 * silent, because every value involved is plausible-looking text.
 *
 * So: one record per part, and everything a component asks about it is
 * DERIVED. `status` is not stored, because a stored status is a fifth thing to
 * keep in step.
 *
 * WHAT `outgoing` IS FOR
 * -----------------------
 * "Which text does Send to editor use?" had three separate answers scattered
 * across the panel. It is one function here, so a part that was converted and
 * then edited cannot send the model's output from one button and the user's
 * correction from another.
 *
 * THE SOURCE IS NEVER MUTATED
 * ----------------------------
 * `docs/TRANSCRIPT_IMPORT.md` is explicit that the transcript itself is
 * read-only and the editable copy lives in the Composer — two editable copies
 * is how they drift. A conversion under review is not a second copy of the
 * transcript; it is a suggestion being corrected before it leaves. The caption
 * stays exactly as YouTube sent it, which is also what makes the conversion
 * checkable: you can always see what it was derived from.
 */
import { useCallback, useMemo, useState } from 'react';
import type { TranscriptChunk, TransliterateItem } from '../types/api';

export type PartStatus = 'original' | 'converted' | 'edited' | 'rejected';

export interface PartState {
  /** The caption as imported. Never mutated. */
  readonly source: string;
  /** The model's output, verbatim. `null` until a conversion succeeds. */
  converted: string | null;
  /** The user's edit of `converted`. `null` while untouched. */
  draft: string | null;
  /** The validator's reason. A rejected part carries NO text, by contract. */
  rejection: string | null;
  /** Which target `converted` is in — a part converted to Roman and a part
   *  converted to Urdu script must not look alike. */
  target: 'roman' | 'perso_arabic' | null;
}

export interface TranscriptParts {
  get: (index: number) => PartState | undefined;
  status: (index: number) => PartStatus;
  /** The text this part should hand onward. One answer, one place. */
  outgoing: (index: number) => string;
  convertedCount: number;
  rejectedCount: number;
  editedCount: number;
  reset: (chunks: TranscriptChunk[]) => void;
  edit: (index: number, draft: string) => void;
  /** Drop an edit and go back to the model's output. */
  revert: (index: number) => void;
  applyConversion: (
    batchIndexes: number[],
    ok: TransliterateItem[],
    rejected: TransliterateItem[],
    target: 'roman' | 'perso_arabic',
  ) => void;
}

export function useTranscriptParts(): TranscriptParts {
  const [parts, setParts] = useState<Map<number, PartState>>(new Map());

  const reset = useCallback((chunks: TranscriptChunk[]) => {
    setParts(
      new Map(
        chunks.map((chunk) => [
          chunk.index,
          {
            source: chunk.text,
            converted: null,
            draft: null,
            rejection: null,
            target: null,
          } satisfies PartState,
        ]),
      ),
    );
  }, []);

  const edit = useCallback((index: number, draft: string) => {
    setParts((prev) => {
      const part = prev.get(index);
      if (!part) return prev;
      const next = new Map(prev);
      next.set(index, { ...part, draft });
      return next;
    });
  }, []);

  const revert = useCallback((index: number) => {
    setParts((prev) => {
      const part = prev.get(index);
      if (!part) return prev;
      const next = new Map(prev);
      next.set(index, { ...part, draft: null });
      return next;
    });
  }, []);

  const applyConversion = useCallback(
    (
      batchIndexes: number[],
      ok: TransliterateItem[],
      rejected: TransliterateItem[],
      target: 'roman' | 'perso_arabic',
    ) => {
      setParts((prev) => {
        const next = new Map(prev);
        // `item.index` is the position IN THE BATCH, not in the transcript.
        // Converting part 8 alone returns index 0, so without this remap that
        // result lands on part 1 — and both are plausible Roman Urdu, so
        // nothing would look wrong. This is the one line in the file that
        // must not be "simplified".
        const chunkIndexOf = (batchPosition: number) => batchIndexes[batchPosition];

        for (const item of ok) {
          const index = chunkIndexOf(item.index);
          const part = index === undefined ? undefined : next.get(index);
          if (!part || !item.text) continue;
          next.set(index!, {
            ...part,
            converted: item.text,
            target,
            // A successful re-run CLEARS the old rejection, or the part shows
            // a converted body underneath a stale error.
            rejection: null,
            // And it clears the edit, because the edit was of the previous
            // conversion — keeping it would silently re-attach a correction to
            // text it was never written against.
            draft: null,
          });
        }

        for (const item of rejected) {
          const index = chunkIndexOf(item.index);
          const part = index === undefined ? undefined : next.get(index);
          if (!part) continue;
          next.set(index!, {
            ...part,
            rejection: item.detail ?? 'The model did not return a conversion.',
          });
        }
        return next;
      });
    },
    [],
  );

  const get = useCallback((index: number) => parts.get(index), [parts]);

  const status = useCallback(
    (index: number): PartStatus => {
      const part = parts.get(index);
      if (!part) return 'original';
      if (part.rejection) return 'rejected';
      if (part.draft !== null && part.draft !== part.converted) return 'edited';
      if (part.converted !== null) return 'converted';
      return 'original';
    },
    [parts],
  );

  const outgoing = useCallback(
    (index: number): string => {
      const part = parts.get(index);
      if (!part) return '';
      return part.draft ?? part.converted ?? part.source;
    },
    [parts],
  );

  const counts = useMemo(() => {
    let converted = 0;
    let rejected = 0;
    let edited = 0;
    for (const part of parts.values()) {
      if (part.rejection) rejected += 1;
      else if (part.draft !== null && part.draft !== part.converted) edited += 1;
      else if (part.converted !== null) converted += 1;
    }
    // An edited part IS a converted part — it just has a correction on top.
    // Reporting "3 converted" while 5 are usable would understate the work.
    return { convertedCount: converted + edited, rejectedCount: rejected, editedCount: edited };
  }, [parts]);

  return { get, status, outgoing, reset, edit, revert, applyConversion, ...counts };
}
