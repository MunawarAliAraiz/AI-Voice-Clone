import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type CloudDiscovery, type StorageQuote } from '../services/api';

const money = (v: number | null | undefined) => v == null ? 'Unknown' : `$${v.toFixed(2)}`;
const gb = (v: number) => `${(v / 1e9).toFixed(2)} GB`;

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
  const [sessionLimit, setSessionLimit] = useState('1');
  const [hourlyLimit, setHourlyLimit] = useState('2');
  async function action(work: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await work(); await client.invalidateQueries({ queryKey: ['cloud-setup'] }); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }
  async function refresh() {
    const value = await api.cloudDiscover(); setDiscovery(value);
    setRegion(r => value.regions.some(v => v.id === r) ? r : value.regions[0]?.id ?? '');
    setQuote(null);
  }
  useEffect(() => { if (connected) void action(refresh); else { setDiscovery(null); setQuote(null); } }, [connected]);
  useEffect(() => { if (setup?.ready) void client.invalidateQueries({ queryKey: ['models'] }); }, [setup?.ready, client]);
  const suitable = discovery?.volumes.filter(v => v.size >= 200 && v.type === 'STANDARD') ?? [];
  const installing = setup?.compute?.kind === 'installer';
  const expired = quote != null && Date.now() / 1000 > quote.expires_at;
  if (!connected) return null;
  return <>
    <section className="card">
      <header className="card-head"><h2>Persistent model storage</h2></header>
      <p>Account credit: <strong>{money(discovery?.balance_usd)}</strong></p>
      <p className="hint">Account spend: {money(discovery?.account_hourly_spend_usd)}/hour, including other projects.</p>
      <button className="btn sm" disabled={busy} onClick={() => void action(refresh)}>Refresh storage and funds</button>{' '}
      <a className="btn sm" href="https://www.runpod.io/console/user/billing" target="_blank" rel="noreferrer">Add Runpod funds</a>
      <p className="hint">200 GB recommended · about $14/month while retained. Current pinned files total about 49 GB; spare capacity holds caches and updates. Voice references and generated audio stay on this PC.</p>
      {setup?.volume ? <>
        <p><strong>{setup.volume.name}</strong> · {setup.volume.size} GB · {setup.volume.dataCenter}</p>
        <button className="btn primary sm" disabled={busy || !!setup.compute || !setup.release_available}
          onClick={() => void action(api.cloudInstall)}>{setup.ready ? 'Verify stored models again' : 'Verify and download models (up to $1 compute)'}</button>
      </> : <>
        {suitable.length > 0 && <div className="runpod-controls">
          <label className="field"><span className="field-label">Existing suitable volume</span>
            <select value={volumeId} onChange={e => setVolumeId(e.target.value)}><option value="">Select storage</option>
              {suitable.map(v => <option key={v.id} value={v.id}>{v.name} · {v.size} GB · {v.dataCenter}</option>)}
            </select></label>
          <button className="btn sm" disabled={busy || !volumeId || !setup?.release_available} onClick={() => void action(async () => {
            await api.cloudSelectStorage(volumeId); await api.cloudInstall();
          })}>Use storage and download (up to $1 compute)</button>
        </div>}
        {discovery && suitable.length === 0 && <p>No suitable Standard volume with 200 GB was found.</p>}
        <div className="runpod-controls">
          <label className="field"><span className="field-label">Storage region</span>
            <select value={region} onChange={e => { setRegion(e.target.value); setQuote(null); }}>
              {(discovery?.regions ?? []).map(r => <option value={r.id} key={r.id}>{r.name} · GPU from {money(r.gpu_hourly_from_usd)}/hour</option>)}
            </select></label>
          <button className="btn sm" disabled={busy || !region} onClick={() => void action(async () => { setQuote(await api.cloudQuote(region)); })}>Review storage purchase</button>
        </div>
        {quote && <div aria-live="polite">
          <p><strong>{quote.storage_gb} GB · {money(quote.monthly_usd)}/month</strong> · {quote.region}</p>
          <p className="hint">Storage bills over time. Setup requires {money(quote.required_credit_reserve_usd)} available credit for a day of storage and up to {money(quote.installer_budget_usd)} temporary CPU compute. This reserve is not an upfront monthly charge.</p>
          {!quote.can_purchase && <p>{quote.balance_usd == null ? 'Balance is unknown. Check API key account permissions, then refresh.' : 'Add funds, refresh, then request a new quote.'}</p>}
          {expired && <p>Quote expired; request a new quote.</p>}
          <button className="btn primary sm" disabled={busy || !quote.can_purchase || expired || !setup?.release_available} onClick={() => void action(async () => {
            await api.cloudPurchase(quote.id); setQuote(null); await api.cloudInstall(); await refresh();
          })}>Purchase storage and set up models</button>
        </div>}
      </>}
      {setup && !setup.release_available && <div role="status">
        <p className="hint">Model setup is unavailable in this app version because a verified cloud worker release has not been included. You can check storage and funds now. Purchasing and model downloads unlock in the desktop update that includes the worker release.</p>
        <button type="button" className="btn sm" onClick={() => window.dispatchEvent(new CustomEvent('vcs-open-updates'))}>Check app updates</button>
      </div>}
      {(error || setupQ.error) && <p role="alert">{error || String(setupQ.error)}</p>}
    </section>
    <section className="card">
      <header className="card-head"><h2>{setup?.ready ? 'Models ready' : 'Prepare models'}</h2></header>
      <p aria-live="polite">{setup?.detail || 'Select storage to verify and prepare the required models.'}</p>
      {installing && <>
        <progress aria-label="Model transfer progress" max={100} value={setup?.progress_pct ?? undefined} style={{ width: '100%' }} />
        <p className="hint">{setup?.progress_pct == null ? 'Discovering file sizes…' : `${setup.progress_pct.toFixed(1)}% transferred`}
          {' · '}{gb(setup?.bytes_completed ?? 0)}{setup?.bytes_total != null && ` / ${gb(setup.bytes_total)}`}. Verification must pass before generation unlocks.</p>
      </>}
      {Object.entries(setup?.models ?? {}).map(([id, m]) => <p key={id}>{id} · {m.state}{m.progress_pct != null && ` · ${m.progress_pct.toFixed(1)}%`}
        {m.current_file && <span className="hint"> · {m.current_file}</span>}{m.detail && <span className="hint"> · {m.detail}</span>}</p>)}
      {!setup?.ready && <p className="hint">Voice generation and cloud text helpers remain disabled until every required model is verified.</p>}
      {setup?.compute && <p>Cloud {setup.compute.kind} · {setup.compute.status} · {money(setup.compute.hourly_usd)}/hour.
        Automatic termination deadline: {new Date(setup.compute.deadline).toLocaleString()}.</p>}
      {setup?.compute && !installing && <button className="btn sm" disabled={busy} onClick={() => void action(api.cloudRelease)}>Release compute</button>}
    </section>
    <section className="card">
      <header className="card-head"><h2>Automatic generation compute</h2></header>
      <p className="hint">The app selects the cheapest compatible available GPU beside your storage, then releases it after the queue finishes. Startup, verification and model loading are billed too.</p>
      <div className="runpod-controls">
        <label className="field"><span className="field-label">Maximum compute per session (USD)</span>
          <input type="number" min="0.1" max="20" step="0.1" value={sessionLimit} onChange={e => setSessionLimit(e.target.value)} /></label>
        <label className="field"><span className="field-label">Maximum GPU hourly rate (USD)</span>
          <input type="number" min="0.1" max="10" step="0.1" value={hourlyLimit} onChange={e => setHourlyLimit(e.target.value)} /></label>
        <button className="btn primary sm" disabled={busy || Number(sessionLimit) < .1 || Number(hourlyLimit) < .1} onClick={() => void action(async () => {
          await api.cloudPolicy(Number(sessionLimit), Number(hourlyLimit));
        })}>Approve automatic compute limits</button>
      </div>
      {setup?.policy && <p className="hint">Approved: up to {money(setup.policy.max_session_usd)} per session and {money(setup.policy.max_hourly_usd)}/hour. Storage is separate.</p>}
      <div className="runpod-scroll"><table><thead><tr><th>Option</th><th>Benefit</th><th>Tradeoff</th></tr></thead><tbody>
        <tr><td>Terminated Pod</td><td>Lower hourly rates; suits batches.</td><td>Fresh startup and model loading. Current managed mode.</td></tr>
        <tr><td>Serverless Flex</td><td>Sleeps between requests; may reduce repeat startup.</td><td>Higher active rates; this app's handler and comparison still need testing.</td></tr>
        <tr><td>Briefly warm</td><td>Faster repeated edits with loaded models.</td><td>Idle time is billed. Current queue grace is five seconds.</td></tr>
      </tbody></table></div>
    </section>
  </>;
}
