// Build script to produce the Safari/iOS bundle from public/
// Run: npm run build:safari
//
// Differences vs the Chrome bundle:
//   - "key" removed          (Chrome-only field; safari-web-extension-converter rejects it)
//   - "identity" permission  (chrome.identity does not exist in Safari)
//
// No source changes are needed: background.js answers GET_CAPABILITIES with
// authSupported=false when chrome.identity is absent, and content.js renders
// the logged-out UI instead of the OAuth login gate.

import { cpSync, rmSync, readFileSync, writeFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const outDir = join(__dirname, 'dist-safari');

rmSync(outDir, { recursive: true, force: true });
cpSync(join(__dirname, 'public'), outDir, { recursive: true });

const manifestPath = join(outDir, 'manifest.json');
const manifest = JSON.parse(readFileSync(manifestPath, 'utf-8'));

delete manifest.key;
manifest.permissions = (manifest.permissions || []).filter(p => p !== 'identity');

writeFileSync(manifestPath, JSON.stringify(manifest, null, 4));
console.log('Built dist-safari/ — removed "key", stripped "identity" permission');
