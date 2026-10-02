// API client for the voice-clone backend. Plain fetch, problem+json aware.

import type {
  DirectionAnalyzeResponse,
  HistoryItem,
  HistoryList,
  JobList,
  JobStatusResponse,
  LanguageListResponse,
  ModelListResponse,
  ProblemJson,
  PronunciationCreate,
  PronunciationItem,
  PronunciationList,
  PronunciationUpdate,
  ScriptDetectResponse,
  SystemStatus,
  TTSGenerateRequest,
  TitleResponse,
  PreparedTextResponse,
  TransliterateRequest,
  VoiceProfile,
  VoiceProfileList,
} from '../types/api';

export interface CloudVolume { id: string; name: string; size: number; dataCenter: string; type: string }
export interface CloudModelInstall { state: string; progress_pct?: number | null; current_file?: string; detail?: string; bytes_completed?: number; bytes_total?: number | null }
export interface CloudSetup {
  connected: boolean; stage: string; ready: boolean; release_available: boolean;
  volume: CloudVolume | null; models: Record<string, CloudModelInstall>;
  required_model_ids?: string[];
  progress_pct: number | null; bytes_completed: number; bytes_total: number | null; detail: string;
  setup_phase?: 'idle' | 'starting_worker' | 'checking_files' | 'downloading' | 'verifying' | 'stopping_worker' | 'ready' | 'failed' | 'cancelling' | 'cancelled';
  setup_error?: string | null; setup_running?: boolean; cleanup_pending?: boolean;
  auto_setup_enabled?: boolean | null; auto_setup_waiting?: boolean; auto_setup_retry_at?: string | null;
  auto_setup_requires_resume?: boolean;
  compute: { pod_id: string | null; kind: string; status: string; hourly_usd: number; deadline: string; creation_confirmed?: boolean } | null;
  policy: { max_session_usd: number; max_hourly_usd: number } | null;
}
export interface CloudDiscovery {
  volumes: CloudVolume[];
  regions: { id: string; name: string; gpu_hourly_from_usd: number; installer_hourly_from_usd: number }[];
  balance_usd: number | null; account_hourly_spend_usd: number | null; funding_url: string;
}
export interface StorageQuote {
  id: string; region: string; storage_gb: number; monthly_usd: number;
  expires_at: number; balance_usd: number | null; can_purchase: boolean;
  required_credit_reserve_usd: number; installer_budget_usd: number;
}

export interface AudioToolsStatus {
  stage: 'idle' | 'verifying' | 'downloading' | 'extracting' | 'ready' | 'failed' | 'cancelled';
  ready: boolean; detail: string; bytes_completed: number; bytes_total: number;
  progress_pct: number; version: string; source_url: string;
}

/**
 * Backend URL precedence:
 * 1. import.meta.env.VITE_API_BASE — build-time, for dev against a non-default
 *    address (put it in frontend/.env.local)
 * 2. '' (same-origin) for prod, localhost:8000 for dev
 *
 * There is deliberately no runtime override. In production the Worker serving
 * this page also proxies `/api/*` to the backend, so same-origin is the only
 * base that can be correct — pointing the app anywhere else puts requests
 * cross-origin and reinstates the ngrok interstitial that breaks `<audio>`.
 * A settings box for it could only ever be set wrong, and because localStorage
 * outlives a deploy it broke exactly one device while every other one worked.
 */
function getApiBase(): string {
  const envBase = import.meta.env.VITE_API_BASE;
  if (envBase) {
    const normalized = normalizeUrl(envBase);
    if (normalized !== null) return normalized;
  }

  return '';
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

/** Media/audio URLs from the API are root-relative and already signed. */
export function mediaUrl(path: string): string {
  return path.startsWith('http') ? path : `${getApiBase()}${path}`;
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

function apiKey(): string {
  return window.__VCS_DESKTOP_KEY__ ?? localStorage.getItem('vcs_api_key') ?? '';
}

export type RunpodEstimate = {
  gpu_id: string;
  gpu_name: string;
  vram_gb: number;
  availability: string;
  hourly_gpu_usd: number;
  warm_estimated_compute_usd: number;
  cold_estimated_compute_usd: number;
  calibration: string;
};

export type RunpodAnalytics = {
  pods: { id: string; name: string; status: string; cost: number; gpu?: { id: string } }[];
  volumes: { id: string; name: string; size: number; type: string }[];
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const apiBase = getApiBase();
  const headers = new Headers(init.headers);
  const key = apiKey();
  if (key) headers.set('X-API-Key', key);

  // ngrok free tier bypass (custom header triggers CORS preflight)
  if (apiBase.includes('ngrok')) {
    headers.set('ngrok-skip-browser-warning', 'true');
  }

  let res: Response;
  try {
    res = await fetch(`${apiBase}${path}`, { ...init, headers });
  } catch {
    const msg = apiBase.startsWith('https://')
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
  audioToolsStatus: () => request<AudioToolsStatus>('/api/audio-tools/status'),
  retryAudioTools: () => request<AudioToolsStatus>('/api/audio-tools/start', { method: 'POST' }),
  cloudSetup: () => request<CloudSetup>('/api/runpod/setup'),
  cloudDiscover: () => request<CloudDiscovery>('/api/runpod/setup/discover'),
  cloudQuote: (region: string) => request<StorageQuote>('/api/runpod/setup/storage/quote', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ region }),
  }),
  cloudPurchase: (quoteId: string) => request<CloudVolume>('/api/runpod/setup/storage/purchase', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ quote_id: quoteId }),
  }),
  cloudSelectStorage: (volumeId: string) => request<CloudVolume>('/api/runpod/setup/storage', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ volume_id: volumeId }),
  }),
  cloudInstall: () => request<CloudSetup>('/api/runpod/setup/install', { method: 'POST' }),
  cloudCancel: () => request<CloudSetup>('/api/runpod/setup/cancel', { method: 'POST' }),
  cloudAutoSetup: (enabled: boolean) => request<CloudSetup>('/api/runpod/setup/auto', {
    method: 'PUT', body: JSON.stringify({ enabled }),
  }),
  cloudRelease: () => request<void>('/api/runpod/setup/release', { method: 'POST' }),
  cloudPolicy: (session: number, hourly: number) => request<void>('/api/runpod/setup/policy', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ max_session_usd: session, max_hourly_usd: hourly }),
  }),
  runpodConnection: () => request<{ connected: boolean }>('/api/runpod/connection'),
  connectRunpod: (apiKey: string) => request<{ connected: boolean }>('/api/runpod/connection', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey }),
  }),
  disconnectRunpod: () => request<void>('/api/runpod/connection', { method: 'DELETE' }),
  runpodWorker: () => request<{ paired: boolean; pod_id: string | null }>('/api/runpod/worker'),
  pairRunpodWorker: (podId: string, workerToken: string) =>
    request<{ paired: boolean; pod_id: string }>('/api/runpod/worker', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pod_id: podId, worker_token: workerToken }),
    }),
  unpairRunpodWorker: () => request<void>('/api/runpod/worker', { method: 'DELETE' }),
  runpodWorkerModels: () => request<{ models: { id: string; revision: string; state: string }[] }>(
    '/api/runpod/worker/models',
  ),
  installRunpodModel: (modelId: string) =>
    request<{ model_id: string; state: string }>(
      `/api/runpod/worker/models/${encodeURIComponent(modelId)}/install`,
      { method: 'POST' },
    ),
  runpodAnalytics: () => request<RunpodAnalytics>('/api/runpod/analytics'),
  runpodEstimate: (modelId: string, text: string) =>
    request<{ minimum_full_feature_vram_gb: number; estimates: RunpodEstimate[] }>(
      `/api/runpod/estimate?model_id=${encodeURIComponent(modelId)}&text=${encodeURIComponent(text)}`,
    ),
  runpodPodUsage: (id: string) =>
    request<{ totals: { totalAmount?: number; gpuAmount?: number; diskAmount?: number } }>(
      `/api/runpod/analytics/pods/${encodeURIComponent(id)}`,
    ),
  runpodVolumeUsage: (id: string) =>
    request<{ totals: { totalAmount?: number } }>(
      `/api/runpod/analytics/volumes/${encodeURIComponent(id)}`,
    ),
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
  previewEditVoice: (form: FormData) =>
    request<{ preview_url: string; duration_sec?: number; peak_dbfs?: number; is_clipped?: boolean }>('/api/voices/preview-edit', { method: 'POST', body: form }),

  // synthesis — POST /generate is async now: 202 with a job to poll, not a
  assembleDialogue: (body: { lines: { job_id: number; pause_after_sec: number }[] }) =>
    request<{ id: string; audio_url: string; duration_sec: number; sha256: string;
      spans: { job_id: number; start_sec: number; end_sec: number }[] }>(
      '/api/dialogue/assemble', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) },
    ),
  dialogueDraft: () => request<{ draft: unknown | null }>('/api/dialogue/draft'),
  saveDialogueDraft: (body: { draft: unknown }) =>
    request<{ saved: boolean }>('/api/dialogue/draft', { method: 'PUT',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  // completed generation. See job() / jobs() / cancelJob() below.
  generate: (body: TTSGenerateRequest) =>
    request<JobStatusResponse>('/api/generate', {
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
  analyzeDirection: (
    text: string,
    language: string,
    modelId: string | null = null,
    allowExperimental: boolean = false,
  ) =>
    request<DirectionAnalyzeResponse>('/api/direction/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        language,
        model_id: modelId,
        allow_experimental: allowExperimental,
      }),
    }),
  // LLM-backed Speech Direction classification — same {text, language} body
  // as analyzeDirection() above, but async: 202 with a job to poll (kind
  // 'analyze_llm'), same JobStatusResponse shape generate() returns. The
  // job's eventual `result` is an `AnalyzeLlmResult` ({rows, gen_time_sec,
  // load_time_sec}), not a TTSGenerateResponse — see types/api.ts.
  analyzeLlm: (text: string, language: string) =>
    request<JobStatusResponse>('/api/direction/analyze-llm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, language }),
    }),

  // jobs
  job: (id: number) => request<JobStatusResponse>(`/api/jobs/${id}`),
  jobs: (page = 1, pageSize = 50) =>
    request<JobList>(`/api/jobs?page=${page}&page_size=${pageSize}`),
  cancelJob: (id: number) => request<void>(`/api/jobs/${id}`, { method: 'DELETE' }),

  /**
   * Re-enqueue a finished job from its own stored parameters. The server
   * reuses the ORIGINAL route rather than re-resolving it, so a retry cannot
   * quietly come back on a different model than the one you were shown.
   */
  retryJob: (id: number) =>
    request<JobStatusResponse>(`/api/jobs/${id}/retry`, { method: 'POST' }),

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

  /**
   * Short label for a generation. Synchronous, not a job — the client needs it
   * before it can enqueue. Never throws the caller into a dead end: the server
   * falls back to the text and says `source: "text"` when the analyzer is
   * unavailable.
   */
  /**
   * Chunk a pasted script for the Convert tab. No network — the server splits
   * the text, detects its script, and reports whether it needs converting
   * before it can be spoken. Conversion itself is `transliterate` below.
   */
  prepareText: (text: string, source_language?: 'en' | 'hi' | 'ur') =>
    request<PreparedTextResponse>('/api/transcript/prepare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, source_language }),
    }),

  importYoutubeTranscript: (url: string, source_language: 'en' | 'hi' | 'ur') =>
    request<PreparedTextResponse>('/api/transcript/youtube', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, source_language }),
    }),

  /**
   * Convert text between scripts. **202 + poll**, not synchronous — unlike
   * `suggestTitle` below, this one touches the GPU.
   *
   * Send one `text` or many `texts`, never both. The server detects the SOURCE
   * script from the text; `target` is the caller's choice and may be omitted
   * for this source's usual destination.
   *
   * A 503 here means the server has no transliterator — read
   * `SystemStatus.script_conversion.reason` and show it rather than retrying.
   */
  transliterate: (body: TransliterateRequest) =>
    request<JobStatusResponse>('/api/text/transliterate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  suggestTitle: (text: string, language: string) =>
    request<TitleResponse>('/api/text/title', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, language }),
    }),

  // pronunciation dictionary
  //
  // The list deliberately includes DISABLED entries: a disabled entry whose
  // key matches a shipped default is the only way to switch that default off,
  // so filtering them here would hide the mechanism.
  pronunciations: (language?: string) =>
    request<PronunciationList>(
      language ? `/api/pronunciations?language=${encodeURIComponent(language)}` : '/api/pronunciations',
    ),
  createPronunciation: (body: PronunciationCreate) =>
    request<PronunciationItem>('/api/pronunciations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  updatePronunciation: (id: number, body: PronunciationUpdate) =>
    request<PronunciationItem>(`/api/pronunciations/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  deletePronunciation: (id: number) =>
    request<void>(`/api/pronunciations/${id}`, { method: 'DELETE' }),
};
