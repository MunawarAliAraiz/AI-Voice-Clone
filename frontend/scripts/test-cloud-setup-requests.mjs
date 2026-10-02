import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

// Execute the real API client; provide the Vite build environment in Node.
const source = readFileSync(new URL('../src/services/api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  transformers: { before: [context => {
    const visit = node => {
      if (ts.isPropertyAccessExpression(node) && ts.isMetaProperty(node.expression)
          && node.expression.keywordToken === ts.SyntaxKind.ImportKeyword && node.name.text === 'env') {
        return ts.factory.createIdentifier('__viteEnv');
      }
      return ts.visitEachChild(node, visit, context);
    };
    return node => ts.visitNode(node, visit);
  }] },
});
const exports = {};
const emitted = [];
runInNewContext(compiled.outputText, {
  exports, Headers, console, __viteEnv: {},
  window: { __VCS_DESKTOP_KEY__: 'isolated-test-session' },
  async fetch(path, init) {
    // Request applies the same default body Content-Type as browser fetch.
    const request = new Request(new URL(path, 'http://127.0.0.1:12345'), init);
    const body = await request.text();
    emitted.push({ request, body });
    // FastAPI's JSON model expects an object, not a text/plain string body.
    if (request.headers.get('Content-Type') !== 'application/json') {
      return new Response(JSON.stringify({ code: 'VALIDATION', detail: 'The request body failed validation.' }), {
        status: 422, headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response(JSON.stringify({ auto_setup_enabled: JSON.parse(body).enabled }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  },
});

for (const enabled of [true, false, true]) {
  // Covers enabling automatic downloads, pausing the intent and resuming it.
  const result = await exports.api.cloudAutoSetup(enabled);
  assert.equal(result.auto_setup_enabled, enabled);
  const { request, body } = emitted.at(-1);
  assert.equal(request.url, 'http://127.0.0.1:12345/api/runpod/setup/auto');
  assert.equal(request.method, 'PUT');
  assert.equal(request.headers.get('Content-Type'), 'application/json');
  assert.equal(request.headers.get('X-API-Key'), 'isolated-test-session');
  assert.deepEqual(JSON.parse(body), { enabled });
}
assert.equal(emitted.length, 3);
console.log('Automatic setup request regression passed: enable, pause and resume send authenticated JSON. No provider calls.');
