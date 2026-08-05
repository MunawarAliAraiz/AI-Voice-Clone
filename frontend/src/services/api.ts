// API client for the voice-clone backend. Plain fetch, problem+json aware.

import type {
  HistoryList,
  LanguageListResponse,
  ModelListResponse,
  ProblemJson,
  ScriptDetectResponse,
  SystemStatus,
  TTSGenerateRequest,
  TTSGenerateResponse,
  VoiceProfile,
  VoiceProfileList,
} from '../types/api';

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000';

/** Media/audio URLs from the API are root-relative and already signed. */
export function mediaUrl(path: string): string {
  return path.startsWith('http') ? path : `${API_BASE}${path}`;
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

function apiKey(): string {
  return localStorage.getItem('vcs_api_key') ?? '';
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const key = apiKey();
  if (key) headers.set('X-API-Key', key);

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, 'NETWORK', 'Cannot reach the backend. Is it running?');
  }

  if (!res.ok) {
    let problem: Partial<ProblemJson> = {};
    try {
      problem = (await res.json()) as ProblemJson;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, problem.code ?? 'ERROR', problem.detail ?? res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  // system
  health: () => request<{ status: string; version: string }>('/api/health'),
  system: () => request<SystemStatus>('/api/system'),
  models: () => request<ModelListResponse>('/api/models'),
  languages: () => request<LanguageListResponse>('/api/languages'),

  // voices
  listVoices: () => request<VoiceProfileList>('/api/voices'),
  getVoice: (id: number) => request<VoiceProfile>(`/api/voices/${id}`),
  deleteVoice: (id: number) => request<void>(`/api/voices/${id}`, { method: 'DELETE' }),
  createVoice: (form: FormData) =>
    request<VoiceProfile>('/api/voices', { method: 'POST', body: form }),

  // synthesis
  generate: (body: TTSGenerateRequest) =>
    request<TTSGenerateResponse>('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  detectScript: (text: string, language: string) =>
    request<ScriptDetectResponse>('/api/detect-script', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, language }),
    }),

  // history
  history: (page = 1, pageSize = 50) =>
    request<HistoryList>(`/api/history?page=${page}&page_size=${pageSize}`),
};
