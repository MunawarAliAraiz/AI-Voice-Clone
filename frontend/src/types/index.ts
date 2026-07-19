/* ═══════════════════════════════════════════════════════════
   AI Voice Clone Studio — TypeScript Interfaces
   ═══════════════════════════════════════════════════════════ */

// ── Voice Profiles ──

export interface VoiceProfile {
  id: number;
  name: string;
  audio_path: string;
  transcript: string | null;
  language: string;
  duration_sec: number | null;
  sample_rate: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

// ── TTS Generation ──

export interface TTSGenerateRequest {
  text: string;
  profile_id: number;
  language: string;
  engine: string;
  output_format: string;
}

export interface TTSGenerateResult {
  id: number;
  output_path: string;
  duration_sec: number | null;
  gen_time_sec: number;
  engine: string;
  language: string;
}

export interface LanguageInfo {
  code: string;
  name: string;
  engines: string[];
}

// ── History ──

export interface HistoryItem {
  id: number;
  profile_id: number;
  profile_name: string | null;
  input_text: string;
  language: string;
  engine: string;
  output_path: string;
  output_format: string;
  duration_sec: number | null;
  gen_time_sec: number | null;
  is_favorite: boolean;
  created_at: string;
}

// ── Engine / Model Info ──

export interface EngineInfo {
  name: string;
  display_name: string;
  version: string;
  description: string;
  supported_languages: string[];
  requires_gpu: boolean;
  model_size_mb: number;
  is_loaded: boolean;
}

// ── System ──

export interface SystemStatus {
  status: string;
  version: string;
  gpu_available: boolean;
  gpu_name: string | null;
  gpu_vram_mb: number | null;
  cuda_version: string | null;
  active_engine: string;
  profiles_count: number;
  generations_count: number;
}

// ── UI State ──

export type PageName = 'dashboard' | 'record' | 'generate' | 'history' | 'settings';

export interface Toast {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
  duration?: number;
}
