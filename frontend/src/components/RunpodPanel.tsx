import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CloudSetupPanel, type RunpodSetupStep } from './CloudSetupPanel';
import { queryKeys } from '../hooks/queries';
import { api, type RunpodAnalytics, type RunpodEstimate } from '../services/api';
import type { ModelListResponse } from '../types/api';

function money(value: number | undefined, digits = 4): string {
  return value == null ? 'Unknown' : `$${value.toFixed(digits)}`;
}

export function RunpodPanel() {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [savedKey, setSavedKey] = useState(false);
  const [checkingKey, setCheckingKey] = useState(true);
  const [step, setStep] = useState<RunpodSetupStep>('account');
  const setupQ = useQuery({ queryKey: queryKeys.cloudSetup, queryFn: api.cloudSetup, refetchInterval: 3000 });
  const [key, setKey] = useState('');
  const [models, setModels] = useState<ModelListResponse['models']>([]);
  const [modelId, setModelId] = useState('');
  const [text, setText] = useState('A short example of the speech I want to generate.');
  const [estimates, setEstimates] = useState<RunpodEstimate[]>([]);
  const [analytics, setAnalytics] = useState<RunpodAnalytics | null>(null);
  const [billing, setBilling] = useState<string>('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    void (async () => {
      try {
        const result = await api.runpodConnection();
        if (!current) return;
        setSavedKey(result.connected);
        if (result.connected) {
          const discovery = await api.cloudDiscover();
          if (!current) return;
          queryClient.setQueryData(['cloud-discovery'], discovery);
          setConnected(true);
          const setup = await api.cloudSetup();
          if (!current) return;
          queryClient.setQueryData(queryKeys.cloudSetup, setup);
          setStep(setup.ready ? 'ready' : setup.volume ? 'models' : 'storage');
        }
      } catch (cause) { if (current) setError(String(cause)); }
      finally { if (current) setCheckingKey(false); }
    })();
    void api.models().then((result) => {
      const supported = result.models.filter(model => model.runtime !== 'f5');
      setModels(supported);
      setModelId((current) => current || supported[0]?.id || '');
    }).catch((cause) => setError(String(cause)));
    return () => { current = false; };
  }, []);

  async function connect() {
    setBusy(true);
    setError('');
    try {
      await api.connectRunpod(key);
      setSavedKey(true);
      const discovery = await api.cloudDiscover();
      queryClient.setQueryData(['cloud-discovery'], discovery);
      setKey('');
      setConnected(true);
      setStep('storage');
      await queryClient.invalidateQueries({ queryKey: queryKeys.cloudSetup });
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    setError('');
    try {
      await api.disconnectRunpod();
      setConnected(false);
      setSavedKey(false);
      setStep('account');
      setAnalytics(null);
      setEstimates([]);
      await queryClient.invalidateQueries({ queryKey: queryKeys.cloudSetup });
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function checkSavedKey() {
    setBusy(true); setError('');
    try {
      queryClient.setQueryData(['cloud-discovery'], await api.cloudDiscover());
      setConnected(true); setStep('storage');
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }

  async function refresh() {
    setBusy(true);
    setError('');
    try {
      setAnalytics(await api.runpodAnalytics());
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function estimate() {
    setBusy(true);
    setError('');
    try {
      setEstimates((await api.runpodEstimate(modelId, text)).estimates);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function usage(kind: 'pod' | 'volume', id: string) {
    setBusy(true);
    setError('');
    try {
      const response = kind === 'pod'
        ? await api.runpodPodUsage(id) : await api.runpodVolumeUsage(id);
      setBilling(`${kind === 'pod' ? 'Pod' : 'Volume'} ${id}: ${money(response.totals.totalAmount, 2)} billed in the last 24 hourly buckets`);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  return <div className="runpod-panel">
    <nav className="runpod-wizard-nav" aria-label="Runpod setup">
      {(['account', 'storage', 'models', 'ready'] as RunpodSetupStep[]).map((value, index) =>
        <button type="button" key={value} aria-current={step === value ? 'step' : undefined}
          disabled={value !== 'account' && !connected || value === 'models' && !setupQ.data?.volume}
          onClick={() => setStep(value)}>
          <span>{index + 1}</span>{({ account: 'Account', storage: 'Storage', models: 'Models', ready: 'Ready' })[value]}
        </button>)}
    </nav>
    {step === 'account' && <section className="card runpod-account">
      <header className="card-head"><h2>Connect your Runpod account</h2></header>
      {checkingKey ? <p role="status">Checking your saved connection…</p> : <>
      {connected ? <>
        <p>Connected to Runpod.</p>
        <div className="runpod-wizard-footer"><button className="btn sm" disabled={busy} onClick={() => void disconnect()}>Disconnect</button>
          <button className="btn primary sm" disabled={busy} onClick={() => setStep('storage')}>Continue</button></div>
      </> : <div className="runpod-controls">
        {savedKey && <button className="btn sm" disabled={busy} onClick={() => void checkSavedKey()}>Check saved API key</button>}
        <label className="field"><span className="field-label">Runpod API key</span>
          <input type="password" value={key} onChange={(event) => setKey(event.target.value)}
            autoComplete="off" spellCheck={false} placeholder="Paste your Runpod API key" /></label>
        <button className="btn primary sm" disabled={busy || key.length < 8}
          onClick={() => void connect()}>{busy ? 'Connecting…' : 'Connect Runpod'}</button>
      </div>}
      </>}
      {error && <p role="alert" className="hint">{error}</p>}
    </section>}

    <CloudSetupPanel connected={connected} step={step} onStep={setStep} />

    {connected && step === 'ready' && <details className="card runpod-extra-settings"><summary>Cost estimates and account activity</summary>
      <section className="card">
        <header className="card-head"><h2>Estimated generation compute cost</h2></header>
        <p className="hint">Based on current Secure Cloud GPU rates and reference model timing. Idle Pod time and persistent storage are extra. Actual timing needs a GPU benchmark.</p>
        <div className="runpod-controls">
          <label className="field"><span className="field-label">Model</span>
            <select value={modelId} onChange={(event) => setModelId(event.target.value)}>
              {models.map((model) => <option value={model.id} key={model.id}>{model.display_name}</option>)}
            </select></label>
          <label className="field"><span className="field-label">Sample script</span>
            <textarea value={text} maxLength={6000} onChange={(event) => setText(event.target.value)} />
          </label>
          <button className="btn primary sm" disabled={busy || !modelId || !text.trim()}
            onClick={() => void estimate()}>Compare GPUs</button>
        </div>
        {estimates.length > 0 && <div className="runpod-scroll"><table>
          <thead><tr><th>GPU</th><th>VRAM</th><th>Availability</th><th>GPU/hour</th><th>Warm estimate</th><th>Cold estimate</th></tr></thead>
          <tbody>{estimates.map((row) => <tr key={row.gpu_id}>
            <td>{row.gpu_name}</td><td>{row.vram_gb} GB</td><td>{row.availability}</td>
            <td>{money(row.hourly_gpu_usd, 2)}</td>
            <td>{money(row.warm_estimated_compute_usd, 6)}</td>
            <td>{money(row.cold_estimated_compute_usd, 6)}</td>
          </tr>)}</tbody>
        </table></div>}
        {estimates.length === 0 && <p className="hint">GPU availability is checked again before generation. Estimates are approximate until your first generation is measured.</p>}
      </section>

      <section className="card">
        <header className="card-head"><h2>Pod and volume analytics</h2></header>
        <button className="btn sm" disabled={busy} onClick={() => void refresh()}>Refresh account</button>
        {analytics && <>
          <h3>Pods</h3>
          {analytics.pods.length === 0 && <p className="hint">No Pods in this account.</p>}
          {analytics.pods.map((pod) => <p key={pod.id}>
            {pod.name} · {pod.status} · {pod.gpu?.id ?? 'GPU unknown'} · {money(pod.cost, 2)}/hour{' '}
            <button className="btn sm" disabled={busy} onClick={() => void usage('pod', pod.id)}>Billing</button>
          </p>)}
          <h3>Persistent volumes</h3>
          {analytics.volumes.length === 0 && <p className="hint">No network volumes in this account.</p>}
          {analytics.volumes.map((volume) => <p key={volume.id}>
            {volume.name} · {volume.size} GB · {volume.type}{' '}
            <button className="btn sm" disabled={busy} onClick={() => void usage('volume', volume.id)}>Billing</button>
          </p>)}
          {billing && <p aria-live="polite">{billing}</p>}
        </>}
      </section>
    </details>}
  </div>;
}
