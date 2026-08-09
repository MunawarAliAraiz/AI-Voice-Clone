/**
 * A minimal toast stack — the notification the async job queue needed and
 * the codebase didn't have (every other surface uses inline `role="alert"`
 * blocks, which only work while the triggering component is on screen; a job
 * can finish while the user has scrolled away or switched tabs).
 *
 * State lives in `App.tsx` (a plain array, not a global store) and is pushed
 * to from `Composer` when a polled job first reaches a terminal status.
 */
import { useEffect } from 'react';
import { IconAlert, IconCheck, IconX } from './icons';

export interface ToastItem {
  id: number;
  tone: 'success' | 'error';
  message: string;
}

interface Props {
  toasts: ToastItem[];
  onDismiss: (id: number) => void;
}

const AUTO_DISMISS_MS = 5000;

export function ToastStack({ toasts, onDismiss }: Props) {
  if (toasts.length === 0) return null;
  return (
    <div className="toast-stack" role="region" aria-label="Notifications">
      {toasts.map((t) => (
        <Toast key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function Toast({ toast, onDismiss }: { toast: ToastItem; onDismiss: (id: number) => void }) {
  useEffect(() => {
    const h = window.setTimeout(() => onDismiss(toast.id), AUTO_DISMISS_MS);
    return () => window.clearTimeout(h);
  }, [toast.id, onDismiss]);

  return (
    <div className={`toast ${toast.tone}`} role="status">
      {toast.tone === 'success' ? <IconCheck size={15} /> : <IconAlert size={15} />}
      <span className="toast-msg">{toast.message}</span>
      <button
        type="button"
        className="toast-dismiss"
        aria-label="Dismiss notification"
        onClick={() => onDismiss(toast.id)}
      >
        <IconX size={13} />
      </button>
    </div>
  );
}
