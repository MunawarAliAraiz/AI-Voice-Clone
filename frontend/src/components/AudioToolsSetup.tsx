import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../services/api';
import './AudioToolsSetup.css';

export function AudioToolsSetup() {
  const cache = useQueryClient();
  const status = useQuery({ queryKey: ['audio-tools'], queryFn: api.audioToolsStatus,
    enabled: !!window.__VCS_DESKTOP_KEY__,
    refetchInterval: (query) => query.state.data?.ready ? false : 1500,
  });
  const retry = useMutation({ mutationFn: api.retryAudioTools,
    onSuccess: (data) => { cache.setQueryData(['audio-tools'], data); },
  });
  if (!window.__VCS_DESKTOP_KEY__ || status.data?.ready) return null;
  const data = status.data;
  const failed = data?.stage === 'failed' || data?.stage === 'cancelled' || status.isError;
  const percent = Math.max(0, Math.min(100, data?.progress_pct ?? 0));
  return <section className="audio-tools-setup" role="status" aria-label="Local audio tools setup">
    <div>
      <strong>{failed ? 'Audio tools need setup' : 'Preparing audio tools'}</strong>
      <p>{status.error?.message ?? data?.detail ?? 'Checking local audio tools.'}</p>
      {!failed && data?.stage === 'downloading' && <>
        <progress max={100} value={percent} aria-label="Audio tools download progress" />
        <span>{(data.bytes_completed / 1_000_000).toFixed(1)} / {(data.bytes_total / 1_000_000).toFixed(1)} MB · {Math.round(percent)}%</span>
      </>}
      {!failed && data?.stage !== 'downloading' && <progress aria-label="Verifying local audio tools" />}
      <small>One local download from the publisher. Audio import, editing and generation unlock when ready.</small>
      {retry.error && <p role="alert">{retry.error.message}</p>}
    </div>
    {failed && <button className="btn sm" type="button" disabled={retry.isPending}
      onClick={() => retry.mutate()}>{retry.isPending ? 'Starting…' : 'Retry setup'}</button>}
  </section>;
}
