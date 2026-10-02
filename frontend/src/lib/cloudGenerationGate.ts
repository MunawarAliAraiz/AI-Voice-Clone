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
  const minimumStorage = setup.storage_min_gb ?? 60;
  if (!setup.volume || setup.volume.size < minimumStorage || setup.volume.type !== 'STANDARD') {
    return block(`Choose persistent model storage with at least ${minimumStorage} GB in Runpod.`);
  }
  if (setup.capacity?.sufficient === false) {
    return block('Your storage needs more free space. Open Runpod to choose storage.');
  }
  if (!setup.release_available) {
    return block('This app does not have a published cloud worker release yet. Check app updates to enable model setup.', 'updates');
  }
  if (setup.setup_phase === 'cancelling') return block('Pausing setup. Waiting for the download machine to stop.');
  if (setup.setup_phase === 'cancelled') return block(setup.cleanup_pending
    ? 'Waiting for the download machine to stop. Open Runpod to check it.'
    : 'Setup is paused. Open Runpod to continue.');
  if (setup.auto_setup_waiting) return block('Waiting for an available download machine.');
  if (setup.setup_error || setup.setup_phase === 'failed' || Object.values(setup.models).some(model => model.state === 'failed') || setup.compute?.status === 'failed') {
    return block('Model setup failed. Open Runpod to fix it.');
  }
  if (!setup.ready || setup.compute?.kind === 'installer') {
    if (setup.setup_phase === 'starting_worker' || (setup.compute?.status === 'provisioning' && !setup.setup_running)) {
      return block('Preparing your setup. Open Runpod to see progress.');
    }
    const preparing = setup.compute?.kind === 'installer' || Object.values(setup.models).some(model =>
      ['discovering', 'verifying', 'downloading'].includes(model.state));
    const percent = preparing && typeof setup.progress_pct === 'number' && Number.isFinite(setup.progress_pct)
      ? ` (${Math.min(100, Math.max(0, setup.progress_pct)).toFixed(0)}%)` : '';
    return block(preparing
      ? `Preparing models${percent}. Open Runpod to see progress.`
      : 'Finish model setup in Runpod before generating.');
  }
  if (!setup.policy) return block('Confirm your voice generation spending limit in Runpod before generating.');
  if (audioTools && !audioTools.ready) return {
    blocked: true, reason: `${audioTools.detail || 'Local audio tools are preparing.'} Use Audio tools setup above to view progress or retry.`,
    action: null, actionLabel: null,
  };
  return available;
}
