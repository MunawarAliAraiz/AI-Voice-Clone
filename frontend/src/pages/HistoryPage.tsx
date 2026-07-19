import { useState, useEffect } from 'react';
import { historyApi } from '../services/api';
import type { HistoryItem } from '../types';
import './HistoryPage.css';

export default function HistoryPage() {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const pageSize = 15;

  const loadHistory = async () => {
    try {
      const data = await historyApi.list(page, pageSize);
      setItems(data.items);
      setTotal(data.total);
    } catch { /* backend offline */ }
  };

  useEffect(() => { loadHistory(); }, [page]);

  const handleDelete = async (id: number) => {
    await historyApi.delete(id);
    loadHistory();
  };

  const handleFavorite = async (id: number) => {
    const newStatus = await historyApi.toggleFavorite(id);
    setItems(prev => prev.map(item =>
      item.id === id ? { ...item, is_favorite: newStatus } : item
    ));
  };

  const totalPages = Math.ceil(total / pageSize);

  const languageNames: Record<string, string> = { en: 'English', ur: 'Urdu', hi: 'Hindi' };

  return (
    <div className="history-page animate-fade-in">
      <div className="history-header">
        <h1>Generation History</h1>
        <span className="badge badge-accent">{total} total</span>
      </div>

      {items.length === 0 ? (
        <div className="empty-state">
          <h3>No generations yet</h3>
          <p>Go to the Generate page to create your first voice clone.</p>
        </div>
      ) : (
        <>
          <div className="history-list">
            {items.map(item => (
              <div key={item.id} className="card history-item stagger-item">
                <div className="history-item-top">
                  <div className="history-item-text">
                    {item.input_text.slice(0, 120)}
                    {item.input_text.length > 120 ? '…' : ''}
                  </div>
                  <div className="history-item-actions">
                    <button
                      className={`btn btn-ghost btn-icon ${item.is_favorite ? 'favorite-active' : ''}`}
                      onClick={() => handleFavorite(item.id)}
                      title="Toggle favorite"
                    >
                      {item.is_favorite ? '⭐' : '☆'}
                    </button>
                    <button
                      className="btn btn-ghost btn-icon"
                      onClick={() => handleDelete(item.id)}
                      title="Delete"
                    >
                      🗑️
                    </button>
                  </div>
                </div>

                <div className="history-item-meta">
                  <span className="badge badge-accent">{languageNames[item.language] ?? item.language}</span>
                  <span className="badge badge-success">{item.engine}</span>
                  {item.profile_name && <span className="history-profile">{item.profile_name}</span>}
                  {item.duration_sec && <span>{item.duration_sec.toFixed(1)}s</span>}
                  {item.gen_time_sec && <span>in {item.gen_time_sec.toFixed(2)}s</span>}
                  <span>{new Date(item.created_at).toLocaleString()}</span>
                </div>

                <audio controls src={historyApi.getAudioUrl(item.id)} className="history-audio" />
              </div>
            ))}
          </div>

          {totalPages > 1 && (
            <div className="history-pagination">
              <button className="btn btn-secondary" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>
                ← Previous
              </button>
              <span className="pagination-info">Page {page} of {totalPages}</span>
              <button className="btn btn-secondary" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
