import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type CloudDiscovery, type StorageQuote, type CloudModelInstall } from '../services/api';

const money = (v: number | null | undefined) => v == null ? 'Unknown' : `$${v.toFixed(2)}`;
const gb = (v: number) => `${(v / 1e9).toFixed(2)} GB`;
const DEFAULT_SESSION_LIMIT = 1;
const DEFAULT_HOURLY_LIMIT = 2;
const suitableVolumes = (discovery: CloudDiscovery | null) =>
  discovery?.volumes.filter(v => v.size >= 200 && v.type === 'STANDARD') ?? [];
const modelName = (id: string) => ({ vox_cpm: 'VoxCPM', voxcpm: 'VoxCPM', voxcpm2: 'VoxCPM 2',
  chatterbox: 'Chatterbox', chatterbox_ml_v3: 'Chatterbox Multilingual', chatterbox_multilingual: 'Chatterbox Multilingual', omni_voice: 'OmniVoice',
  omni_urdu: 'OmniVoice Urdu', omnivoice_urdu: 'OmniVoice Urdu', qwen_transliterator: 'Qwen text conversion', gemma_transliterator: 'Gemma text conversion',
  'qwen2.5-3b-instruct-analyzer': 'Qwen script helper', 'gemma-4-31b-it-transliterator': 'Gemma Urdu text conversion',
})[id] ?? id.replace(/[_-]/g, ' ');

export function CloudSetupPanel({ connected }: { connected: boolean }) {
  const client = useQueryClient();
  const setupQ = useQuery({ queryKey: ['cloud-setup'], queryFn: api.cloudSetup, refetchInterval: 3000 });
  const setup = setupQ.data;
  const [discovery, setDiscovery] = useState<CloudDiscovery | null>(null);
  const [quote, setQuote] = useState<StorageQuote | null>(null);
  const [region, setRegion] = useState('');
  const [volumeId, setVolumeId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [sessionLimit, setSessionLimit] = useState(String(DEFAULT_SESSION_LIMIT));
  const [hourlyLimit, setHourlyLimit] = useState(String(DEFAULT_HOURLY_LIMIT));
  const automaticAttempt = useRef<string | null>(null);
  async function action(work: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await work(); await client.invalidateQueries({ queryKey: ['cloud-setup'] }); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }
  async function refresh() {
    const value = await api.cloudDiscover(); setDiscovery(value);
    // Discovery is ordered by the cheapest compatible available GPU region.
    setRegion(r => value.regions.some(v => v.id === r) ? r : value.regions[0]?.id ?? '');
    const volumes = suitableVolumes(value);
    setVolumeId(id => volumes.some(v => v.id === id) ? id : volumes[0]?.id ?? '');
    setQuote(null);
  }
  useEffect(() => {
    if (connected) void action(refresh);
    else { setDiscovery(null); setQuote(null); setVolumeId(''); setRegion(''); automaticAttempt.current = null; }
  }, [connected]);
  useEffect(() => { if (setup?.ready) void client.invalidateQueries({ queryKey: ['models'] }); }, [setup?.ready, client]);
  useEffect(() => {
    setSessionLimit(String(setup?.policy?.max_session_usd ?? DEFAULT_SESSION_LIMIT));
    setHourlyLimit(String(setup?.policy?.max_hourly_usd ?? DEFAULT_HOURLY_LIMIT));
  }, [setup?.policy?.max_session_usd, setup?.policy?.max_hourly_usd]);
  useEffect(() => {
    if (!connected || !setup || busy || setup.ready || setup.compute || !setup.release_available
        || setup.auto_setup_enabled != null) return;
    const volume = setup.volume ?? suitableVolumes(discovery)[0];
    if (!volume || automaticAttempt.current === volume.id) return;
    automaticAttempt.current = volume.id;
    void action(async () => {
      if (!setup.volume) await api.cloudSelectStorage(volume.id);
      const value = await api.cloudAutoSetup(true);
      client.setQueryData(['cloud-setup'], value);
    });
  }, [connected, setup, discovery, busy, client]);

  const suitable = suitableVolumes(discovery);
  const chosenVolume = suitable.find(v => v.id === volumeId);
  const chosenRegion = discovery?.regions.find(r => r.id === region);
  const installing = setup?.compute?.kind === 'installer';
  const creationConfirmed = setup?.compute?.creation_confirmed ?? !!setup?.compute?.pod_id;
  const cancelled = setup?.setup_phase === 'cancelled';
  const cancelling = setup?.setup_phase === 'cancelling';
  const waiting = !!setup?.auto_setup_waiting;
  const failed = !!setup && !cancelled && !cancelling && !waiting && (
    !!setup.setup_error || setup.setup_phase === 'failed'
    || /failed|could not|couldn't|release is pending|release needs retry|reached its time limit|needs cleanup/i.test(setup.detail)
    || setup.compute?.status === 'failed'
    || Object.values(setup.models).some(m => m.state === 'failed' || m.state === 'unsupported')
  );
  const releasePending = !!setup?.compute && (setup.cleanup_pending
    || /release is pending|release needs retry|release compute|needs cleanup/i.test(setup.detail));
  const installingActive = !failed && !cancelled && (setup?.setup_running ?? installing);
  const expired = quote != null && Date.now() / 1000 > quote.expires_at;
  const validLimits = Number.isFinite(Number(sessionLimit)) && Number(sessionLimit) >= .1 && Number(sessionLimit) <= 20
    && Number.isFinite(Number(hourlyLimit)) && Number(hourlyLimit) >= .1 && Number(hourlyLimit) <= 10;
  const retryDisabled = busy || !!setup?.compute || installingActive || cancelling || !setup?.release_available;
  const failureMessage = 'Automatic model preparation needs attention. Your storage and downloaded files are kept. Resolve the issue below to continue.';
  const phase = setup?.setup_phase ?? (installingActive ? (setup?.bytes_completed ? 'downloading' : 'starting_worker') : setup?.ready ? 'ready' : 'idle');
  const activity = ({ starting_worker: 'Starting a temporary machine and downloading its software. Model files have not started downloading yet.',
    checking_files: 'Checking which model files are already on your storage.', downloading: 'Downloading missing model files to your storage.',
    verifying: 'Checking the downloaded files before generation is enabled.', stopping_worker: 'Stopping the temporary machine. Your downloaded models stay on storage.',
    cancelling: 'Stopping model setup and releasing its temporary machine. Your storage and downloaded files are kept.',
    cancelled: 'Model setup cancelled. Resume when ready: valid downloaded files are reused and incomplete files resume where supported.',
    idle: setup?.volume ? 'Required models are prepared automatically on your storage.' : 'Connect storage above to get started.',
    ready: 'Your models are downloaded and checked.', failed: failureMessage,
  })[phase];
  const models: [string, CloudModelInstall][] = (setup?.required_model_ids ?? Object.keys(setup?.models ?? {}))
    .map(id => [id, setup?.models[id] ?? { state: 'pending' }]);
  const hasTransfer = !failed && !cancelled && !cancelling && phase === 'downloading' && (setup?.bytes_total ?? 0) > 0
    && setup?.progress_pct != null && Number.isFinite(setup.progress_pct);

  if (!connected) return null;
  return <>
    <section className="card">
      <header className="card-head"><h2>1. Model storage</h2></header>
      <p>Keep the models on Runpod so you only download them once. Your voice files and generated audio stay on this PC.</p>
      <p><strong>Account credit: {money(discovery?.balance_usd)}</strong></p>
      <div className="runpod-actions">
        <button type="button" className="btn sm" disabled={busy} onClick={() => void action(refresh)}>Refresh storage and funds</button>
        <a className="btn sm" href="https://www.runpod.io/console/user/billing" target="_blank" rel="noreferrer">Add funds</a>
      </div>
      {setup?.volume ? <>
        <p><strong>Storage connected</strong> · {setup.volume.size} GB · {setup.volume.dataCenter}</p>
        <p className="hint">Storage is billed while you keep it, even when no machine is running. A 200 GB volume costs about $14/month.</p>
      </> : chosenVolume ? <>
        <p><strong>Existing storage found</strong> · {chosenVolume.size} GB · {chosenVolume.dataCenter}</p>
        <p className="hint">Connecting this storage and preparing required models automatically. Temporary download-machine time is limited to $1 per setup attempt.</p>
      </> : <>
        <p>{discovery ? 'You need 200 GB of storage for the models, cache and updates.' : 'Checking your storage…'}</p>
        <p className="hint">About $14/month while kept. The app selects an available region with a low GPU rate. Review the price before purchasing.</p>
        {chosenRegion && <p>Selected region: <strong>{chosenRegion.name}</strong> · GPU from {money(chosenRegion.gpu_hourly_from_usd)}/hour.</p>}
        {discovery && !chosenRegion && <p role="status">No suitable region is available right now. Refresh to check again.</p>}
        <button type="button" className="btn primary sm" disabled={busy || !region || !!setup?.compute} onClick={() => void action(async () => {
          setQuote(await api.cloudQuote(region));
        })}>Review storage purchase</button>
        {quote && <div className="runpod-purchase" aria-live="polite">
          <p><strong>{quote.storage_gb} GB · {money(quote.monthly_usd)}/month</strong> · {quote.region}</p>
          <p className="hint">You need {money(quote.required_credit_reserve_usd)} available credit for one day of storage and up to {money(quote.installer_budget_usd)} to download and check the models. Storage is billed over time; the full monthly price is not charged now.</p>
          {!quote.can_purchase && <p>{quote.balance_usd == null ? 'We could not check your balance. Check the API key permissions and refresh.' : 'Add funds, then refresh and review the purchase again.'}</p>}
          {expired && <p>The price check expired. Review the purchase again.</p>}
          <button type="button" className="btn primary sm" disabled={busy || !quote.can_purchase || expired || !setup?.release_available || !!setup?.compute} onClick={() => void action(async () => {
            await api.cloudPurchase(quote.id); setQuote(null); await api.cloudAutoSetup(true); await refresh();
          })}>Purchase storage and download models</button>
        </div>}
      </>}
      <details className="runpod-advanced">
        <summary>Advanced storage settings</summary>
        <p className="hint">The current model files total about 49 GB. Extra space holds cache files and future updates.</p>
        <p className="hint">Total account spend: {money(discovery?.account_hourly_spend_usd)}/hour, including your other projects.</p>
        {setup?.volume ? <p className="hint">Storage name: {setup.volume.name}</p> : <div className="runpod-controls">
          {suitable.length > 0 ? <label className="field"><span className="field-label">Choose existing storage</span>
            <select value={volumeId} onChange={e => { setVolumeId(e.target.value); setQuote(null); }}>
              {suitable.map(v => <option key={v.id} value={v.id}>{v.name} · {v.size} GB · {v.dataCenter}</option>)}
            </select></label> : <label className="field"><span className="field-label">Storage region</span>
            <select value={region} onChange={e => { setRegion(e.target.value); setQuote(null); }}>
              {(discovery?.regions ?? []).map(r => <option value={r.id} key={r.id}>{r.name} · GPU from {money(r.gpu_hourly_from_usd)}/hour</option>)}
            </select></label>}
        </div>}
      </details>
      {setup && !setup.release_available && <div role="status">
        <p className="hint">This app version needs an update before it can download models or rent a machine.</p>
        <button type="button" className="btn sm" onClick={() => window.dispatchEvent(new CustomEvent('vcs-open-updates'))}>Check app updates</button>
      </div>}
      {(error || setupQ.error) && <p role="alert">{error || String(setupQ.error)}</p>}
    </section>
    <section className="card">
      <header className="card-head"><h2>2. {setup?.ready ? 'Models ready' : 'Set up models'}</h2></header>
      <ol className="runpod-steps" aria-label="Model setup steps">
        <li><strong>{setup?.volume ? '✓ ' : ''}Storage</strong></li>
        <li><strong>{phase === 'starting_worker' && !failed ? 'Current: ' : ''}Start machine</strong><span>Download setup software</span></li>
        <li><strong>{phase === 'downloading' && !failed ? 'Current: ' : ''}Download models</strong></li>
        <li><strong>{(phase === 'checking_files' || phase === 'verifying') && !failed ? 'Current: ' : ''}Check files</strong></li>
        <li><strong>{setup?.ready ? '✓ ' : ''}Ready</strong></li>
      </ol>
      <p aria-live="polite">{waiting ? 'Waiting for an available download machine in your storage region. The app will check again automatically; model downloading has not started yet.' : failed ? failureMessage : activity}</p>
      {!setup?.ready && !cancelled && <p className="hint">Required models download automatically. Temporary setup compute is limited to $1 per attempt; storage is billed separately.</p>}
      {(failed || cancelled) && (setup?.setup_error || setup?.detail) && <p role={setup?.setup_error ? 'alert' : 'status'}>{setup.setup_error || setup.detail}</p>}
      {releasePending && <p role="alert">{creationConfirmed
        ? 'Runpod has not confirmed that the rented machine stopped. Check the pending machine before retrying setup.'
        : 'Runpod has not confirmed whether the machine started. Check the pending machine; another setup attempt stays blocked until this one is reconciled.'}</p>}
      {(installingActive || waiting || setup?.auto_setup_enabled) && !cancelling && phase !== 'stopping_worker' && !setup?.ready && <div>
        <button type="button" className="btn sm" disabled={busy} onClick={() => void action(api.cloudCancel)}>Cancel model setup</button>
        <p className="hint">Stops the download and releases the temporary machine. Your model storage is kept.</p>
      </div>}
      {hasTransfer && <>
        <progress aria-label="Model download progress" max={100} value={Math.max(0, Math.min(100, setup!.progress_pct!))} />
        <p className="hint">{setup!.progress_pct!.toFixed(1)}% downloaded · {gb(setup?.bytes_completed ?? 0)} / {gb(setup!.bytes_total!)}</p>
      </>}
      {!setup?.ready && models.length > 0 && <ul className="runpod-model-status">
        {models.map(([id, m]) => <li key={id}><strong>{modelName(id)}</strong> · {(failed || cancelled || cancelling) && ['downloading', 'verifying', 'discovering'].includes(m.state)
          ? 'Stopped' : ({ ready: 'Downloaded and checked', installed: 'Downloaded and checked', downloading: 'Downloading', verifying: 'Checking files', discovering: 'Finding required files', resolving: 'Checking required files', idle: 'Waiting', pending: 'Waiting', failed: 'Failed', cancelled: 'Stopped', unsupported: 'Unavailable' })[m.state] ?? m.state}
          {m.state === 'downloading' && m.progress_pct != null && Number.isFinite(m.progress_pct) && ` · ${failed || cancelled || cancelling ? 'Last reported: ' : ''}${Math.max(0, Math.min(100, m.progress_pct)).toFixed(1)}%`}
          {m.bytes_total != null && m.bytes_total > 0 && ` · ${gb(m.bytes_completed ?? 0)} / ${gb(m.bytes_total)}`}</li>)}
      </ul>}
      {setup?.volume && !setup.ready && setup.auto_setup_enabled === false && !setup.compute && <button type="button" className="btn sm" disabled={retryDisabled}
        onClick={() => void action(() => api.cloudAutoSetup(true))}>Resume automatic downloads (up to $1)</button>}
      {!setup?.ready && <p className="hint">Generation stays disabled until the required models are fully checked.</p>}
      {!setup?.ready && <p className="hint">Retrying checks stored files first. Valid files are skipped; partial downloads resume if supported. Only corrupt or changed files need downloading again.</p>}
      {setup?.compute && <div className="runpod-machine">
        <p><strong>{creationConfirmed ? (installing ? 'Model setup machine' : 'Voice generation machine') : 'Machine start not confirmed'}</strong> · {creationConfirmed ? '' : 'Estimated rate: '}{money(setup.compute.hourly_usd)}/hour.</p>
        <p className="hint">{creationConfirmed ? 'It is billed until Runpod confirms it has stopped. Automatic stop-by time: ' : 'This is a pending start attempt, not a confirmed rental. Requested stop-by time: '}{new Date(setup.compute.deadline).toLocaleString()}.</p>
        {!(installingActive || cancelling) && <button type="button" className="btn sm" disabled={busy}
          onClick={() => void action(installing ? api.cloudCancel : api.cloudRelease)}>{creationConfirmed ? 'Stop rented machine' : 'Check pending start'}</button>}
      </div>}
      <details className="runpod-advanced">
        <summary>{failed ? 'Error details and model status' : 'Advanced model details'}</summary>
        {(setup?.setup_error || setup?.detail) && <p>{setup.setup_error || setup.detail}</p>}
        {setup?.compute && <p className="hint">Machine status: {setup.compute.status}</p>}
        {models.map(([id, m]) => <p key={id}>{id} · {m.state}
          {m.current_file && <span className="hint"> · {m.current_file}</span>}{m.detail && <span className="hint"> · {m.detail}</span>}</p>)}
        {setup?.ready && <button type="button" className="btn sm" disabled={retryDisabled} onClick={() => void action(api.cloudInstall)}>Check stored models again (up to $1)</button>}
        {!setup?.ready && failed && setup.auto_setup_enabled !== false && <button type="button" className="btn sm" disabled={retryDisabled}
          onClick={() => void action(() => api.cloudAutoSetup(true))}>Resume after resolving the error (up to $1)</button>}
      </details>
    </section>
    <section className="card">
      <header className="card-head"><h2>3. Voice generation</h2></header>
      <p>The app automatically rents the cheapest available GPU that can run your models. It starts when you generate a voice and stops after your queue finishes.</p>
      <p className="hint">You pay for startup, model loading and generation. Keeping storage is a separate cost.</p>
      {setup?.policy ? <p><strong>Approved limits:</strong> {money(setup.policy.max_session_usd)} per session · GPU up to {money(setup.policy.max_hourly_usd)}/hour.</p> : <>
        <p><strong>Suggested limits:</strong> {money(DEFAULT_SESSION_LIMIT)} per session · GPU up to {money(DEFAULT_HOURLY_LIMIT)}/hour.</p>
        <button type="button" className="btn primary sm" disabled={busy} onClick={() => void action(async () => {
          await api.cloudPolicy(DEFAULT_SESSION_LIMIT, DEFAULT_HOURLY_LIMIT);
        })}>Approve these limits</button>
        <p className="hint">Approval saves your spending limits. It does not start a machine or charge you now.</p>
      </>}
      {setup?.ready && setup.policy && <p role="status">Ready. Go to Voice Studio, choose your voice and enter a script.</p>}
      <details className="runpod-advanced">
        <summary>Advanced generation settings</summary>
        <div className="runpod-controls">
          <label className="field"><span className="field-label">Maximum cost per session (USD)</span>
            <input type="number" min="0.1" max="20" step="0.1" value={sessionLimit} onChange={e => setSessionLimit(e.target.value)} /></label>
          <label className="field"><span className="field-label">Maximum GPU rate per hour (USD)</span>
            <input type="number" min="0.1" max="10" step="0.1" value={hourlyLimit} onChange={e => setHourlyLimit(e.target.value)} /></label>
          <button type="button" className="btn sm" disabled={busy || !validLimits} onClick={() => void action(async () => {
            await api.cloudPolicy(Number(sessionLimit), Number(hourlyLimit));
          })}>{setup?.policy ? 'Save new spending limits' : 'Approve custom limits'}</button>
        </div>
        <p className="hint">Current mode: rent a Pod, finish the queue, then terminate it. The GPU must be available in your storage region and fit within your approved hourly rate.</p>
        <div className="runpod-scroll"><table><thead><tr><th>Option</th><th>Benefit</th><th>Tradeoff</th></tr></thead><tbody>
          <tr><td>Stop after generation (current)</td><td>Lower hourly prices; good for a batch of voices.</td><td>Each new session starts a machine and loads the models.</td></tr>
          <tr><td>Serverless Flex (not available yet)</td><td>Can sleep between requests and may start faster.</td><td>Higher active prices; support and cost comparisons still need testing.</td></tr>
          <tr><td>Keep a machine warm (not available yet)</td><td>Faster repeated edits because models stay loaded.</td><td>You also pay while it waits. The current queue only has a five-second grace period.</td></tr>
        </tbody></table></div>
      </details>
    </section>
  </>;
}
