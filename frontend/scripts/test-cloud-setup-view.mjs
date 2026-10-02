import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as reactQuery from '@tanstack/react-query';
import ts from 'typescript';
const require = createRequire(import.meta.url);
let apiCalls = 0;
const api = new Proxy({}, { get: () => () => { apiCalls++; throw new Error('No provider calls in SSR'); } });
function load(file, dependencies = {}) {
  const source = readFileSync(new URL(`../src/components/${file}.tsx`, import.meta.url), 'utf8');
  const result = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } });
  const exports = {};
  runInNewContext(result.outputText, { exports, require(name) {
    if (name === '../services/api') return { api };
    if (name === '@tanstack/react-query') return reactQuery;
    if (['react', 'react/jsx-runtime'].includes(name)) return require(name);
    if (name in dependencies) return dependencies[name];
    throw new Error(name);
  } });
  return exports;
}
const cloud = load('CloudSetupPanel');
const runpod = load('RunpodPanel', { './CloudSetupPanel': cloud, '../hooks/queries': { queryKeys: { cloudSetup: ['cloud-setup'] } } });
const volume = { id: 'voice', name: 'Voice Clone Studio', size: 60, dataCenter: 'US-NE-1', type: 'STANDARD', app_owned: true };
const discovery = { volumes: [volume, { id: 'video', name: 'Video Studio', size: 100, dataCenter: 'US-NE-1', type: 'STANDARD' }],
  regions: [{ id: 'US-NE-1', name: 'US-NE-1', gpu_hourly_from_usd: 1.09 }], balance_usd: 9, storage_min_gb: 60 };
const base = { connected: true, ready: false, release_available: true, volume, required_model_ids: ['voxcpm2', 'chatterbox_ml_v3'],
  policy: { max_session_usd: 3.25, max_hourly_usd: 1.5 }, models: {}, detail: '', setup_phase: 'idle', setup_running: false,
  setup_error: null, cleanup_pending: false, auto_setup_enabled: true, compute: null };
function render(changes = {}, step = 'models', target = cloud.CloudSetupPanel, audioTools = { ready: true, stage: 'ready' }) {
  const client = new reactQuery.QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity } } });
  client.setQueryData(['cloud-setup'], { ...base, ...changes }); client.setQueryData(['cloud-discovery'], discovery);
  client.setQueryData(['audio-tools'], audioTools);
  try { return renderToStaticMarkup(React.createElement(reactQuery.QueryClientProvider, { client }, React.createElement(target, { connected: true, step }))); }
  finally { client.clear(); }
}
const storage = render({}, 'storage');
assert.match(storage, /Available volumes/); assert.match(storage, /Video Studio · 100 GB/); assert.match(storage, /Create a new volume/);
assert.match(storage, /Use this volume/); assert.doesNotMatch(storage, /200 GB|Your models|Ready to generate|<details\b[^>]*\bopen(?:[=\s>])/);
const empty = render({ volume: null }, 'storage');
assert.match(empty, /Create a 60 GB volume/); assert.match(empty, /10 GB of spare space/); assert.match(empty, /Review storage cost/);
const starting = render({ setup_phase: 'starting_worker', setup_running: true });
assert.match(starting, /Preparing your setup/); assert.match(starting, /Pause downloads/); assert.match(starting, /VoxCPM 2/);
assert.doesNotMatch(starting, /<progress\b|Start machine|Download setup software|Advanced model/);
const transfer = { setup_phase: 'downloading', setup_running: true, models: { voxcpm2: { state: 'ready' },
  chatterbox_ml_v3: { state: 'downloading', progress_pct: 40, bytes_completed: 2e9, bytes_total: 5e9 } } };
const downloading = render(transfer);
assert.match(downloading, /aria-label="Chatterbox Multilingual download progress" max="100" value="40"/);
assert.match(downloading, /40% · 2\.00 GB \/ 5\.00 GB/); assert.match(downloading, /VoxCPM 2<\/strong><span>Ready/);
assert.doesNotMatch(downloading, /Storage for your voices|Ready to generate/);
for (const model of [{ state: 'downloading', progress_pct: NaN, bytes_total: 5e9 }, { state: 'downloading', progress_pct: 40 }])
  assert.doesNotMatch(render({ ...transfer, models: { chatterbox_ml_v3: model } }), /<progress\b/);
const paused = render({ ...transfer, setup_phase: 'cancelled', setup_running: false, auto_setup_enabled: false });
assert.match(paused, /Downloads paused/); assert.match(paused, /Resume downloads/); assert.match(paused, /value="40"/);
assert.doesNotMatch(paused, /valid downloaded files|incomplete files resume|Retrying checks|Cancel model setup|Advanced model/);
const pending = render({ setup_phase: 'failed', cleanup_pending: true, setup_error: 'Runpod start could not be confirmed.',
  compute: { pod_id: null, kind: 'installer', status: 'provisioning', hourly_usd: .06, deadline: '2026-10-03T00:00:00Z', creation_confirmed: false } });
assert.match(pending, /Check pending start/); assert.match(pending, /Error details/); assert.match(pending, /Runpod start could not be confirmed/);
assert.doesNotMatch(pending, /Resume downloads|Continue setup|<progress\b/);
const waiting = render({ auto_setup_waiting: true }); assert.match(waiting, /Waiting for an available machine/); assert.match(waiting, /Pause downloads/);
const ready = render({ ready: true, setup_phase: 'ready' }, 'ready');
assert.match(ready, /Ready to generate/); assert.match(ready, /\$3\.25 maximum per session/); assert.match(ready, /GPU up to \$1\.50\/hour/);
assert.match(ready, /Advanced generation settings/); assert.match(ready, /Open Voice Studio/); assert.doesNotMatch(ready, /Approve these limits|Your models|Storage for your voices/);
const appPreparing = render({ ready: true, setup_phase: 'ready' }, 'ready', cloud.CloudSetupPanel,
  { ready: false, stage: 'downloading', progress_pct: 25, bytes_completed: 25e6, bytes_total: 100e6 });
assert.match(appPreparing, /Preparing your app/); assert.match(appPreparing, /aria-label="App setup progress" max="100" value="25"/);
assert.match(appPreparing, /disabled=""[^>]*>Open Voice Studio/); assert.doesNotMatch(appPreparing, /Ready to generate/);
assert.match(appPreparing, /App setup must finish before you generate/); assert.doesNotMatch(appPreparing, /FFmpeg/);
const appFailed = render({ ready: true }, 'ready', cloud.CloudSetupPanel,
  { ready: false, stage: 'failed', detail: 'Download connection failed.', progress_pct: 0, bytes_total: 0 });
assert.match(appFailed, /Continue app setup/); assert.match(appFailed, /Error details/);
assert.doesNotMatch(appFailed, /Ready to generate|<progress\b/);
const account = render({}, 'account', runpod.RunpodPanel);
assert.match(account, /aria-label="Runpod setup"/); assert.match(account, /Checking your saved connection/);
assert.doesNotMatch(account, /Storage for your voices|Your models|Ready to generate/);
assert.equal(apiCalls, 0);
console.log('Wizard SSR passed: single pages, storage choices, calculated minimum, real model progress, pauses, cleanup, limits and initial key check. No provider calls.');
