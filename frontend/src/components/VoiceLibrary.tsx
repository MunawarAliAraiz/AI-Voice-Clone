/**
 * Enrolled reference voices.
 *
 * The predecessor deleted on a single click with no confirmation and no
 * `.catch`, so a failed delete looked exactly like a successful one.
 */
import { useState } from 'react';
import { api, ApiError, mediaUrl } from '../services/api';
import type { VoiceProfile } from '../types/api';
import { AudioPlayer } from './AudioPlayer';
import { IconAlert, IconSpinner, IconTrash } from './icons';
import { fmtDuration, fmtSampleRate } from '../lib/format';

interface Props {
  voices: VoiceProfile[];
  onDeleted: () => void;
}

export function VoiceLibrary({ voices, onDeleted }: Props) {
  const [confirming, setConfirming] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function remove(id: number) {
    setDeleting(id);
    setError(null);
    try {
      await api.deleteVoice(id);
      setConfirming(null);
      onDeleted();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setDeleting(null);
    }
  }

  return (
    <section className="card" aria-labelledby="voices-h">
      <header className="card-head">
        <h2 id="voices-h">
          Voices
          {voices.length > 0 && <span className="count">{voices.length}</span>}
        </h2>
      </header>

      {error && (
        <div className="inline-error" role="alert">
          <IconAlert size={14} /> {error}
        </div>
      )}

      {voices.length === 0 ? (
        <p className="empty-body pad">
          No voices yet. Add one above — upload a clean 6–15 second clip, or record straight from
          your mic.
        </p>
      ) : (
        <ul className="voice-list">
          {voices.map((v, i) => (
            <li key={v.id} style={{ animationDelay: `${Math.min(i, 8) * 30}ms` }}>
              <div className="v-head">
                <div className="v-name">
                  <span className="avatar" aria-hidden="true">
                    {v.name.trim().charAt(0).toUpperCase() || '?'}
                  </span>
                  <div>
                    <strong>{v.name}</strong>
                    <div className="v-meta">
                      <span>{fmtDuration(v.duration_sec)}</span>
                      <span className="dot" />
                      <span>{fmtSampleRate(v.sample_rate)}</span>
                      {v.is_clipped && (
                        <>
                          <span className="dot" />
                          <span className="tag warn" title="Peaks hit 0 dBFS — may distort">
                            clipped
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                </div>

                <button
                  type="button"
                  className="icon-btn danger"
                  aria-label={`Delete voice ${v.name}`}
                  onClick={() => setConfirming(v.id)}
                >
                  <IconTrash size={15} />
                </button>
              </div>

              {confirming === v.id ? (
                <div className="confirm" role="alert">
                  <span>Delete “{v.name}”?</span>
                  <div className="confirm-actions">
                    <button
                      type="button"
                      className="btn-sm danger"
                      disabled={deleting === v.id}
                      onClick={() => void remove(v.id)}
                    >
                      {deleting === v.id ? <IconSpinner size={13} /> : null}
                      {deleting === v.id ? 'Deleting…' : 'Delete'}
                    </button>
                    <button
                      type="button"
                      className="btn-sm"
                      onClick={() => setConfirming(null)}
                      disabled={deleting === v.id}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <AudioPlayer compact src={mediaUrl(v.audio_url)} label={v.name} />
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
