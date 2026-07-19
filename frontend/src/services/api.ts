/* ═══════════════════════════════════════════════════════════
   AI Voice Clone Studio — API Client
   ═══════════════════════════════════════════════════════════ */

import type {
  VoiceProfile,
  TTSGenerateRequest,
  TTSGenerateResult,
  HistoryItem,
  LanguageInfo,
  EngineInfo,
  SystemStatus,
} from '../types';

const API_BASE = 'http://127.0.0.1:8000';

// ── Generic fetch wrapper ──

async function apiFetch<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const url = `${API_BASE}${path}`;

  try {
    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail?.message || error.detail || `API error: ${response.status}`);
    }

    return response.json();
  } catch (error) {
    if (error instanceof TypeError && error.message.includes('fetch')) {
      throw new Error('Cannot connect to backend. Is the Python server running?');
    }
    throw error;
  }
}

// ── Voice Profile API ──

export const voiceApi = {
  async uploadVoice(file: Blob, name: string, transcript?: string, language = 'en'): Promise<VoiceProfile> {
    const formData = new FormData();
    formData.append('file', file, 'recording.wav');
    formData.append('name', name);
    if (transcript) formData.append('transcript', transcript);
    formData.append('language', language);

    const res = await fetch(`${API_BASE}/api/voice/upload`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    return data.profile;
  },

  async saveRecording(blob: Blob, name: string, transcript?: string, language = 'en'): Promise<VoiceProfile> {
    const formData = new FormData();
    formData.append('file', blob, 'recording.webm');
    formData.append('name', name);
    if (transcript) formData.append('transcript', transcript);
    formData.append('language', language);

    const res = await fetch(`${API_BASE}/api/voice/record`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    return data.profile;
  },

  async listProfiles(): Promise<VoiceProfile[]> {
    const data = await apiFetch<{ profiles: VoiceProfile[] }>('/api/voice/profiles');
    return data.profiles;
  },

  async getProfile(id: number): Promise<VoiceProfile> {
    const data = await apiFetch<{ profile: VoiceProfile }>(`/api/voice/profiles/${id}`);
    return data.profile;
  },

  async deleteProfile(id: number): Promise<void> {
    await apiFetch(`/api/voice/profiles/${id}`, { method: 'DELETE' });
  },

  getAudioUrl(profileId: number): string {
    return `${API_BASE}/api/voice/profiles/${profileId}/audio`;
  },
};

// ── TTS API ──

export const ttsApi = {
  async generate(request: TTSGenerateRequest): Promise<TTSGenerateResult> {
    const data = await apiFetch<{ result: TTSGenerateResult }>('/api/tts/generate', {
      method: 'POST',
      body: JSON.stringify(request),
    });
    return data.result;
  },

  async getLanguages(): Promise<LanguageInfo[]> {
    const data = await apiFetch<{ languages: LanguageInfo[] }>('/api/tts/languages');
    return data.languages;
  },

  getGeneratedAudioUrl(historyId: number): string {
    return `${API_BASE}/api/tts/generate/${historyId}/audio`;
  },
};

// ── History API ──

export const historyApi = {
  async list(page = 1, pageSize = 20): Promise<{ items: HistoryItem[]; total: number }> {
    return apiFetch(`/api/history?page=${page}&page_size=${pageSize}`);
  },

  async delete(id: number): Promise<void> {
    await apiFetch(`/api/history/${id}`, { method: 'DELETE' });
  },

  async toggleFavorite(id: number): Promise<boolean> {
    const data = await apiFetch<{ is_favorite: boolean }>(`/api/history/${id}/favorite`, {
      method: 'PATCH',
    });
    return data.is_favorite;
  },

  getAudioUrl(historyId: number): string {
    return `${API_BASE}/api/history/${historyId}/audio`;
  },
};

// ── Settings & System API ──

export const systemApi = {
  async getStatus(): Promise<SystemStatus> {
    return apiFetch('/api/system/status');
  },

  async getModels(): Promise<EngineInfo[]> {
    const data = await apiFetch<{ engines: EngineInfo[] }>('/api/models');
    return data.engines;
  },

  async getSettings(): Promise<Record<string, { value: string; category: string }>> {
    const data = await apiFetch<{ settings: Record<string, { value: string; category: string }> }>('/api/settings');
    return data.settings;
  },

  async updateSettings(updates: Record<string, string>): Promise<void> {
    await apiFetch('/api/settings', {
      method: 'PUT',
      body: JSON.stringify(updates),
    });
  },
};
