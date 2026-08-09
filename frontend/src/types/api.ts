// API types — mirror the backend pydantic schemas (app/api/schemas/**).
// Hand-written for now; the plan is to generate these from OpenAPI later.

export interface RouteInfo {
  model_id: string;
  model_display_name: string;
  transform: string; // "none" | "roman_to_deva" | "arab_to_deva"
  lossy: boolean;
  rationale: string;
  source_script: string; // "latin" | "arabic" | "devanagari" | ...
  alternatives: string[];
}

export interface VoiceProfile {
  id: number;
  name: string;
  language: string;
  transcript: string | null;
  duration_sec: number | null;
  sample_rate: number;
  is_active: boolean;
  audio_url: string;
  peak_dbfs: number | null;
  is_clipped: boolean;
  created_at: string;
  updated_at: string;
}

export interface VoiceProfileList {
  profiles: VoiceProfile[];
  total: number;
}

export interface LanguageSupportInfo {
  language: string;
  script: string;
  verified: boolean;
  cer: number | null;
  speaker_cosine: number | null;
}

export interface ModelSummary {
  id: string;
  display_name: string;
  runtime: string;
  license: string;
  languages: LanguageSupportInfo[];
  state: string; // resident | warm | cold | not_downloaded
  est_wait_sec: number;
  vram_mb: number;
  est_rtf: number | null;
  params: Record<string, Record<string, unknown>>;
  needs_reference_text: boolean;
  reference_max_sec: number | null;
  notes: string;
}

export interface ModelListResponse {
  models: ModelSummary[];
  vram_budget_mb: number;
  max_workers: number;
}

export interface LanguageInfo {
  code: string;
  display_name: string;
  native_name: string;
  scripts: string[];
  model_ids: string[];
  requires_transform: boolean;
}

export interface LanguageListResponse {
  languages: LanguageInfo[];
}

export interface TTSGenerateRequest {
  text: string;
  profile_id: number;
  language: string;
  model_id?: string | null;
  urdu_strategy?: string;
  output_format?: string;
  speed?: number;
  stability?: number;
  params?: Record<string, number | string | boolean>;
}

export interface TTSGenerateResponse {
  id: number;
  audio_url: string;
  duration_sec: number | null;
  gen_time_sec: number;
  rtf: number | null;
  language: string;
  route: RouteInfo;
  created_at: string;
}

export interface ScriptDetectResponse {
  script: string;
  script_ratios: Record<string, number>;
  is_rtl: boolean;
  routable: boolean;
  hint: string | null;
  would_route_to: RouteInfo | null;
}

export interface HistoryItem {
  id: number;
  profile_id: number;
  profile_name: string | null;
  input_text: string;
  language: string;
  audio_url: string;
  output_format: string;
  duration_sec: number | null;
  gen_time_sec: number | null;
  is_favorite: boolean;
  route: RouteInfo;
  created_at: string;
}

export interface HistoryList {
  items: HistoryItem[];
  total: number;
  page: number;
  page_size: number;
}

/** RFC 9457 problem+json, as stored on a failed job — same shape as ProblemJson. */
export type JobError = ProblemJson;

export type JobKind = 'synthesize';
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';

/**
 * One job's state — the SAME shape whether it just came back from
 * `POST /api/generate` (202, still 'queued'), a poll of
 * `GET /api/jobs/{id}`, or an item inside `GET /api/jobs`' Recent list.
 *
 * `status` here is domain state, never transport state (see the backend's
 * `JobStatusResponse` docstring) — a bad job id is a 404 problem+json
 * response, never a 200 with an `error` field.
 */
export interface JobStatusResponse {
  id: number;
  kind: JobKind;
  status: JobStatus;
  profile_id: number | null;
  profile_name: string | null;
  input_text: string | null;
  /** Never absent from 'queued' onward — routing is pure and already ran. */
  route: RouteInfo;
  /** 0-indexed jobs strictly ahead of this one. Only set while 'queued'. */
  position: number | null;
  /** Seconds. A UI estimate, not a promise. Set while 'queued' or 'running'. */
  eta_sec: number | null;
  /** Set once 'succeeded'. Same shape the old synchronous /generate returned. */
  result: TTSGenerateResponse | null;
  /** Set once 'failed'. */
  error: JobError | null;
  /** True only once a 'succeeded' job's audio came from the fake runtime. */
  is_fake: boolean;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobList {
  items: JobStatusResponse[];
  total: number;
  page: number;
  page_size: number;
}

export interface GPUInfo {
  available: boolean;
  name: string | null;
  total_mb: number | null;
  free_mb: number | null;
  compute_capability: string | null;
  driver_version: string | null;
  temperature_c: number | null;
}

export interface SystemStatus {
  version: string;
  gpu: GPUInfo;
  resident_models: string[];
  workers_alive: number;
  fake_runtime_enabled: boolean;
}

/** RFC 9457 problem+json error body. */
export interface ProblemJson {
  type: string;
  title: string;
  status: number;
  detail: string;
  code: string;
  [k: string]: unknown;
}
