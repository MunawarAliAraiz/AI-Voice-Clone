import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as reactQuery from '@tanstack/react-query';
import ts from 'typescript';

// Render the actual component against seeded API responses. No browser,
// provider requests, purchase, model download or machine operation is used.
const require = createRequire(import.meta.url);
const { QueryClient, QueryClientProvider } = reactQuery;
const source = readFileSync(new URL('../src/components/CloudSetupPanel.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
});
let apiCalls = 0;
const api = new Proxy({}, {
  get: () => () => { apiCalls += 1; throw new Error('SSR checks must not call the API'); },
});
const exports = {};
runInNewContext(compiled.outputText, {
  exports,
  require(name) {
    if (name === '../services/api') return { api };
    // Share the provider's ESM instance; the package's CJS export has a
    // separate context and cannot consume the provider imported above.
    if (name === '@tanstack/react-query') return reactQuery;
    if (['react', 'react/jsx-runtime'].includes(name)) return require(name);
    throw new Error('Unexpected component dependency: ' + name);
  },
});

const base = {
  connected: true, stage: 'download_models', ready: false, release_available: true,
  volume: { id: 'volume', name: 'Existing storage', size: 200, dataCenter: 'US-NE-1', type: 'STANDARD' },
  policy: { max_session_usd: 3.25, max_hourly_usd: 1.5 },
  progress_pct: null, bytes_completed: 0, bytes_total: null, models: {}, detail: '',
  setup_phase: 'idle', setup_running: false, setup_error: null, cleanup_pending: false,
  compute: null,
};
const machine = {
  pod_id: 'pod', kind: 'installer', status: 'starting', hourly_usd: .06,
  deadline: '2026-10-03T00:00:00Z', creation_confirmed: true,
};
function render(changes) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } } });
  client.setQueryData(['cloud-setup'], { ...base, ...changes });
  try {
    return renderToStaticMarkup(React.createElement(QueryClientProvider, { client },
      React.createElement(exports.CloudSetupPanel, { connected: true })));
  } finally { client.clear(); }
}
function closedAdvanced(html) {
  assert.match(html, /<summary>Advanced storage settings<\/summary>/);
  assert.match(html, /<summary>Advanced generation settings<\/summary>/);
  assert.doesNotMatch(html, /<details\b[^>]*\bopen(?:[=\s>])/);
}

const failed = render({
  setup_phase: 'failed', setup_error: 'Runpod start failed; try again after checking the pending attempt.',
  cleanup_pending: true, detail: 'Cloud release is pending',
  compute: { ...machine, pod_id: null, creation_confirmed: false, status: 'provisioning' },
  progress_pct: 12.5, bytes_completed: 125, bytes_total: 1000,
  models: { voxcpm2: { state: 'downloading', progress_pct: 12.5, current_file: 'weights/model.safetensors' } },
});
assert.doesNotMatch(failed, /<progress\b/);
assert.match(failed, /Automatic model preparation needs attention/);
assert.match(failed, /Runpod start failed/);
assert.match(failed, /Machine start not confirmed/);
assert.match(failed, /Estimated rate: \$0\.06/);
assert.match(failed, /Requested stop-by time:/);
assert.doesNotMatch(failed, /Automatic stop-by time:/);
assert.match(failed, /Check pending start/);
assert.match(failed, /<strong>VoxCPM 2<\/strong> · Stopped · Last reported: 12\.5%/);
assert.doesNotMatch(failed, /<strong>VoxCPM 2<\/strong> · Downloading/);
assert.match(failed, /<button[^>]*disabled=""[^>]*>Resume after resolving the error/);
closedAdvanced(failed);

const starting = render({ setup_phase: 'starting_worker', setup_running: true, compute: machine });
assert.doesNotMatch(starting, /<progress\b/);
assert.match(starting, /downloading its software\. Model files have not started downloading yet/);
assert.match(starting, /Storage connected/);
assert.match(starting, /Current: Start machine/);
assert.match(starting, />Cancel model setup<\/button>/);
closedAdvanced(starting);

const transfer = {
  setup_phase: 'downloading', setup_running: true, compute: { ...machine, status: 'running' },
  bytes_completed: 2e9, bytes_total: 5e9, progress_pct: 40,
  models: { chatterbox_ml_v3: { state: 'downloading', progress_pct: 40, current_file: 'weights/model.safetensors' } },
};
const downloading = render(transfer);
assert.match(downloading, /<progress\b[^>]*max="100"[^>]*value="40"/);
assert.match(downloading, /40\.0% downloaded · 2\.00 GB \/ 5\.00 GB/);
assert.match(downloading, /<strong>Chatterbox Multilingual<\/strong> · Downloading · 40\.0%/);
assert.match(downloading, /Current: Download models/);
assert.match(downloading, /<summary>Advanced model details<\/summary>[\s\S]*weights\/model\.safetensors/);
assert.match(downloading, /Approved limits:<\/strong> \$3\.25 per session · GPU up to \$1\.50\/hour/);
assert.doesNotMatch(downloading, />Approve these limits<\/button>/);
closedAdvanced(downloading);

// Unknown totals and non-finite percentages cannot produce a fake moving bar.
assert.doesNotMatch(render({ ...transfer, bytes_total: null }), /<progress\b/);
assert.doesNotMatch(render({ ...transfer, progress_pct: NaN }), /<progress\b/);
const cancelled = render({ ...transfer, setup_phase: 'cancelled', setup_running: false, compute: null, auto_setup_enabled: false });
assert.doesNotMatch(cancelled, /<progress\b/);
assert.match(cancelled, /Model setup cancelled/);
assert.match(cancelled, /valid downloaded files are reused/);
assert.match(cancelled, /Resume automatic downloads/);
assert.match(cancelled, /Stopped · Last reported: 40\.0%/);
assert.doesNotMatch(cancelled, />Cancel model setup<\/button>/);
const cancelling = render({ ...transfer, setup_phase: 'cancelling' });
assert.doesNotMatch(cancelling, /<progress\b/);
assert.match(cancelling, /Stopping model setup and releasing its temporary machine/);
assert.doesNotMatch(cancelling, />Cancel model setup<\/button>/);
const pendingCleanup = render({ ...transfer, setup_phase: 'cancelled', setup_running: false,
  cleanup_pending: true, setup_error: 'Machine stop still pending' });
assert.match(pendingCleanup, /Runpod has not confirmed that the rented machine stopped/);
assert.doesNotMatch(pendingCleanup, />Resume automatic downloads/);
assert.match(pendingCleanup, /Machine stop still pending/);
const waiting = render({ auto_setup_enabled: true, auto_setup_waiting: true });
assert.match(waiting, /check again automatically/);
assert.match(waiting, />Cancel model setup<\/button>/);
assert.doesNotMatch(waiting, /Retry model setup|Resume automatic downloads|<progress\b/);
assert.equal(apiCalls, 0);
console.log('Cloud setup SSR checks passed: failed/pending, startup, real transfer, collapsed advanced settings and saved budgets; no API calls.');
