// TanStack Query hooks — the server-state layer CLAUDE.md already claimed
// existed. Replaces App.tsx's manual `refresh()`/`reload()` pair, which
// refetched languages + voices + history on every mutation and re-downloaded
// the whole history list (not just the next page) on "load more".

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../services/api';
import type { JobStatusResponse } from '../types/api';

export const queryKeys = {
  languages: ['languages'] as const,
  voices: ['voices'] as const,
  models: ['models'] as const,
  history: (page: number, pageSize: number) => ['history', page, pageSize] as const,
  jobs: (page: number, pageSize: number) => ['jobs', page, pageSize] as const,
  job: (id: number) => ['job', id] as const,
};

export function useLanguages() {
  return useQuery({ queryKey: queryKeys.languages, queryFn: api.languages, staleTime: 60_000 });
}

// Powers the Composer's model picker. Short staleTime (not 0): `state` and
// `est_wait_sec` are live scheduler facts, but re-fetching on every keystroke
// re-render would be wasted work for a value that changes on the order of
// seconds, not milliseconds.
export function useModels() {
  return useQuery({ queryKey: queryKeys.models, queryFn: api.models, staleTime: 15_000 });
}

export function useVoices() {
  return useQuery({ queryKey: queryKeys.voices, queryFn: api.listVoices });
}

export function useHistory(page: number, pageSize: number) {
  return useQuery({
    queryKey: queryKeys.history(page, pageSize),
    queryFn: () => api.history(page, pageSize),
  });
}

export function useJobsList(page: number, pageSize: number) {
  return useQuery({
    queryKey: queryKeys.jobs(page, pageSize),
    queryFn: () => api.jobs(page, pageSize),
  });
}

const TERMINAL = new Set<JobStatusResponse['status']>(['succeeded', 'failed', 'cancelled']);
export function isTerminal(status: JobStatusResponse['status']): boolean {
  return TERMINAL.has(status);
}

/**
 * Polls a single job until it reaches a terminal status, then stops.
 * `onSettled` fires exactly once per job id, the turn it first observes a
 * terminal status — the hook the Composer's toast and result card key off.
 */
export function useJob(id: number | null) {
  return useQuery({
    queryKey: id !== null ? queryKeys.job(id) : ['job', 'idle'],
    queryFn: () => api.job(id as number),
    enabled: id !== null,
    // TanStack Query v5: a function form gets the live Query object so the
    // interval can depend on the LAST fetched data, not just the initial one.
    refetchInterval: (query) => {
      const data = query.state.data as JobStatusResponse | undefined;
      if (!data || !isTerminal(data.status)) return 1500;
      return false;
    },
  });
}

/** Enqueue a generation. On success, invalidate the lists that now include it. */
export function useGenerateMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.generate,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
}

export function useCancelJobMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.cancelJob(id),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(id) });
      void queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
}

/**
 * The old `reload()`'s replacement: after a mutation that changes voices,
 * history, or jobs, invalidate ONLY those — not a full languages+voices+
 * history refetch on every keystroke-adjacent action.
 */
export function useInvalidateAfterJobSuccess() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ['history'] });
    void queryClient.invalidateQueries({ queryKey: ['jobs'] });
  };
}

export function useInvalidateVoices() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: ['voices'] });
}

export function useInvalidateHistory() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: ['history'] });
}
