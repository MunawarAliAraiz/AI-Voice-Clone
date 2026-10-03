import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as jsxRuntime from 'react/jsx-runtime';

const source = readFileSync(new URL('../src/components/LiquidOrb.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const load = (react, environment = {}) => {
  const exports = {};
  runInNewContext(compiled, {
    exports,
    require: name => name === 'react' ? react : name === 'react/jsx-runtime' ? jsxRuntime : {},
    ...environment,
  });
  return exports.LiquidOrb;
};
const Component = load(React);
const states = ['idle', 'queued', 'generating', 'complete', 'blocked', 'error', 'paused'];
for (const state of states) {
  const html = renderToStaticMarkup(React.createElement(Component, { state, className: 'studio-orb' }));
  assert.match(html, /aria-hidden="true"/);
  assert.match(html, new RegExp(`data-state="${state}"`));
  assert.match(html, /class="liquid-orb studio-orb"/);
  assert.match(html, /liquid-orb__material/);
  assert.match(html, /width="128" height="128"/);
  assert.doesNotMatch(html, /role="status"|microphone|https:/);
}

function harness(state, { reduced = false, glAvailable = true, shaderValid = true, hidden = false } = {}) {
  let cleanup;
  let drawCount = 0;
  let layoutReads = 0;
  let id = 0;
  let deleted = 0;
  let observerDisconnected = 0;
  const frames = new Map();
  const canvasEvents = new Map();
  const documentEvents = new Map();
  const motionEvents = new Map();
  const gl = new Proxy({
    getShaderParameter: () => shaderValid,
    getProgramParameter: () => true,
    getAttribLocation: () => 0,
    drawArrays: () => { drawCount += 1; },
    deleteShader: () => { deleted += 1; },
    deleteBuffer: () => { deleted += 1; },
    deleteProgram: () => { deleted += 1; },
  }, { get: (target, key) => key in target ? target[key] : key.startsWith('create') || key === 'getUniformLocation' ? () => ({}) : key.toUpperCase() === key ? 1 : () => {} });
  const canvas = {
    dataset: {}, width: 128, height: 128,
    getContext: () => glAvailable ? gl : null,
    getBoundingClientRect: () => { layoutReads += 1; return { width: 300, height: 300, top: 0, bottom: 300 }; },
    addEventListener: (name, fn) => canvasEvents.set(name, fn),
    removeEventListener: name => canvasEvents.delete(name),
  };
  const doc = {
    hidden,
    addEventListener: (name, fn) => documentEvents.set(name, fn),
    removeEventListener: name => documentEvents.delete(name),
  };
  const motion = {
    matches: reduced,
    addEventListener: (name, fn) => motionEvents.set(name, fn),
    removeEventListener: name => motionEvents.delete(name),
  };
  let intersectionCallback;
  let resizeCallback;
  const FakeReact = {
    useRef: () => ({ current: canvas }),
    useEffect: fn => { cleanup = fn(); },
  };
  load(FakeReact, {
    window: { devicePixelRatio: 4, innerHeight: 1000, matchMedia: () => motion },
    document: doc,
    requestAnimationFrame: fn => { frames.set(++id, fn); return id; },
    cancelAnimationFrame: frame => frames.delete(frame),
    IntersectionObserver: class {
      constructor(callback) { intersectionCallback = callback; }
      observe() {}
      disconnect() { observerDisconnected += 1; }
    },
    ResizeObserver: class {
      constructor(callback) { resizeCallback = callback; }
      observe() {}
      disconnect() { observerDisconnected += 1; }
    },
  })({ state });
  return {
    canvas, frames, doc, motion, canvasEvents, documentEvents, motionEvents,
    get draws() { return drawCount; },
    get reads() { return layoutReads; },
    get deleted() { return deleted; },
    get disconnected() { return observerDisconnected; },
    visible: flag => intersectionCallback?.([{ isIntersecting: flag }]),
    resize: () => resizeCallback?.(),
    frame: timestamp => {
      const pending = [...frames.values()];
      frames.clear();
      pending.forEach(fn => fn(timestamp));
    },
    cleanup: () => cleanup?.(),
  };
}

const active = harness('generating');
assert.equal(active.frames.size, 0, 'No animation until element is visible');
active.visible(true);
assert.equal(active.canvas.width, 192, 'Buffer cap survives a large display and high DPR');
assert.equal(active.canvas.height, 192);
assert.equal(active.canvas.dataset.rendered, 'true');
assert.equal(active.frames.size, 1);
const initialReads = active.reads;
active.frame(0);
const firstFrameDraws = active.draws;
active.frame(10);
assert.equal(active.draws, firstFrameDraws, 'Does not draw at 100 fps');
active.frame(50);
assert.equal(active.draws, firstFrameDraws + 1);
assert.equal(active.reads, initialReads, 'Animation frames do not read layout');
active.visible(false);
assert.equal(active.frames.size, 0, 'Offscreen animation stops scheduling');
const offscreenDraws = active.draws;
active.frame(1000);
assert.equal(active.draws, offscreenDraws);
active.visible(true);
active.doc.hidden = true;
active.documentEvents.get('visibilitychange')();
assert.equal(active.frames.size, 0, 'Hidden document stops scheduling');
active.doc.hidden = false;
active.documentEvents.get('visibilitychange')();
assert.equal(active.frames.size, 1);
active.motion.matches = true;
active.motionEvents.get('change')();
assert.equal(active.frames.size, 0, 'Changing reduced motion stops scheduling');
active.motion.matches = false;
active.motionEvents.get('change')();
assert.equal(active.frames.size, 1);
let prevented = false;
active.canvasEvents.get('webglcontextlost')({ preventDefault: () => { prevented = true; } });
assert.equal(prevented, true);
assert.equal(active.frames.size, 0, 'Context loss cancels animation');
assert.equal(active.canvas.dataset.rendered, undefined, 'Context loss exposes CSS fallback');
active.cleanup();
assert.equal(active.deleted, 4, 'Deletes two shaders, program and buffer');
assert.equal(active.disconnected, 2);
assert.equal(active.canvasEvents.size + active.documentEvents.size + active.motionEvents.size, 0);

for (const state of ['complete', 'blocked', 'error', 'paused']) {
  const staticOrb = harness(state);
  staticOrb.visible(true);
  assert.equal(staticOrb.draws, 1);
  assert.equal(staticOrb.frames.size, 0, `${state} stays static`);
  staticOrb.cleanup();
}
for (const state of ['idle', 'queued', 'generating']) {
  const still = harness(state, { reduced: true });
  still.visible(true);
  assert.equal(still.draws, 1);
  assert.equal(still.frames.size, 0, `${state} respects reduced motion`);
  still.cleanup();
}
for (const options of [{ glAvailable: false }, { shaderValid: false }]) {
  const fallback = harness('generating', options);
  assert.equal(fallback.frames.size, 0);
  assert.equal(fallback.canvas.dataset.rendered, undefined);
  fallback.cleanup();
}
const hiddenOrb = harness('generating', { hidden: true });
hiddenOrb.visible(true);
assert.equal(hiddenOrb.draws, 0);
assert.equal(hiddenOrb.frames.size, 0);
hiddenOrb.cleanup();
console.log('Liquid orb: seven SSR states, animation lifecycle, frame throttle, GPU cleanup, buffer cap and static fallbacks passed.');
