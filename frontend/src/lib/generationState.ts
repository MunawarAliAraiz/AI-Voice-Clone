import type { LiquidOrbState } from '../components/LiquidOrb';
import type { JobStatusResponse } from '../types/api';

/** This is a visual description of real state, never a progress estimate. */
export function generationState(input: {
  submitting: boolean; converting: boolean; job?: JobStatusResponse;
  checkingJob?: boolean; jobStatusError?: boolean;
  blocked: boolean; disabledReason: string | null; error: string | null;
}): { state: LiquidOrbState; label: string; detail: string } {
  if (input.submitting) return { state: 'queued', label: 'Adding to queue', detail: 'Waiting for the studio to accept your script.' };
  if (input.converting) return { state: 'generating', label: 'Converting your script', detail: 'Review the converted text before generating speech.' };
  if (input.jobStatusError) return { state: 'error', label: 'Status unavailable', detail: 'Your job was submitted. Check Recent for its latest status.' };
  if (input.checkingJob) return { state: 'queued', label: 'Checking your generation', detail: 'Your script was accepted by the queue.' };
  const job = input.job;
  if (job?.status === 'running') return { state: 'generating', label: 'Generating speech', detail: `Latest generation · ${job.title || 'Job #' + job.id}` };
  if (job?.status === 'queued') return { state: 'queued', label: 'Waiting in queue', detail: `Latest generation${job.position != null ? ' · Position ' + (job.position + 1) : ''}` };
  if (input.error) return { state: 'error', label: 'Could not submit', detail: 'Check the error above and try again.' };
  if (input.blocked) return { state: 'blocked', label: 'Setup needed', detail: 'Complete Runpod and audio setup to generate speech.' };
  if (job?.status === 'failed') return { state: 'error', label: 'Generation failed', detail: 'See the error below for the next step.' };
  if (job?.status === 'cancelled') return { state: 'paused', label: 'Generation cancelled', detail: 'You can edit your script and generate again.' };
  if (job?.status === 'succeeded' && job.result && 'audio_url' in job.result && job.result.audio_url) {
    return { state: 'complete', label: 'Audio ready', detail: 'Latest generation · Listen or download below.' };
  }
  if (job?.status === 'succeeded') return { state: 'error', label: 'Audio unavailable', detail: 'The job finished without an audio result. Check Recent for details.' };
  return { state: 'idle', label: input.disabledReason ? 'Prepare your script' : 'Ready to generate',
    detail: input.disabledReason || 'Your next generation will be added to the queue.' };
}
