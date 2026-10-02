import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type CloudModelInstall } from '../services/api';

export type RunpodSetupStep = 'account' | 'storage' | 'models' | 'ready';
const money = (value: number | null | undefined) => value == null ? 'Not available' : `$${value.toFixed(2)}`;
const gb = (value: number) => `${(value / 1e9).toFixed(2)} GB`;
const storageCost = (size: number) => money(Math.min(size, 1000) * .07 + Math.max(0, size - 1000) * .05);
const modelName = (id: string) => ({ voxcpm2: 'VoxCPM 2', chatterbox_ml_v3: 'Chatterbox Multilingual',
  omnivoice_urdu: 'OmniVoice Urdu', 'qwen2.5-3b-instruct-analyzer': 'Qwen script helper',
  'gemma-4-31b-it-transliterator': 'Gemma Urdu text conversion',
})[id] ?? id.replace(/[_-]/g, ' ');

export function CloudSetupPanel({ connected, step = 'models', onStep = () => {} }: {
  connected: boolean; step?: RunpodSetupStep; onStep?: (step: RunpodSetupStep) => void;
}) {
  const client = useQueryClient();
  const setupQ = useQuery({ queryKey: ['cloud-setup'], queryFn: api.cloudSetup, refetchInterval: 3000 });
  const discoverQ = useQuery({ queryKey: ['cloud-discovery'], queryFn: api.cloudDiscover,
    enabled: connected, staleTime: 60000, retry: false });
  const audioToolsQ = useQuery({ queryKey: ['audio-tools'], queryFn: api.audioToolsStatus,
    enabled: connected, refetchInterval: query => query.state.data?.ready ? false : 2000, retry: false });
  const setup = setupQ.data;
  const discovery = discoverQ.data;
  const minimum = discovery?.storage_min_gb ?? 60;
  const [region, setRegion] = useState('');
  const [volumeId, setVolumeId] = useState('');
  const [storageMode, setStorageMode] = useState<'existing' | 'new'>('existing');
  const [size, setSize] = useState('');
  const [name, setName] = useState('');
  const [quote, setQuote] = useState<Awaited<ReturnType<typeof api.cloudQuote>> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [sessionLimit, setSessionLimit] = useState('1');
  const [hourlyLimit, setHourlyLimit] = useState('2');
  const automaticAttempt = useRef<string | null>(null);
  const selectionAttempt = useRef<string | null>(null);
  const automaticVolume = useRef<string | null | undefined>(undefined);
  const stored = setup?.volume;
  const running = !!setup?.setup_running || !!setup?.compute || !!setup?.auto_setup_waiting;
  const controlsLocked = busy || running || !!setup?.cleanup_pending;
  const phase = setup?.setup_phase ?? 'idle';
  const paused = phase === 'cancelled' && !setup?.auto_setup_enabled;
  const stopping = phase === 'cancelling' || phase === 'stopping_worker';
  const failed = phase === 'failed' || (!!setup?.setup_error && !paused && !stopping);
  const cleanup = !!setup?.cleanup_pending;
  const confirmed = setup?.compute?.creation_confirmed ?? !!setup?.compute?.pod_id;
  const standardVolumes = discovery?.volumes.filter(volume => volume.type === 'STANDARD') ?? [];
  const candidate = standardVolumes.find(volume => volume.app_candidate && volume.size >= minimum);

  async function action(work: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await work(); await client.invalidateQueries({ queryKey: ['cloud-setup'] }); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }
  async function refresh() {
    const value = await api.cloudDiscover();
    client.setQueryData(['cloud-discovery'], value); setQuote(null);
  }
  async function enableDownloads() {
    const value = await api.cloudAutoSetup(true);
    client.setQueryData(['cloud-setup'], value);
    onStep('models');
  }
  useEffect(() => {
    if (!discovery) return;
    setRegion(current => discovery.regions.some(value => value.id === current) ? current : discovery.regions[0]?.id ?? '');
    setSize(current => current || String(minimum));
    setVolumeId(current => standardVolumes.some(value => value.id === current) ? current
      : stored?.id ?? standardVolumes.find(value => value.app_owned)?.id ?? '');
  }, [discovery, minimum, stored?.id]);
  useEffect(() => {
    setSessionLimit(String(setup?.policy?.max_session_usd ?? 1));
    setHourlyLimit(String(setup?.policy?.max_hourly_usd ?? 2));
  }, [setup?.policy?.max_session_usd, setup?.policy?.max_hourly_usd]);
  useEffect(() => { if (setup?.ready) void client.invalidateQueries({ queryKey: ['models'] }); }, [setup?.ready, client]);
  useEffect(() => {
    if (connected && setup && automaticVolume.current === undefined) automaticVolume.current = stored?.id ?? null;
  }, [connected, setup, stored]);
  // Only a confirmed Voice Clone volume may be selected automatically. A name
  // match alone does not prove that a volume belongs to this app.
  useEffect(() => {
    if (!connected || !setup || stored || controlsLocked) return;
    const owned = standardVolumes.find(value => value.app_owned && value.size >= minimum);
    if (!owned || selectionAttempt.current === owned.id) return;
    selectionAttempt.current = owned.id;
    automaticVolume.current = owned.id;
    void action(() => api.cloudSelectStorage(owned.id));
  }, [connected, setup, discovery, controlsLocked, stored]);
  // Migrate a previously selected volume to automatic setup once. Explicit
  // user pauses (false) remain paused; ordinary page navigation never cancels.
  useEffect(() => {
    if (!connected || !setup || !stored || controlsLocked || setup.ready || !setup.release_available
      || setup.auto_setup_enabled != null || automaticAttempt.current === stored.id || automaticVolume.current !== stored.id) return;
    automaticAttempt.current = stored.id;
    void action(async () => {
      client.setQueryData(['cloud-setup'], await api.cloudAutoSetup(true));
    });
  }, [connected, setup, stored, controlsLocked, client]);

  const models: [string, CloudModelInstall][] = (setup?.required_model_ids ?? Object.keys(setup?.models ?? {}))
    .map(id => [id, setup?.models[id] ?? { state: 'pending' }]);
  const validSize = Number.isInteger(Number(size)) && Number(size) >= minimum && Number(size) <= 4000;
  const validLimits = Number.isFinite(Number(sessionLimit)) && Number(sessionLimit) >= .1 && Number(sessionLimit) <= 20
    && Number.isFinite(Number(hourlyLimit)) && Number(hourlyLimit) >= .1 && Number(hourlyLimit) <= 10;
  const expired = quote != null && Date.now() / 1000 > quote.expires_at;
  const noRelease = !!setup && !setup.release_available;
  const audioTools = audioToolsQ.data;
  const allReady = !!setup?.ready && !!audioTools?.ready && !!setup.policy;
  const normalMessage = setup?.ready ? 'Your models are ready.'
    : cleanup ? 'Waiting for Runpod to confirm the machine stopped.'
    : stopping ? 'Pausing setup…'
    : paused ? 'Downloads paused.'
    : failed ? 'Setup needs attention.'
    : setup?.auto_setup_waiting ? 'Waiting for an available machine. We will try again automatically.'
    : phase === 'downloading' ? 'Downloading your models…'
    : phase === 'verifying' ? 'Checking your models…'
    : 'Preparing your setup…';

  if (!connected || step === 'account') return null;
  return <section className="card runpod-wizard-page" aria-labelledby="runpod-page-heading">
    {step === 'storage' && <>
      <header className="card-head"><h2 id="runpod-page-heading">Storage for your voices</h2></header>
      <p>Models stay in a separate Runpod volume for Voice Clone Studio.</p>
      <p>Account credit: <strong>{money(discovery?.balance_usd)}</strong></p>
      <div className="runpod-actions">
        <button className="btn sm" disabled={busy} onClick={() => void action(refresh)}>Refresh</button>
        <a className="btn sm" href="https://www.runpod.io/console/user/billing" target="_blank" rel="noreferrer">Add funds</a>
      </div>
      {stored ? <div className="runpod-storage-choice">
        <strong>{stored.name}</strong><span>{stored.size} GB · {stored.dataCenter} · About {storageCost(stored.size)}/month</span>
        <p className="hint">Storage is billed while you keep it, even between generations.</p>
      </div> : candidate && storageMode !== 'new' ? <div className="runpod-storage-choice">
        <strong>Voice Clone storage found</strong><span>{candidate.name} · {candidate.size} GB · {candidate.dataCenter} · About {storageCost(candidate.size)}/month</span>
        <button className="btn primary sm" disabled={controlsLocked || noRelease} onClick={() => void action(async () => {
          await api.cloudSelectStorage(candidate.id); await enableDownloads();
        })}>Use this storage and continue</button>
        <p className="hint">Model setup: up to $1 per session.</p>
      </div> : <>
        <p>{discovery ? `Create a ${minimum} GB volume for your models.` : 'Checking your storage…'}</p>
        <p className="hint">Includes the model files and 10 GB of spare space.</p>
      </>}
      <details className="runpod-advanced">
        <summary>Advanced storage settings</summary>
        {running && <p role="status">{setup?.compute?.kind === 'generation' ? 'Finish generation before changing storage.' : 'Pause downloads before changing storage.'}</p>}
        {running && setup?.compute?.kind !== 'generation' && !stopping && !cleanup && <button className="btn sm" disabled={busy} onClick={() => void action(api.cloudCancel)}>Pause downloads</button>}
        <div className="runpod-controls">
          <label className="field"><span className="field-label">Storage choice</span>
            <select value={storageMode} disabled={controlsLocked} onChange={event => { setStorageMode(event.target.value as 'existing' | 'new'); setQuote(null); }}>
              <option value="existing">Use existing storage</option><option value="new">Create a new volume</option>
            </select></label>
          {storageMode === 'existing' ? <>
            <label className="field"><span className="field-label">Available volumes</span>
              <select value={volumeId} disabled={controlsLocked} onChange={event => { setVolumeId(event.target.value); setQuote(null); }}>
                <option value="">Choose a volume</option>
                {standardVolumes.map(volume => <option key={volume.id} value={volume.id} disabled={volume.size < minimum}>{volume.name} · {volume.size} GB · {volume.dataCenter}{volume.size < minimum ? ` · Needs at least ${minimum} GB` : volume.app_owned ? ' · Voice Clone' : ''}</option>)}
              </select></label>
            {volumeId && !standardVolumes.find(volume => volume.id === volumeId)?.app_owned && volumeId !== stored?.id
              && <p className="hint">This volume may belong to another app. Use it only if you want Voice Clone models stored there.</p>}
            <button className="btn sm" disabled={controlsLocked || !volumeId || volumeId === stored?.id || noRelease || (standardVolumes.find(volume => volume.id === volumeId)?.size ?? 0) < minimum} onClick={() => void action(async () => {
              automaticVolume.current = null;
              await api.cloudSelectStorage(volumeId, !standardVolumes.find(volume => volume.id === volumeId)?.app_owned);
              setQuote(null);
            })}>Use this volume</button>
          </> : <>
            {stored && <p className="hint">Your current volume stays in your Runpod account and is still billed until you remove it.</p>}
            <label className="field"><span className="field-label">Volume name (optional)</span>
              <input value={name} maxLength={64} disabled={controlsLocked} placeholder="Voice Clone Studio" onChange={event => { setName(event.target.value); setQuote(null); }} /></label>
            <label className="field"><span className="field-label">Size (GB)</span>
              <input type="number" min={minimum} max="4000" step="1" value={size} disabled={controlsLocked} onChange={event => { setSize(event.target.value); setQuote(null); }} /></label>
            <p className="hint">Minimum {minimum} GB for all model files plus 10 GB spare.</p>
            <label className="field"><span className="field-label">Region</span>
              <select value={region} disabled={controlsLocked} onChange={event => { setRegion(event.target.value); setQuote(null); }}>
                {(discovery?.regions ?? []).map(value => <option key={value.id} value={value.id}>{value.name} · GPU from {money(value.gpu_hourly_from_usd)}/hour</option>)}
              </select></label>
          </>}
        </div>
      </details>
      {(storageMode === 'new' || (!stored && !candidate)) && <>
        {discovery && !region && <p role="status">No suitable region is available. Refresh to check again.</p>}
        <button className="btn primary sm" disabled={controlsLocked || !region || !validSize || noRelease} onClick={() => void action(async () => {
          setQuote(await api.cloudQuote(region, Number(size || minimum), name.trim() || undefined, storageMode === 'new' && !!stored));
        })}>Review storage cost</button>
        {quote && <div className="runpod-purchase" aria-live="polite">
          <p><strong>{quote.storage_gb} GB · {money(quote.monthly_usd)}/month</strong></p>
          <p>Model setup: up to {money(quote.installer_budget_usd)} per session.</p>
          {!quote.can_purchase && <p role="status">{quote.balance_usd == null ? 'Refresh to check your balance.' : `Add funds to reach ${money(quote.required_credit_reserve_usd)}, then refresh.`}</p>}
          {expired && <p>Review the cost again to continue.</p>}
          <button className="btn primary sm" disabled={controlsLocked || !quote.can_purchase || expired || noRelease} onClick={() => void action(async () => {
            await api.cloudPurchase(quote.id); setQuote(null); await enableDownloads(); await refresh();
          })}>Create storage and continue</button>
        </div>}
      </>}
      {stored && storageMode !== 'new' && <div className="runpod-wizard-footer">
        <button className="btn sm" onClick={() => onStep('account')}>Back</button>
        <button className="btn primary sm" disabled={busy || noRelease || cleanup} onClick={() => void action(async () => {
          if (!setup?.ready && !running && !setup?.auto_setup_enabled) await enableDownloads(); else onStep('models');
        })}>Continue</button>
        {!setup?.ready && !running && !setup?.auto_setup_enabled && <p className="hint">Missing models download automatically. Model setup: up to $1 per session.</p>}
      </div>}
      {!stored && <div className="runpod-wizard-footer"><button className="btn sm" onClick={() => onStep('account')}>Back</button></div>}
    </>}
    {step === 'models' && <>
      <header className="card-head"><h2 id="runpod-page-heading">Your models</h2></header>
      <p role="status" aria-live="polite">{normalMessage}</p>
      {failed && <p role="alert">{setup?.setup_error || 'Check the details below, then continue setup.'}</p>}
      {!stored && <button className="btn primary sm" onClick={() => onStep('storage')}>Set up storage</button>}
      <div className="runpod-model-list">
        {models.map(([id, model]) => {
          const ready = ['ready', 'installed'].includes(model.state);
          const stopped = paused || stopping || failed;
          const knownProgress = model.bytes_total != null && model.bytes_total > 0
            && model.progress_pct != null && Number.isFinite(model.progress_pct);
          const status = ready ? 'Ready' : stopped ? 'Paused' : ({ downloading: 'Downloading', verifying: 'Checking',
            discovering: 'Preparing', resolving: 'Preparing', failed: 'Needs attention', unsupported: 'Unavailable' })[model.state] ?? 'Waiting';
          return <div className="runpod-model-row" key={id}>
            <div className="runpod-model-heading"><strong>{modelName(id)}</strong><span>{status}</span></div>
            {knownProgress && <>
              <progress aria-label={`${modelName(id)} download progress`} max={100} value={Math.max(0, Math.min(100, model.progress_pct!))} />
              <span className="hint">{Math.max(0, Math.min(100, model.progress_pct!)).toFixed(0)}% · {gb(model.bytes_completed ?? 0)} / {gb(model.bytes_total!)}</span>
            </>}
          </div>;
        })}
      </div>
      {setup?.auto_setup_enabled && !setup.ready && !stopping && !cleanup && <button className="btn sm" disabled={busy} onClick={() => void action(api.cloudCancel)}>Pause downloads</button>}
      {stored && !setup?.ready && !setup?.auto_setup_enabled && !running && !cleanup && <>
        <button className="btn primary sm" disabled={busy || noRelease} onClick={() => void action(enableDownloads)}>{failed ? 'Continue setup' : 'Resume downloads'}</button>
        <p className="hint">Model setup: up to $1 per session.</p>
      </>}
      {cleanup && <button className="btn sm" disabled={busy} onClick={() => void action(api.cloudCancel)}>{confirmed ? 'Check machine stop' : 'Check pending start'}</button>}
      {(failed || cleanup) && <details className="runpod-advanced"><summary>Error details</summary>
        <p>{setup?.detail}</p>{setup?.compute && <p>Machine status: {setup.compute.status}. Requested stop time: {new Date(setup.compute.deadline).toLocaleString()}.</p>}
        {models.filter(([, model]) => model.detail).map(([id, model]) => <p key={id}>{modelName(id)}: {model.detail}</p>)}
      </details>}
      <div className="runpod-wizard-footer">
        <button className="btn sm" onClick={() => onStep('storage')}>Back</button>
        <button className="btn primary sm" disabled={!setup?.ready} onClick={() => onStep('ready')}>Continue</button>
      </div>
    </>}
    {step === 'ready' && <>
      <header className="card-head"><h2 id="runpod-page-heading">{allReady ? 'Ready to generate' : 'Finish setup'}</h2></header>
      {!audioTools?.ready && <div aria-live="polite">
        <p role="status">{audioTools?.stage === 'failed' ? 'App setup needs attention.' : 'Preparing your app…'}</p>
        {audioTools?.progress_pct != null && Number.isFinite(audioTools.progress_pct) && audioTools.bytes_total > 0 && <>
          <progress aria-label="App setup progress" max={100} value={Math.max(0, Math.min(100, audioTools.progress_pct))} />
          <p className="hint">{Math.max(0, Math.min(100, audioTools.progress_pct)).toFixed(0)}% · {gb(audioTools.bytes_completed)} / {gb(audioTools.bytes_total)}</p>
        </>}
        {(audioTools?.stage === 'failed' || audioTools?.stage === 'cancelled') && <>
          <button className="btn sm" disabled={busy} onClick={() => void action(async () => {
            client.setQueryData(['audio-tools'], await api.retryAudioTools());
          })}>Continue app setup</button>
          {audioTools.detail && <details className="runpod-advanced"><summary>Error details</summary><p>{audioTools.detail}</p></details>}
        </>}
        {audioToolsQ.error && <p role="alert">Unable to check app setup. <button className="btn sm" disabled={audioToolsQ.isFetching} onClick={() => void audioToolsQ.refetch()}>Check again</button></p>}
      </div>}
      <p>The app picks an available GPU for your voice. It starts when you generate and stops when your queue finishes.</p>
      <p>Your first generation also loads and checks the model.</p>
      <p><strong>{money(setup?.policy?.max_session_usd ?? 1)} maximum per session</strong> · GPU up to {money(setup?.policy?.max_hourly_usd ?? 2)}/hour.</p>
      {!setup?.policy && <button className="btn primary sm" disabled={busy} onClick={() => void action(() => api.cloudPolicy(1, 2))}>Approve these limits</button>}
      <details className="runpod-advanced"><summary>Advanced generation settings</summary>
        <div className="runpod-controls">
          <label className="field"><span className="field-label">Maximum per session (USD)</span><input type="number" min="0.1" max="20" step="0.1" value={sessionLimit} onChange={event => setSessionLimit(event.target.value)} /></label>
          <label className="field"><span className="field-label">Maximum GPU price per hour (USD)</span><input type="number" min="0.1" max="10" step="0.1" value={hourlyLimit} onChange={event => setHourlyLimit(event.target.value)} /></label>
          <button className="btn sm" disabled={busy || !validLimits} onClick={() => void action(() => api.cloudPolicy(Number(sessionLimit), Number(hourlyLimit)))}>Save spending limits</button>
        </div>
      </details>
      <div className="runpod-wizard-footer"><button className="btn sm" onClick={() => onStep('models')}>Back</button>
        <button className="btn primary sm" disabled={!allReady} onClick={() => window.dispatchEvent(new CustomEvent('vcs-open-voice-studio'))}>Open Voice Studio</button>
        {!allReady && <p className="hint">{!setup?.ready ? 'Finish model downloads to continue.' : !audioTools?.ready ? 'App setup must finish before you generate.' : 'Approve spending limits to continue.'}</p>}
      </div>
    </>}
    {noRelease && <p role="status">Update the app to finish setup. <button className="btn sm" onClick={() => window.dispatchEvent(new CustomEvent('vcs-open-updates'))}>Check updates</button></p>}
    {(error || setupQ.error || discoverQ.error) && <p role="alert">{error || String(setupQ.error || discoverQ.error)}</p>}
  </section>;
}
