import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

// Render the real updater view with seeded hook values. Native commands are
// prohibited: transport and signatures are exercised by the Rust tests.
const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/components/DesktopUpdates.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
});
function render(stage, resumable = true) {
  let index = 0;
  const states = ['0.1.3', { version: '0.1.4', downloadState: stage, downloaded: 8e6, total: 16e6, resumable }, stage, '', { downloaded: 8e6, total: 16e6 }];
  const exports = {};
  runInNewContext(compiled.outputText, {
    exports,
    require(name) {
      if (name === 'react') return { ...React, useEffect() {}, useRef: value => ({ current: value }), useState: () => [states[index++], () => {}] };
      if (name === 'react/jsx-runtime') return require(name);
      if (name === '@tauri-apps/api/core') return { isTauri: () => true, invoke: () => { throw new Error('Native calls are not allowed in SSR tests'); } };
      if (name.endsWith('.css')) return {};
      throw new Error('Unexpected dependency: ' + name);
    },
  });
  return renderToStaticMarkup(React.createElement(exports.DesktopUpdates));
}
const active = render('downloading');
assert.match(active, />Cancel download<\/button>/);
assert.match(active, /<progress[^>]*value="50"/);
assert.match(active, /<button[^>]*>Close<\/button>/);
assert.doesNotMatch(active, /<button[^>]*disabled[^>]*>Close<\/button>/);
const paused = render('paused');
assert.match(paused, /8\.0 MB saved on this PC/);
assert.match(paused, />Resume download<\/button>/);
assert.doesNotMatch(paused, />Cancel download<\/button>|<progress/);
const unsupported = render('paused', false);
assert.match(unsupported, /next attempt starts a fresh download/);
assert.match(unsupported, />Download update<\/button>/);
const stopping = render('canceling');
assert.match(stopping, /Stopping the update download and keeping saved bytes/);
assert.match(stopping, /<button[^>]*disabled[^>]*>Stopping…<\/button>/);
assert.doesNotMatch(stopping, /Resume download|<progress/);
const saved = render('downloaded');
assert.match(saved, />Restart and update<\/button>/);
assert.match(saved, /will be reused after closing the app/);
assert.doesNotMatch(saved, />Download update<\/button>/);
console.log('Desktop update SSR checks passed: Cancel, paused resume, safe fresh download, stopping and persisted installer states; no native calls.');
