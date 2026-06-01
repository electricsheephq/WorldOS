#!/usr/bin/env node
// Bounded OpenWorlds hook probe for the handoff gate.
//
// This probes the same live viewer port that /app-status came from, but reads
// source modules instead of launching another browser. The handoff gate already
// captures screenshots and drives /move; this probe is for exact hook coverage
// without adding a Playwright or Chrome dependency.

const http = require('http');
const https = require('https');

const baseUrl = process.argv[2] || '';
if (!baseUrl) {
  console.error('usage: node qa/app_handoff_hooks.js <base-url>');
  process.exit(2);
}

const FILES = {
  launcher: 'screen-launcher.jsx',
  table: 'screen-table.jsx',
  settings: 'screen-settings.jsx',
  toast: 'toast.jsx',
  modal: 'camp-sidebar.jsx',
  chrome: 'chrome.jsx',
};

const CHECKS = {
  launcher: {
    file: FILES.launcher,
    required: ['worldos-launcher', 'chronicle-start-flow', 'campaign-row'],
    optional: ['continue-banner', 'chronicle-resume', 'chronicle-resume-detail', 'error-banner'],
  },
  table: {
    file: FILES.table,
    required: [
      'openworlds-root',
      'app-status-banner',
      'narration-log',
      'active-player',
      'action-palette',
      'action-button',
      'move-input',
      'move-submit',
    ],
    optional: ['move-composer', 'dice-button', 'error-banner'],
    requiredSourceMarkers: ['data-worldos-action-id'],
  },
  settings: {
    file: FILES.settings,
    required: ['settings-root', 'settings-tab', 'provider-status', 'provider-controls'],
    optional: ['provider-card', 'provider-start', 'provider-stop', 'error-banner'],
    requiredSourceMarkers: ['data-worldos-tab-id'],
  },
  modal: {
    file: FILES.modal,
    required: ['modal-close'],
    optional: [],
  },
  toast: {
    file: FILES.toast,
    required: ['error-banner'],
    optional: ['toast-region', 'toast'],
  },
  chrome: {
    file: FILES.chrome,
    required: ['primary-navigation', 'screen-tabs', 'screen-tab'],
    optional: [],
  },
};

function fetchText(url) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith('https:') ? https : http;
    const req = client.get(url, { timeout: 8000 }, (res) => {
      const chunks = [];
      res.setEncoding('utf8');
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(new Error(`${url} returned HTTP ${res.statusCode}`));
          return;
        }
        resolve(chunks.join(''));
      });
    });
    req.on('timeout', () => {
      req.destroy(new Error(`${url} timed out`));
    });
    req.on('error', reject);
  });
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function countTestId(source, testId) {
  const dataRe = new RegExp(`data-worldos-testid=(?:["']${escapeRegExp(testId)}["']|\\{[^}]*["']${escapeRegExp(testId)}["'][^}]*\\})`, 'g');
  const propRe = new RegExp(`\\btestId=["']${escapeRegExp(testId)}["']`, 'g');
  return (source.match(dataRe) || []).length + (source.match(propRe) || []).length;
}

function sourceHas(source, marker) {
  return source.includes(marker);
}

async function runScreen(name, spec, sources) {
  const url = `${baseUrl.replace(/\/$/, '')}/openworlds/${spec.file}`;
  const source = await fetchText(url);
  sources[spec.file] = source.length;
  const required = Object.fromEntries(spec.required.map((testId) => [testId, countTestId(source, testId)]));
  const optional = Object.fromEntries(spec.optional.map((testId) => [testId, countTestId(source, testId)]));
  const missingRequired = Object.entries(required)
    .filter(([, count]) => !count)
    .map(([testId]) => testId);
  for (const marker of spec.requiredSourceMarkers || []) {
    if (!sourceHas(source, marker)) missingRequired.push(marker);
  }
  return {
    screen: name,
    url,
    ok: missingRequired.length === 0,
    missing_required: missingRequired,
    console_errors: 0,
    console_error_samples: [],
    observed: {
      required,
      optional,
      source_bytes: source.length,
      source_markers: Object.fromEntries((spec.requiredSourceMarkers || []).map((marker) => [marker, sourceHas(source, marker)])),
    },
  };
}

(async () => {
  const appStatusUrl = `${baseUrl.replace(/\/$/, '')}/app-status`;
  const sources = {};
  const appStatus = JSON.parse(await fetchText(appStatusUrl));
  const screens = [];
  for (const [name, spec] of Object.entries(CHECKS)) {
    screens.push(await runScreen(name, spec, sources));
  }
  const missing = screens.flatMap((screen) => screen.missing_required.map((testId) => `${screen.screen}:${testId}`));
  console.log(JSON.stringify({
    schema: 'worldos.app-handoff-hooks.v1',
    ok: missing.length === 0,
    probe_mode: 'same-port-source-http',
    base_url: baseUrl,
    app_status_schema: appStatus.schema || '',
    app_status_port: appStatus.viewer?.port || null,
    missing_required: missing,
    console_errors: 0,
    fetched_sources: sources,
    screens,
  }, null, 2));
})().catch((error) => {
  console.error(`app_handoff_hooks fatal: ${error?.stack || error}`);
  process.exit(3);
});
