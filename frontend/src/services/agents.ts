export interface AgentClient {
  id: string; name: string; detected: boolean; configured: boolean; conflict: boolean;
  config_path: string; can_configure: boolean; error: string | null;
}
export interface AgentConnections {
  clients: AgentClient[]; mcp_available: boolean;
  activity: { last_tool_call_at: string; tool: string } | null;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const key = window.__VCS_DESKTOP_KEY__;
  if (!key) throw new Error('Agent connections require the desktop app.');
  const response = await fetch('/api/agents' + path, {
    ...init, headers: { 'X-API-Key': key, 'Content-Type': 'application/json' },
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || 'Agent configuration is unavailable.');
  return value as T;
}
export const agentsApi = {
  status: () => call<AgentConnections>(''),
  configure: (client: string, replaceExisting: boolean) => call<{ configured: boolean }>('/configure/' + encodeURIComponent(client), {
    method: 'POST', body: JSON.stringify({ replace_existing: replaceExisting }),
  }),
};
