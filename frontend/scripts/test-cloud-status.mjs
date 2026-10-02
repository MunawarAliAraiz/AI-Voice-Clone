import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/cloudStatus.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const exports = {};
runInNewContext(compiled.outputText, { exports });
const status = exports.headerStatus;
const ready = { connected: true, ready: true, release_available: true, policy: {}, models: {}, compute: null };

// A healthy local API must never mask missing credentials or incomplete cloud setup.
assert.equal(status(true, true, { ...ready, connected: false }, false).label, 'Runpod not connected');
assert.equal(status(true, true, { ...ready, ready: false }, false).label, 'Runpod setup needed');
assert.equal(status(true, true, { ...ready, policy: null }, false).tone, '');
assert.equal(status(true, true, { ...ready, release_available: false }, false).tone, '');
assert.equal(status(true, true, undefined, false).label, 'Checking Runpod');
assert.equal(status(true, true, ready, true).tone, 'down');
assert.equal(status(true, false, ready, false).label, 'Local service unavailable');
assert.equal(status(true, true, { ...ready, models: { voice: { state: 'failed' } } }, false).tone, 'down');
assert.equal(status(true, true, { ...ready, compute: { kind: 'installer', status: 'starting' } }, false).label, 'Preparing models');
assert.equal(status(true, true, ready, false).label, 'Ready to generate');
assert.match(status(true, true, ready, false).detail, /No GPU session is active/);
assert.equal(status(true, true, { ...ready, compute: { kind: 'generation', status: 'ready' } }, false).label, 'GPU session active');
assert.equal(status(false, true, undefined, false).label, 'Online');
assert.equal(status(false, null, undefined, false).label, 'Connecting');
assert.equal(status(false, false, undefined, false).label, 'Offline');
console.log('15 cloud/header status checks passed.');
