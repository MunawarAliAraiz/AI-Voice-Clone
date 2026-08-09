#!/usr/bin/env node
/**
 * Fails the build if any `var(--x)` referenced under src/ is not declared in
 * styles/variables.css. CLAUDE.md documents this check as existing; this is
 * that check.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const srcDir = path.join(root, 'src');
const varsFile = path.join(srcDir, 'styles', 'variables.css');

const VAR_REF = /var\(\s*(--[a-zA-Z0-9-]+)/g;
const VAR_DECL = /^\s*(--[a-zA-Z0-9-]+)\s*:/gm;

// Runtime-computed custom properties, written straight to the DOM by JS
// (never declared in a stylesheet, by design) rather than design tokens.
// See EnrollCard.tsx's meter (--level, --phase).
const DYNAMIC_ALLOWLIST = new Set(['--level', '--phase']);

function collectFiles(dir, exts, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) collectFiles(full, exts, out);
    else if (exts.some((e) => entry.name.endsWith(e))) out.push(full);
  }
  return out;
}

const declared = new Set();
for (const m of readFileSync(varsFile, 'utf8').matchAll(VAR_DECL)) declared.add(m[1]);

const files = collectFiles(srcDir, ['.css', '.tsx', '.ts']);
const missing = new Map();

for (const file of files) {
  const text = readFileSync(file, 'utf8');
  for (const m of text.matchAll(VAR_REF)) {
    const name = m[1];
    if (!declared.has(name) && !DYNAMIC_ALLOWLIST.has(name)) {
      const rel = path.relative(root, file);
      if (!missing.has(name)) missing.set(name, new Set());
      missing.get(name).add(rel);
    }
  }
}

if (missing.size > 0) {
  console.error('CSS variable check failed — referenced but never declared in styles/variables.css:\n');
  for (const [name, usedIn] of missing) {
    console.error(`  ${name}`);
    for (const f of usedIn) console.error(`    used in ${f}`);
  }
  process.exit(1);
}

console.log(`CSS variable check passed (${declared.size} declared, all references resolved).`);
