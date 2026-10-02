interface SetupStatus {
  connected: boolean;
  ready: boolean;
  release_available: boolean;
  policy: unknown;
  models: Record<string, { state: string }>;
  compute: { kind: string; status: string } | null;
}

export interface HeaderStatus {
  label: string;
  tone: '' | 'ok' | 'down';
  detail: string;
}

/** Local API responses alone cannot establish cloud generation readiness. */
export function headerStatus(
  desktop: boolean,
  localOnline: boolean | null,
  setup: SetupStatus | undefined,
  setupError: boolean,
): HeaderStatus {
  if (localOnline === false) return { label: desktop ? 'Local service unavailable' : 'Offline', tone: 'down', detail: 'The app could not reach its API.' };
  if (!desktop) return { label: localOnline ? 'Online' : 'Connecting', tone: localOnline ? 'ok' : '', detail: 'Web app API connection.' };
  if (setupError) return { label: 'Runpod status unavailable', tone: 'down', detail: 'Cloud setup could not be checked. Open Runpod and retry.' };
  if (!setup) return { label: 'Checking Runpod', tone: '', detail: 'Checking saved credentials and model setup.' };
  if (!setup.connected) return { label: 'Runpod not connected', tone: '', detail: 'Add your Runpod API key in the Runpod tab.' };
  if (Object.values(setup.models).some(m => m.state === 'failed') || setup.compute?.status === 'failed') {
    return { label: 'Model setup failed', tone: 'down', detail: 'Open Runpod for the setup error and retry.' };
  }
  if (setup.compute?.kind === 'installer') return { label: 'Preparing models', tone: '', detail: 'Models are downloading or being verified. See progress in Runpod.' };
  if (!setup.ready || !setup.policy || !setup.release_available) return { label: 'Runpod setup needed', tone: '', detail: 'Finish storage, verified models and compute limits in Runpod before generating.' };
  if (setup.compute) return { label: 'GPU session active', tone: 'ok', detail: 'A cloud generation session is starting or running; compute time is billed.' };
  return { label: 'Ready to generate', tone: 'ok', detail: 'Models are verified and compute limits approved. No GPU session is active.' };
}
