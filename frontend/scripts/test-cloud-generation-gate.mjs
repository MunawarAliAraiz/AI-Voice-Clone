import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/cloudGenerationGate.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const exports = {};
runInNewContext(compiled.outputText, { exports });
const gate = (setup, error = false) => exports.deriveCloudGenerationGate({ desktop: true, setup, error });
const ready = {
  connected: true, ready: true, release_available: true,
  volume: { id: 'volume', size: 60, type: 'STANDARD' }, storage_min_gb: 60,
  policy: { max_session_usd: 1, max_hourly_usd: 2 }, models: {}, compute: null,
};

// Missing setup fails closed; stale readiness cannot hide a failed status query.
assert.equal(gate(undefined).blocked, true);
assert.match(gate(undefined).reason, /Checking Runpod/);
assert.equal(gate(ready, true).blocked, true);
assert.match(gate(ready, true).reason, /could not be checked/);
assert.match(gate({ ...ready, connected: false }).reason, /API key/);
assert.match(gate({ ...ready, volume: null }).reason, /persistent model storage/);
assert.equal(gate({ ...ready, volume: { ...ready.volume, size: 50 } }).blocked, true);
assert.equal(gate({ ...ready, storage_min_gb: 80 }).blocked, true);
assert.match(gate({ ...ready, capacity: { sufficient: false } }).reason, /more free space/);
assert.equal(gate({ ...ready, volume: { ...ready.volume, type: 'HIGH_PERFORMANCE' } }).blocked, true);
assert.equal(gate({ ...ready, release_available: false }).action, 'updates');
assert.equal(gate({ ...ready, release_available: false }).actionLabel, 'Check app updates');
assert.match(gate({ ...ready, models: { voice: { state: 'failed' } } }).reason, /failed/);
assert.match(gate({ ...ready, compute: { status: 'failed' } }).reason, /failed/);
assert.match(gate({ ...ready, ready: false }).reason, /Finish model setup/);
assert.match(gate({ ...ready, ready: false, models: { voice: { state: 'verifying' } } }).reason, /Preparing models/);
assert.match(gate({ ...ready, compute: { kind: 'installer' }, progress_pct: 42 }).reason, /42%/);
assert.match(gate({ ...ready, compute: { kind: 'installer' }, progress_pct: 120 }).reason, /100%/);
assert.doesNotMatch(gate({ ...ready, compute: { kind: 'installer' }, progress_pct: NaN }).reason, /NaN/);
assert.match(gate({ ...ready, policy: null }).reason, /spending limit/);
assert.match(gate({ ...ready, setup_phase: 'failed', setup_error: 'Start failed' }).reason, /failed/);
assert.match(gate({ ...ready, ready: false, setup_phase: 'starting_worker' }).reason, /Preparing your setup/);
assert.match(gate({ ...ready, setup_phase: 'cancelling' }).reason, /Pausing setup/);
assert.match(gate({ ...ready, setup_phase: 'cancelled' }).reason, /paused.*continue/);
assert.match(gate({ ...ready, setup_phase: 'cancelled', cleanup_pending: true }).reason, /machine to stop/);
assert.equal(gate(ready).blocked, false);
assert.equal(gate({ ...ready, compute: { kind: 'generation', status: 'starting' } }).blocked, false);
assert.equal(exports.deriveCloudGenerationGate({ desktop: false, error: true }).blocked, false);
const audioBlocked = exports.deriveCloudGenerationGate({ desktop: true, setup: ready, audioTools: { ready: false, detail: 'Audio download failed.' } });
assert.equal(audioBlocked.blocked, true);
assert.match(audioBlocked.reason, /Audio download failed.*retry/);
assert.equal(audioBlocked.action, null);
assert.equal(exports.deriveCloudGenerationGate({ desktop: true, setup: ready, audioTools: { ready: true } }).blocked, false);
console.log('32 cloud generation readiness/action checks passed.');
