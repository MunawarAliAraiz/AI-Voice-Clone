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
  experimental: boolean;
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
  experimental: boolean;
  state: string; // resident | warm | cold | not_downloaded
  est_wait_sec: number;
  vram_mb: number;
  est_rtf: number | null;
  params: Record<string, Record<string, unknown>>;
  needs_reference_text: boolean;
  reference_max_sec: number | null;
  /** The one user-facing sentence for an experimental model. Empty otherwise. */
  caveat: string;
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

/**
 * Mirrors backend `DirectedSegmentIn`. One segment's prosody OVERRIDE, keyed
 * by `index` from the last `GET /api/direction/analyze` (`DirectionAnalyzeResponse.plan`)
 * for the current text. Deliberately has no `text` or `tone` field: the
 * Advanced editor can change how a segment is delivered, never what text it
 * contains, and no current runtime honors `tone` (same reason the Composer's
 * model picker shipped without a Tone control) — this must stay that way.
 */
export interface DirectedSegmentIn {
  index: number;
  emotion: string; // "neutral" | "happy" | "sad" | "anxious" | "angry" | "excited" | "calm" | "serious" | "questioning"
  intensity: string; // "low" | "medium" | "high"
  energy: string; // "low" | "medium" | "high"
  rate: string; // "slow" | "normal" | "fast"
  pause_after_ms: number; // 0-5000
}

/**
 * Mirrors backend `DirectionPlanIn`. SPARSE — only segments the user actually
 * edited belong here; any segment index not present keeps the analyzer's own
 * value server-side. Submitting an index the server's fresh `analyze()` of
 * the current text doesn't have (most often: the text changed after the
 * editor fetched its plan) is a 422 `INVALID_DIRECTION_PLAN`.
 */
export interface DirectionPlanIn {
  segments: DirectedSegmentIn[];
}

export interface TTSGenerateRequest {
  text: string;
  profile_id: number;
  language: string;
  model_id?: string | null;
  /** Required alongside model_id to route to a model that hasn't passed its
   *  own accuracy gate (e.g. Chatterbox). Ignored when model_id is unset. */
  allow_experimental?: boolean;
  urdu_strategy?: string;
  output_format?: string;
  speed?: number;
  stability?: number;
  params?: Record<string, number | string | boolean>;
  /** Apply Speech Direction: analyze into per-segment prosody and render each
   *  segment separately, joined with real inter-segment pauses. Only fields
   *  the routed model HONORS/APPROXIMATES take effect (see DirectionPanel's
   *  capability chip) — never a silent no-op. Defaults false. */
  apply_direction?: boolean;
  /** Only read when apply_direction is true. Per-segment prosody overrides
   *  from the Advanced editor, keyed by segment index from the last analyze()
   *  call for this exact text. Omit/null to use the analyzer's own values
   *  unedited. */
  direction_plan?: DirectionPlanIn | null;
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
  /** 1 for a normal generation; >1 when Speech Direction rendered this clip
   *  from several separately-synthesized, pause-joined segments. */
  segment_count: number;
}

export interface ScriptDetectResponse {
  script: string;
  script_ratios: Record<string, number>;
  is_rtl: boolean;
  routable: boolean;
  hint: string | null;
  would_route_to: RouteInfo | null;
}

/** Mirrors backend `EmphasisSpanOut`. Offsets into the segment's text. */
export interface EmphasisSpan {
  start: number;
  end: number;
}

/** Mirrors backend `DirectedSegmentOut` field-for-field. */
export interface DirectedSegment {
  text: string;
  index: number;
  emotion: string; // "neutral" | "happy" | "sad" | "angry" | "excited" | "calm" | "serious" | "questioning"
  tone: string; // "neutral" | "warm" | "firm" | "soft"
  intensity: string; // "low" | "medium" | "high"
  energy: string; // "low" | "medium" | "high"
  rate: string; // "slow" | "normal" | "fast"
  emphasis: EmphasisSpan[];
  pause_after_ms: number;
}

/** Mirrors backend `DirectionSummaryOut`. Derived from `segments`. */
export interface DirectionSummary {
  emotion: string;
  intensity: string;
  rate: string;
}

/** Mirrors backend `DirectionPlanOut` field-for-field. */
export interface DirectionPlan {
  language: string;
  source_script: string; // "latin" | "arabic" | "devanagari" | ...
  segments: DirectedSegment[];
  summary: DirectionSummary;
}

/** Mirrors backend `FieldCapabilityOut`. One row of the honesty chip. */
export interface FieldCapability {
  field: string; // "segmentation" | "pause_after" | "rate" | "emphasis" | "intensity" | "emotion" | "tone" | "energy"
  support: string; // "honored" | "approximated" | "ignored"
  rationale: string;
}

/** Mirrors backend `CapabilityReportOut`. What the routed model does with each `DirectionPlan` field. */
export interface CapabilityReport {
  model_id: string;
  model_display_name: string;
  fields: FieldCapability[];
}

/** `POST /api/direction/analyze`'s body — mirrors backend `DirectionAnalyzeResponse`. */
export interface DirectionAnalyzeResponse {
  plan: DirectionPlan;
  capability: CapabilityReport;
  route: RouteInfo;
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

export type JobKind = 'synthesize' | 'analyze_llm';
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';

/**
 * One row of the LLM analyzer's `result.rows` (`jobs/handlers/analyze_llm.py`,
 * `AnalyzeResult` in `inference/protocol.py`). Classification only — no
 * `text`/`pause_after_ms`, those come from the heuristic `analyze()` plan at
 * the same `index` (see `AnalyzeLlmResult`'s docstring below).
 */
export interface AnalyzeLlmRow {
  index: number;
  emotion: string; // same value set as DirectedSegmentIn.emotion
  intensity: string; // "low" | "medium" | "high"
  energy: string; // "low" | "medium" | "high"
  rate: string; // "slow" | "normal" | "fast"
}

/**
 * Mirrors the `analyze_llm` job's opaque `result` dict verbatim
 * (`JobStatusResponse.result` in `api/schemas/jobs.py`: `{"rows": [...],
 * "gen_time_sec": ..., "load_time_sec": ...}`). `rows` is indexed the same
 * way as the heuristic `DirectionAnalyzeResponse.plan.segments` for the SAME
 * text — the caller must pair `rows[i]` with `plan.segments[i]` to get a full
 * `DirectedSegmentIn` (text + pause_after_ms the LLM never classifies).
 */
export interface AnalyzeLlmResult {
  rows: AnalyzeLlmRow[];
  gen_time_sec: number;
  load_time_sec: number;
}

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
  /** Never absent from 'queued' onward for kinds that route through the audio
   *  catalog (currently only 'synthesize') — routing is pure and already ran.
   *  `null` for kinds that never touch `resolve()`/the audio catalog at all
   *  (currently only 'analyze_llm': the Qwen Speech Direction analyzer is not
   *  audio and is not a routable ModelSpec). */
  route: RouteInfo | null;
  /** 0-indexed jobs strictly ahead of this one. Only set while 'queued'. */
  position: number | null;
  /** Seconds. A UI estimate, not a promise. Set while 'queued' or 'running'. */
  eta_sec: number | null;
  /** Set once 'succeeded'. Same shape the old synchronous /generate returned
   *  for 'synthesize'; for 'analyze_llm' it's the handler's opaque result dict
   *  (`AnalyzeLlmResult`) instead — no `generation_history` row, no audio. */
  result: TTSGenerateResponse | AnalyzeLlmResult | null;
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
