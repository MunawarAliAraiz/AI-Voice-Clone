// API client for the voice-clone backend. Plain fetch, problem+json aware.

import type {
  HistoryItem,
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

/**
 * Backend URL precedence:
 * 1. localStorage('vcs_api_base') — user override
 * 2. import.meta.env.VITE_API_BASE — build-time baked
 * 3. '' (same-origin) for prod, localhost for dev
 */
function getApiBase(): string {
  // 1. Runtime override from localStorage
  const stored = localStorage.getItem('vcs_api_base');
  if (stored) {
    const normalized = normalizeUrl(stored);
    if (normalized !== null) return normalized;
    console.warn('[api] Invalid vcs_api_base in localStorage, ignoring:', stored);
  }

  // 2. Build-time env var
  const envBase = import.meta.env.VITE_API_BASE;
  if (envBase) {
    const normalized = normalizeUrl(envBase);
    if (normalized !== null) return normalized;
  }

  // 3. Fallback: same-origin for prod, localhost for dev
  return import.meta.env.DEV ? 'http://localhost:8000' : '';
}

function normalizeUrl(url: string): string | null {
  const trimmed = url.trim().replace(/\/+$/, '');
  if (!trimmed) return '';
  if (!/^https?:\/\//i.test(trimmed)) {
    console.error('[api] Backend URL must start with http:// or https://');
    return null;
  }
  // Warn on mixed content (HTTPS frontend calling HTTP backend)
  if (typeof window !== 'undefined' && window.location.protocol === 'https:' && trimmed.startsWith('http://')) {
    console.warn('[api] Mixed content: HTTPS page cannot call HTTP backend. Browser will block it.');
  }
  return trimmed;
}

const API_BASE = getApiBase();

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

  // ngrok free tier bypass (custom header triggers CORS preflight)
  if (API_BASE.includes('ngrok')) {
    headers.set('ngrok-skip-browser-warning', 'true');
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch {
    const msg = API_BASE.startsWith('https://')
      ? 'Cannot reach the backend. Check the URL in settings and verify CORS is configured.'
      : 'Cannot reach the backend. Is it running?';
    throw new ApiError(0, 'NETWORK', msg);
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
  setFavorite: (id: number, isFavorite: boolean) =>
    request<HistoryItem>(`/api/history/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_favorite: isFavorite }),
    }),
  deleteHistory: (id: number) =>
    request<void>(`/api/history/${id}`, { method: 'DELETE' }),
};
