import { useState, useEffect } from 'react';
import { systemApi } from '../services/api';
import type { SystemStatus, EngineInfo } from '../types';
import './SettingsPage.css';

interface Props {
  status: SystemStatus | null;
}

export default function SettingsPage({ status }: Props) {
  const [engines, setEngines] = useState<EngineInfo[]>([]);

  useEffect(() => {
    systemApi.getModels().then(setEngines).catch(() => {});
  }, []);

  return (
    <div className="settings-page animate-fade-in">
      <h1>Settings</h1>
      <p className="page-subtitle">Configure your Voice Clone Studio</p>

      {/* System Info */}
      <div className="card settings-section">
        <h2>System Information</h2>
        <div className="info-grid">
          <div className="info-item">
            <span className="info-label">Version</span>
            <span className="info-value">{status?.version ?? '—'}</span>
          </div>
          <div className="info-item">
            <span className="info-label">GPU</span>
            <span className={`info-value ${status?.gpu_available ? 'text-success' : 'text-warning'}`}>
              {status?.gpu_available ? `✓ ${status.gpu_name}` : '✗ No GPU (CPU mode)'}
            </span>
          </div>
          {status?.gpu_vram_mb && (
            <div className="info-item">
              <span className="info-label">VRAM</span>
              <span className="info-value">{status.gpu_vram_mb} MB</span>
            </div>
          )}
          {status?.cuda_version && (
            <div className="info-item">
              <span className="info-label">CUDA</span>
              <span className="info-value">{status.cuda_version}</span>
            </div>
          )}
          <div className="info-item">
            <span className="info-label">Voice Profiles</span>
            <span className="info-value">{status?.profiles_count ?? 0}</span>
          </div>
          <div className="info-item">
            <span className="info-label">Total Generations</span>
            <span className="info-value">{status?.generations_count ?? 0}</span>
          </div>
        </div>
      </div>

      {/* Engines */}
      <div className="card settings-section">
        <h2>TTS Engines</h2>
        <div className="engines-list">
          {engines.map(eng => (
            <div key={eng.name} className="engine-item">
              <div className="engine-header">
                <span className="engine-name">{eng.display_name}</span>
                <span className={`badge ${eng.is_loaded ? 'badge-success' : 'badge-warning'}`}>
                  {eng.is_loaded ? 'Loaded' : 'Not loaded'}
                </span>
              </div>
              <p className="engine-desc">{eng.description}</p>
              <div className="engine-meta">
                <span>v{eng.version}</span>
                <span>{eng.model_size_mb > 0 ? `${(eng.model_size_mb / 1024).toFixed(1)} GB` : 'No download needed'}</span>
                <span>{eng.requires_gpu ? '🎮 GPU Required' : '💻 CPU OK'}</span>
                <span>Languages: {eng.supported_languages.join(', ')}</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* About */}
      <div className="card settings-section">
        <h2>About</h2>
        <p className="about-text">
          AI Voice Clone Studio is a personal desktop application for voice cloning
          and multilingual text-to-speech generation. Built with Tauri, React, Python,
          and open-source AI models from Hugging Face.
        </p>
        <p className="about-text" style={{ color: 'var(--text-tertiary)', marginTop: 'var(--space-2)' }}>
          Made with ❤️ in Pakistan 🇵🇰
        </p>
      </div>
    </div>
  );
}
