import { useState, useEffect } from 'react';
import type { SystemStatus, HistoryItem } from '../types';
import { historyApi } from '../services/api';
import { useNavigate } from 'react-router-dom';
import './DashboardPage.css';

interface Props {
  status: SystemStatus | null;
}

export default function DashboardPage({ status }: Props) {
  const navigate = useNavigate();
  const [recentHistory, setRecentHistory] = useState<HistoryItem[]>([]);

  useEffect(() => {
    historyApi.list(1, 5).then(data => setRecentHistory(data.items)).catch(() => {});
  }, []);

  return (
    <div className="dashboard animate-fade-in">
      <div className="dashboard-header">
        <h1>Welcome Back</h1>
        <p className="dashboard-subtitle">AI Voice Clone Studio</p>
      </div>

      {/* Stats Grid */}
      <div className="dashboard-stats">
        <div className="stat-card card">
          <div className="stat-icon">🎙️</div>
          <div className="stat-info">
            <span className="stat-value">{status?.profiles_count ?? 0}</span>
            <span className="stat-label">Voice Profiles</span>
          </div>
        </div>
        <div className="stat-card card">
          <div className="stat-icon">🔊</div>
          <div className="stat-info">
            <span className="stat-value">{status?.generations_count ?? 0}</span>
            <span className="stat-label">Generations</span>
          </div>
        </div>
        <div className="stat-card card">
          <div className="stat-icon">🎮</div>
          <div className="stat-info">
            <span className="stat-value">{status?.gpu_available ? '✓ GPU' : 'CPU'}</span>
            <span className="stat-label">{status?.gpu_name ?? 'No GPU detected'}</span>
          </div>
        </div>
        <div className="stat-card card">
          <div className="stat-icon">🤖</div>
          <div className="stat-info">
            <span className="stat-value">{status?.active_engine ?? 'mock'}</span>
            <span className="stat-label">Active Engine</span>
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <div className="dashboard-actions">
        <h2>Quick Actions</h2>
        <div className="action-grid">
          <button className="action-card card" onClick={() => navigate('/record')}>
            <span className="action-icon">🎙️</span>
            <span className="action-title">Record Voice</span>
            <span className="action-desc">Record your voice to create a new profile</span>
          </button>
          <button className="action-card card" onClick={() => navigate('/generate')}>
            <span className="action-icon">🔊</span>
            <span className="action-title">Generate Speech</span>
            <span className="action-desc">Convert text to speech with your cloned voice</span>
          </button>
          <button className="action-card card" onClick={() => navigate('/history')}>
            <span className="action-icon">📜</span>
            <span className="action-title">View History</span>
            <span className="action-desc">Browse and replay past generations</span>
          </button>
        </div>
      </div>

      {/* Recent History */}
      {recentHistory.length > 0 && (
        <div className="dashboard-recent">
          <h2>Recent Generations</h2>
          <div className="recent-list">
            {recentHistory.map(item => (
              <div key={item.id} className="recent-item card">
                <div className="recent-item-info">
                  <span className="recent-item-text">{item.input_text.slice(0, 80)}{item.input_text.length > 80 ? '…' : ''}</span>
                  <span className="recent-item-meta">
                    {item.engine} · {item.language} · {new Date(item.created_at).toLocaleString()}
                  </span>
                </div>
                <audio
                  controls
                  src={historyApi.getAudioUrl(item.id)}
                  className="recent-item-audio"
                />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
