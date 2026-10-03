import { cloneElement, useId, useState } from 'react';
import type { ReactElement } from 'react';
import type { GenerationGate } from '../lib/cloudGenerationGate';
import './CloudGenerationGate.css';

export function CloudGenerationGate({ gate, onOpenRunpod, onOpenUpdates }: {
  gate: GenerationGate; onOpenRunpod?: () => void; onOpenUpdates?: () => void;
}) {
  if (!gate.blocked) return null;
  const open = () => {
    const callback = gate.action === 'updates' ? onOpenUpdates : onOpenRunpod;
    if (callback) callback();
    else window.dispatchEvent(new CustomEvent(gate.action === 'updates' ? 'vcs-open-updates' : 'vcs-open-runpod'));
  };
  return <div className="cloud-generation-gate" role="status">
    <div><strong>Generation unavailable</strong><p>{gate.reason}</p></div>
    {gate.actionLabel && <button type="button" className="btn sm" onClick={open}>{gate.actionLabel}</button>}
  </div>;
}

/** A native disabled button plus a focusable explanation for keyboard/touch users. */
export function DisabledAction({ reason, children, className = '' }: {
  reason: string | null; className?: string;
  children: ReactElement<{ disabled?: boolean; title?: string; 'aria-describedby'?: string }>;
}) {
  const id = useId();
  const [position, setPosition] = useState<React.CSSProperties>({});
  const placeTooltip = (element: HTMLElement) => {
    const bounds = element.getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 32);
    const left = Math.max(16, Math.min(bounds.left, window.innerWidth - width - 16));
    setPosition({ width, left, ...(bounds.top < 180
      ? { top: bounds.bottom + 8, bottom: 'auto' }
      : { bottom: window.innerHeight - bounds.top + 8, top: 'auto' }) });
  };
  return <span className={`disabled-action ${reason ? 'is-disabled' : ''} ${className}`}
    tabIndex={reason ? 0 : undefined} role={reason ? 'group' : undefined}
    aria-label={reason ? 'Unavailable action' : undefined}
    aria-describedby={reason ? id : undefined}
    onMouseEnter={reason ? event => placeTooltip(event.currentTarget) : undefined}
    onFocus={reason ? event => placeTooltip(event.currentTarget) : undefined}
    onClick={reason ? event => event.currentTarget.focus() : undefined}>
    {reason ? cloneElement(children, {
      disabled: true, title: reason,
      'aria-describedby': [children.props['aria-describedby'], id].filter(Boolean).join(' '),
    }) : children}
    {reason && <span id={id} role="tooltip" className="disabled-action-tooltip" style={position}>{reason}</span>}
  </span>;
}
