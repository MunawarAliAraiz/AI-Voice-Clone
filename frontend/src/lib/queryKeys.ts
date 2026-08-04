/**
 * AI Voice Clone Studio — TanStack Query key factory.
 *
 * CONTRACT MODULE. Wave 0. F1–F4 all import from here.
 *
 * Every key is built here and nowhere else. Inline array literals scattered
 * across components are how invalidation silently stops working: one component
 * writes `['models']`, another `['models', undefined]`, and the refetch after a
 * mutation quietly does nothing.
 *
 * Hierarchical by design — `queryClient.invalidateQueries({ queryKey: qk.models.all })`
 * invalidates every models query, including the parameterized ones.
 */

import type { LanguageCode } from '../types/api';

export const qk = {
  models: {
    all: ['models'] as const,
    list: () => [...qk.models.all, 'list'] as const,
    /** Residency changes as workers load and evict — poll this one. */
    status: () => [...qk.models.all, 'status'] as const,
  },

  languages: {
    all: ['languages'] as const,
    list: () => [...qk.languages.all, 'list'] as const,
  },

  profiles: {
    all: ['profiles'] as const,
    list: () => [...qk.profiles.all, 'list'] as const,
    detail: (id: number) => [...qk.profiles.all, 'detail', id] as const,
  },

  history: {
    all: ['history'] as const,
    list: (page: number, pageSize: number) =>
      [...qk.history.all, 'list', { page, pageSize }] as const,
    detail: (id: number) => [...qk.history.all, 'detail', id] as const,
  },

  system: {
    all: ['system'] as const,
    status: () => [...qk.system.all, 'status'] as const,
  },

  /**
   * Live script detection for the editor.
   *
   * Keyed on the text itself so the debounced result is cached per input. Keep
   * `staleTime` high — for a given (text, language) the answer is deterministic
   * and can never go stale.
   */
  scriptDetect: (text: string, language: LanguageCode) =>
    ['script-detect', language, text] as const,
} as const;

/**
 * How long each family stays fresh.
 *
 * The catalog is effectively static within a session; residency is not. Polling
 * the whole model list to discover that one model went warm is wasteful, which
 * is why `status` is separate from `list`.
 */
export const staleTimes = {
  /** Catalog contents change only on deploy. */
  models: 5 * 60_000,
  /** Residency changes as workers load and evict. */
  modelStatus: 5_000,
  languages: 5 * 60_000,
  profiles: 30_000,
  history: 10_000,
  system: 10_000,
  /** Deterministic for a given input — never stale. */
  scriptDetect: Infinity,
} as const;
