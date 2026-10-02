import type { CloudSetup } from '../services/api';

export interface GenerationGate {
  blocked: boolean;
  reason: string;
  action: 'runpod' | 'updates' | null;
  actionLabel: string | null;
}

/** Only verified cloud setup can unlock desktop generation; local API health cannot. */
export function deriveCloudGenerationGate({ desktop, setup, error = false, audioTools }: {
  desktop: boolean; setup?: CloudSetup; error?: boolean;
  audioTools?: { ready: boolean; detail?: string };
}): GenerationGate {
  const block = (reason: string, action: 'runpod' | 'updates' = 'runpod'): GenerationGate => ({
    blocked: true, reason, action, actionLabel: action === 'updates' ? 'Check app updates' : 'Open Runpod',
  });
  const available = { blocked: false, reason: '', action: null, actionLabel: null } as const;
  if (!desktop) return available;
  if (error) return block('Runpod setup could not be checked. Open Runpod and refresh the connection.');
  if (!setup) return block('Checking Runpod setup. Generation unlocks after model storage and compute limits are ready.');
  if (!setup.connected) return block('Connect your Runpod API key to set up voice generation.');
  if (!setup.volume || setup.volume.size < 200 || setup.volume.type !== 'STANDARD') {
    return block('Select or purchase at least 200 GB of persistent model storage in Runpod.');
  }
  if (!setup.release_available) {
    return block('This app does not have a published cloud worker release yet. Check app updates to enable model setup.', 'updates');
  }
  if (setup.setup_error || setup.setup_phase === 'failed' || Object.values(setup.models).some(model => model.state === 'failed') || setup.compute?.status === 'failed') {
    return block('Model setup failed. Open Runpod to see the error and retry setup.');
  }
  if (!setup.ready || setup.compute?.kind === 'installer') {
    if (setup.setup_phase === 'starting_worker' || (setup.compute?.status === 'provisioning' && !setup.setup_running)) {
      return block('The download worker is starting or needs attention. Open Runpod to check setup.');
    }
    const preparing = setup.compute?.kind === 'installer' || Object.values(setup.models).some(model =>
      ['discovering', 'verifying', 'downloading'].includes(model.state));
    const percent = preparing && typeof setup.progress_pct === 'number' && Number.isFinite(setup.progress_pct)
      ? ` (${Math.min(100, Math.max(0, setup.progress_pct)).toFixed(0)}%)` : '';
    return block(preparing
      ? `Models are downloading or being verified${percent}. Open Runpod to view progress.`
      : 'Download and verify the required models in Runpod before generating.');
  }
  if (!setup.policy) return block('Confirm your voice generation spending limit in Runpod before generating.');
  if (audioTools && !audioTools.ready) return {
    blocked: true, reason: `${audioTools.detail || 'Local audio tools are preparing.'} Use Audio tools setup above to view progress or retry.`,
    action: null, actionLabel: null,
  };
  return available;
}
