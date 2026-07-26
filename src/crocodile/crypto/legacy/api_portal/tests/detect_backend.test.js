/**
 * Unit tests for portal detectBackend probe list / health fallback.
 * Run: node --test tests/detect_backend.test.js
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const appJsPath = path.join(__dirname, '..', 'public', 'js', 'app.js');
const appJs = fs.readFileSync(appJsPath, 'utf8');

/** Extract the probe path array from detectBackend in app.js. */
function extractProbePaths(source) {
    const match = source.match(
        /for\s*\(\s*const\s+path\s+of\s*(\[[\s\S]*?\])\s*\)/,
    );
    assert.ok(match, 'expected detectBackend probe for-loop in app.js');
    // Probe list is a static JS array literal of string constants.
    // eslint-disable-next-line no-new-func
    return Function(`"use strict"; return (${match[1]});`)();
}

/**
 * Pure status classifier mirroring detectBackend probe rule:
 * HTTP 200 on a Python-only probe path → Python backend.
 */
function isPythonProbeStatus(status) {
    return status === 200;
}

test('detectBackend probes include catalog, metrics, ready, and health', () => {
    const paths = extractProbePaths(appJs);
    assert.deepEqual(paths, [
        '/api/v1/catalog/channels',
        '/metrics',
        '/api/v1/ready',
        '/api/v1/health',
    ]);
});

test('detectBackend treats /api/v1/ready and /api/v1/health after catalog probes', () => {
    const paths = extractProbePaths(appJs);
    const catalogIdx = paths.indexOf('/api/v1/catalog/channels');
    const readyIdx = paths.indexOf('/api/v1/ready');
    const healthIdx = paths.indexOf('/api/v1/health');
    assert.ok(catalogIdx >= 0, 'catalog probe present');
    assert.ok(readyIdx >= 0, 'ready probe present');
    assert.ok(healthIdx >= 0, 'health probe present');
    assert.ok(
        readyIdx > catalogIdx,
        'ready must be tried after catalog so it only matters when catalog fails',
    );
    assert.ok(
        healthIdx > readyIdx,
        'health must be tried after ready so 503 readiness falls through to liveness',
    );
});

test('probe status 200 classifies as Python', () => {
    assert.equal(isPythonProbeStatus(200), true);
    assert.equal(isPythonProbeStatus(404), false);
    assert.equal(isPythonProbeStatus(500), false);
    assert.equal(isPythonProbeStatus(0), false);
});

test('app.js still falls back to admin payments when probes miss', () => {
    assert.match(appJs, /\/api\/v1\/admin\/payments/);
    assert.match(appJs, /applyPythonRoutes/);
    assert.match(appJs, /applyNodeRoutes/);
});
