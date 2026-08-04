/**
 * AI Voice Clone Studio — API wire types.
 *
 * CONTRACT MODULE. Wave 0.
 *
 * ⚠️ STATUS: hand-written, temporarily. The routers do not exist yet, so there
 * is no OpenAPI document to generate from. Once B2 lands the routers, this file
 * becomes GENERATED and hand-editing it stops being allowed:
 *
 *     npm run gen:api      # openapi-typescript -> this file
 *
 * Until then it is the contract F1–F4 build against, and it must stay
 * byte-faithful to `backend/app/api/schemas/`. If you change one side, change
 * both in the same commit — drifting wire types are how a frontend ends up
 * rendering `undefined` in production.
 */

// ── Primitives ───────────────────────────────────────────────────────────────

/** Languages the product exposes. Mirrors `domain.language.LanguageCode`. */
export type LanguageCode = 'ur' | 'hi' | 'en';

/** Detected writing system. Mirrors `domain.language.Script`. */
export type ScriptName = 'latin' | 'arabic' | 'devanagari' | 'unknown' | 'mixed';

/** Text transformation applied before synthesis. Mirrors `TransformKind`. */
export type TransformKind = 'none' | 'roman_to_deva' | 'arab_to_deva';

/** How Perso-Arabic Urdu is handled. Mirrors `UrduStrategy`. */
export type UrduStrategy = 'native' | 'translit';

/** Residency of a model. Mirrors `inference.spec.ModelState`. */
export type ModelState = 'resident' | 'warm' | 'cold' | 'not_downloaded';

export type RuntimeKind = 'f5' | 'chatterbox' | 'voxcpm' | 'fake';

/** Only permissive licenses ship. */
export type LicenseId = 'MIT' | 'Apache-2.0' | 'CC-BY-SA-4.0';

export type OutputFormat = 'wav' | 'mp3';

// ── Errors: RFC 9457 problem+json ────────────────────────────────────────────

/**
 * Stable machine-readable error codes. Branch on these, never on `detail`,
 * which is prose and may be reworded at any time.
 */
export type ProblemCode =
  | 'VALIDATION_ERROR'
  | 'AUDIO_VALIDATION_ERROR'
  | 'UPLOAD_TOO_LARGE'
  | 'PROFILE_NOT_FOUND'
  | 'HISTORY_NOT_FOUND'
  | 'MODEL_NOT_FOUND'
  | 'NO_ROUTE'
  | 'AMBIGUOUS_SCRIPT'
  | 'INVALID_PARAMS'
  | 'UNAUTHORIZED'
  | 'INVALID_MEDIA_TOKEN'
  | 'QUEUE_FULL'
  | 'VRAM_EXHAUSTED'
  | 'MODEL_LOAD_FAILED'
  | 'MODEL_NOT_DOWNLOADED'
  | 'WORKER_CRASHED'
  | 'GENERATION_TIMEOUT'
  | 'GENERATION_FAILED'
  | 'INTERNAL_ERROR';

/** Base problem document. Every failure response has this shape. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
  code: ProblemCode;
  instance?: string;
}

/**
 * The 422 that replaced silent fallback.
 *
 * `supported` enumerates what WOULD have worked, so the UI can offer a real
 * next step instead of a dead end. Render it; do not swallow it into a generic
 * toast.
 */
export interface NoRouteProblem extends Problem {
  code: 'NO_ROUTE';
  language: string;
  script: ScriptName;
  supported: Array<{ language: LanguageCode; script: ScriptName }>;
  suggestion: string | null;
}

export interface QueueFullProblem extends Problem {
  code: 'QUEUE_FULL';
  limit: number;
  retry_after_sec: number;
}

export interface InvalidParamsProblem extends Problem {
  code: 'INVALID_PARAMS';
  model_id: string;
  unknown: string[];
  accepted: string[];
}

export function isProblem(value: unknown): value is Problem {
  return (
    typeof value === 'object' &&
    value !== null &&
    'code' in value &&
    'status' in value &&
    'title' in value
  );
}

// ── Routing ──────────────────────────────────────────────────────────────────

/**
 * What actually happened to the request.
 *
 * Rendered as a chip beside the player. This is not debug output — it is the
 * mechanism by which a user is never left wondering why their Urdu came out
 * sounding like Hindi. Do not hide it behind a "details" disclosure.
 */
export interface RouteInfo {
  model_id: string;
  model_display_name: string;
  transform: TransformKind;
  /** True when the transform may have altered pronunciation. Show a warning. */
  lossy: boolean;
  /** User-facing prose. Display verbatim; do not template over it. */
  rationale: string;
  source_script: ScriptName;
  alternatives: string[];
}

// ── Models ───────────────────────────────────────────────────────────────────

export interface LanguageSupportInfo {
  language: LanguageCode;
  script: ScriptName;
  verified: boolean;
  cer: number | null;
  speaker_cosine: number | null;
}

/** JSON Schema fragment for one model-specific parameter. */
export interface ParamSchema {
  type: 'number' | 'integer' | 'string' | 'boolean';
  title?: string;
  minimum?: number;
  maximum?: number;
  default?: number | string | boolean;
  enum?: Array<string | number>;
}

export interface ModelSummary {
  id: string;
  display_name: string;
  runtime: RuntimeKind;
  license: LicenseId;
  languages: LanguageSupportInfo[];
  state: ModelState;
  /** Seconds before audio starts. Show this BEFORE the click: "cold, ~40s". */
  est_wait_sec: number;
  vram_mb: number;
  est_rtf: number | null;
  /** Render only the controls the selected model declares. No dead knobs. */
  params: Record<string, ParamSchema>;
  needs_reference_text: boolean;
  /** Runtime hard-trims the reference to this length. Drives the trim editor. */
  reference_max_sec: number | null;
  notes: string;
}

export interface ModelListResponse {
  models: ModelSummary[];
  vram_budget_mb: number;
  max_workers: number;
}

export interface LanguageInfo {
  code: LanguageCode;
  display_name: string;
  native_name: string;
  scripts: ScriptName[];
  model_ids: string[];
  requires_transform: boolean;
}

export interface LanguageListResponse {
  languages: LanguageInfo[];
}

// ── Voice profiles ───────────────────────────────────────────────────────────

export interface VoiceProfile {
  id: number;
  name: string;
  language: LanguageCode;
  transcript: string | null;
  duration_sec: number | null;
  sample_rate: number;
  is_active: boolean;
  /** Signed and expiring. Never construct a media URL client-side. */
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

export interface VoiceProfileUpdate {
  name?: string;
  transcript?: string;
  is_active?: boolean;
}

// ── Generation ───────────────────────────────────────────────────────────────

export interface TTSGenerateRequest {
  text: string;
  profile_id: number;
  /**
   * REQUIRED. The user declares the language; the server detects the script.
   * `{ language: 'ur', script: 'latin' }` IS Roman Urdu, unambiguously.
   */
  language: LanguageCode;
  /** Pin a model. Honored if it can serve the text, else 422 — never swapped. */
  model_id?: string;
  urdu_strategy?: UrduStrategy;
  output_format?: OutputFormat;
  params?: Record<string, number | string | boolean>;
}

export interface TTSGenerateResponse {
  id: number;
  audio_url: string;
  duration_sec: number | null;
  gen_time_sec: number;
  rtf: number | null;
  language: LanguageCode;
  /** Never optional. Every generation states what produced it. */
  route: RouteInfo;
  created_at: string;
}

export interface ScriptDetectRequest {
  text: string;
  language: LanguageCode;
}

export interface ScriptDetectResponse {
  script: ScriptName;
  script_ratios: Partial<Record<ScriptName, number>>;
  /** Key `dir="rtl"` off THIS, never off `language === 'ur'` — that wrongly
   *  right-aligns Roman Urdu. */
  is_rtl: boolean;
  routable: boolean;
  hint: string | null;
  would_route_to: RouteInfo | null;
}

// ── History ──────────────────────────────────────────────────────────────────

export interface HistoryItem {
  id: number;
  profile_id: number;
  profile_name: string | null;
  input_text: string;
  language: LanguageCode;
  audio_url: string;
  output_format: OutputFormat;
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

// ── System ───────────────────────────────────────────────────────────────────

export interface GPUInfo {
  available: boolean;
  name: string | null;
  total_mb: number | null;
  free_mb: number | null;
  used_by_workers_mb: number | null;
  compute_capability: string | null;
  driver_version: string | null;
  temperature_c: number | null;
}

export interface SystemStatus {
  version: string;
  gpu: GPUInfo;
  resident_models: string[];
  workers_alive: number;
  queue_depth: number;
  queue_limit: number;
  profiles_count: number;
  generations_count: number;
  /** When true the UI MUST show a loud banner: this audio is not a clone. */
  fake_runtime_enabled: boolean;
}
