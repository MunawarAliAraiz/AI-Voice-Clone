/**
 * Enqueue a script conversion and follow it to the end.
 *
 * Shared by the Composer (one passage, Roman Urdu → Urdu script, so OmniVoice
 * can say it) and the Import tab (a whole transcript at once, Devanagari or
 * Urdu script → whichever the owner picked). Both want exactly this: enqueue,
 * poll, get items back, know whether it is still running.
 *
 * WHY A BATCH IS ONE CALL AND NOT A LOOP
 * ---------------------------------------
 * `convert_many` runs every chunk against ONE model residency. From a cold
 * start that is the difference between one 19 GB load and forty-five, which
 * for a 90,000-character transcript is minutes instead of hours. A caller that
 * loops over chunks here would silently throw that away, so this takes a list
 * and there is no single-item path.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 * ---------------------------------
 * It does not apply the result. A conversion is a suggestion the user reads
 * and accepts — three of the four directions have never passed a listening
 * gate, and even the gated one produces valid Urdu words that mean something
 * else often enough that A3 run 2 was rejected on exactly that. Auto-replacing
 * the user's text would be golden rule 5's silent substitution wearing a
 * different hat.
 */
import { useCallback, useMemo, useState } from 'react';
import { useJob, useTransliterateMutation } from './queries';
import type { TransliterateItem, TransliterateResult } from '../types/api';

export interface ScriptConversion {
  /** True from enqueue until the job settles. */
  running: boolean;
  /** Present only once the job SUCCEEDED. */
  result: TransliterateResult | null;
  /** A failed job, or a refusal at enqueue (no transliterator, bad pair). */
  error: string | null;
  /** Items that converted, in input order. Empty until there is a result. */
  ok: TransliterateItem[];
  /** Items the validator refused. Each carries a reason and NO text. */
  rejected: TransliterateItem[];
  /** Seconds until this job is expected to finish, from the server's own
   *  estimate. `null` before the first poll answers. */
  etaSec: number | null;
  /** 0 = running now. Above 0 means it is WAITING BEHIND other jobs — the
   *  difference a bare spinner cannot express, and the one that made a queued
   *  per-part conversion look like a hang. */
  queuePosition: number | null;
  /** 'queued' while something else holds the GPU, 'running' once it is ours. */
  phase: 'idle' | 'queued' | 'running';
  start: (texts: string[], target?: 'roman' | 'perso_arabic') => void;
  reset: () => void;
}

export function useScriptConversion(): ScriptConversion {
  const [jobId, setJobId] = useState<number | null>(null);
  const [enqueueError, setEnqueueError] = useState<string | null>(null);
  const mutation = useTransliterateMutation();
  const job = useJob(jobId);

  const start = useCallback(
    (texts: string[], target?: 'roman' | 'perso_arabic') => {
      setEnqueueError(null);
      setJobId(null);
      mutation.mutate(
        { texts, ...(target ? { target } : {}) },
        {
          onSuccess: (created) => setJobId(created.id),
          // The 503 from a server with no transliterator lands here, and its
          // detail is the sentence the backend composed about THIS card —
          // how much it needs, how much there is. Show that, not a generic
          // "conversion failed", because the two have different next steps.
          onError: (err: unknown) =>
            setEnqueueError(err instanceof Error ? err.message : String(err)),
        },
      );
    },
    [mutation],
  );

  const reset = useCallback(() => {
    setJobId(null);
    setEnqueueError(null);
  }, []);

  const data = job.data;
  const succeeded = data?.status === 'succeeded';
  const result = (succeeded ? (data.result as unknown as TransliterateResult) : null) ?? null;

  const { ok, rejected } = useMemo(() => {
    const items = result?.items ?? [];
    return {
      ok: items.filter((i) => i.status === 'ok'),
      rejected: items.filter((i) => i.status === 'rejected'),
    };
  }, [result]);

  const phase: 'idle' | 'queued' | 'running' =
    mutation.isPending || (jobId !== null && !data)
      ? 'queued'
      : data?.status === 'queued'
        ? 'queued'
        : data?.status === 'running'
          ? 'running'
          : 'idle';

  return {
    etaSec: data?.eta_sec ?? null,
    queuePosition: data?.position ?? null,
    phase,
    running:
      mutation.isPending ||
      (jobId !== null && (!data || data.status === 'queued' || data.status === 'running')),
    result,
    error:
      enqueueError ??
      (data?.status === 'failed' ? (data.error?.detail ?? 'Conversion failed.') : null),
    ok,
    rejected,
    start,
    reset,
  };
}
