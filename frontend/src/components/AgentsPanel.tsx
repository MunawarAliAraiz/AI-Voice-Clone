import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { agentsApi } from '../services/agents';
import type { VoiceProfile } from '../types/api';

const example = 'Use Voice Clone Studio to generate my script. First ask me for the script, language, reference voice and delivery (for example calm, slow narration). List voices and confirm my chosen voice ID. Preview supported Speech Direction before queuing. Return the queued job ID, check progress and tell me when the output is ready. Do not claim success before the job succeeds.';

export function AgentsPanel({ voices, onOpenRecent }: { voices: VoiceProfile[]; onOpenRecent: () => void }) {
  const status = useQuery({ queryKey: ['agent-connections'], queryFn: agentsApi.status, refetchInterval: 5000 });
  const [busy, setBusy] = useState<string | null>(null);
  const [replacements, setReplacements] = useState<Record<string, boolean>>({});
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  async function connect(client: string) {
    setBusy(client); setError(''); setNotice('');
    try {
      await agentsApi.configure(client, !!replacements[client]);
      await status.refetch();
      setNotice('Server configured. Restart that agent client, then ask it to call studio_health. A saved setting does not confirm a live connection.');
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(null); }
  }
  return <div className="agents-panel">
    <section className="card">
      <header className="card-head"><h2>Connect your agent</h2></header>
      <p className="hint">Codex and Claude can use this app's local MCP tools. Keep the studio open. Only the studio server entry is added to the selected client; your Runpod key stays in the studio.</p>
      <button type="button" className="btn sm" disabled={status.isFetching || !!busy} onClick={() => void status.refetch()}>Check clients again</button>
      {status.data && !status.data.mcp_available && <p className="hint">The installed MCP executable is unavailable in this preview. Install the desktop app to configure a client.</p>}
      <div className="agent-clients">
        {(status.data?.clients ?? []).map(client => <article className="agent-client" key={client.id}>
          <div className="agent-client-head"><h3>{client.name}</h3><span className="muted">{client.configured ? 'Configured' : client.detected ? 'Detected locally' : 'Not detected'}</span></div>
          <p className="hint agent-path">{client.config_path}</p>
          {!client.detected && <p className="hint">Install and open this client, then check again.</p>}
          {client.conflict && <label className="agent-replace"><input type="checkbox" checked={!!replacements[client.id]} onChange={e => setReplacements(v => ({ ...v, [client.id]: e.target.checked }))} />Replace the existing studio server entry (a backup is kept).</label>}
          {client.error && <p className="hint" role="alert">{client.error}</p>}
          <button type="button" className="btn sm" disabled={!!busy || !client.can_configure || client.configured || (client.conflict && !replacements[client.id])} onClick={() => void connect(client.id)}>
            {busy === client.id ? 'Saving…' : client.configured ? 'Configured' : 'Connect ' + client.name}
          </button>
        </article>)}
      </div>
      {notice && <p role="status">{notice}</p>}
      {(error || status.error) && <p role="alert">{error || String(status.error)}</p>}
      <p className="hint">{status.data?.activity ? 'Last MCP tool call: ' + new Date(status.data.activity.last_tool_call_at).toLocaleString() + '. This is shared activity, not a per-client connection test.' : 'No MCP tool call has been observed yet. Ask the configured client to check studio_health.'}</p>
    </section>
    <section className="card">
      <header className="card-head"><h2>Ask your agent to generate speech</h2></header>
      <p>The agent should confirm the script, language, reference voice and delivery before adding work to the queue. Jobs then appear in Recent and the studio shows progress and completion.</p>
      <blockquote className="agent-example">{example}</blockquote>
      <button className="btn sm" onClick={() => void navigator.clipboard.writeText(example).then(() => setNotice('Example prompt copied.')).catch(() => setError('Clipboard unavailable; select the example text to copy it.'))}>Copy example prompt</button>{' '}
      <button className="btn sm" onClick={onOpenRecent}>Open queue and results</button>
      <h3>Reference voice → generated speech</h3>
      <p className="hint">For text generation, a reference voice is the enrolled voice used to shape the output. The generated audio is a new result; there is no source recording to convert in this workflow. Recorded voice conversion has separate source and target choices.</p>
      {voices.length === 0 ? <p className="hint">Add a reference voice in Voice Studio before asking an agent to generate speech.</p> :
        <ul className="agent-voices">{voices.map(voice => <li key={voice.id}>{voice.name} · reference voice ID {voice.id} · {voice.language}</li>)}</ul>}
      <p className="hint">Queueing uses your existing Runpod readiness and spending limits. Delivery fields are only applied when the chosen model supports them; direction preview reports that support.</p>
    </section>
  </div>;
}
