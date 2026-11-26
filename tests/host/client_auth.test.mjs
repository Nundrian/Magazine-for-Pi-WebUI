// pi.10: Pi sign-in (HTTP 401) handling in the Magazine view, and the Host connections panel.
// Run: JSDOM_DIR=<node_modules/jsdom> node --test tests/host/client_auth.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const ENTRY = pathToFileURL(path.join(ROOT, 'magazine-pi/client/entry.mjs')).href;
const JSDOM_DIR = process.env.JSDOM_DIR;
const skip = !JSDOM_DIR && 'JSDOM_DIR not set';

const run = {run_id: 'aaaaaaaaaaaa', status: 'RUNNING', display_status: 'RUNNING', done: ['c1'], todo: ['c2'], order: ['c1', 'c2'],
  current_cartridge: 'c2', name: 'Nebula', ledger: {cartridges: []}, tokens: {available: false}, active: true, queue: {queued: false},
  protocol: {handover_mode: 'result_file_v1'}, execution: {owner_alive: true},
  dependencies: {declared: {services: {unreal_connector: {operations: ['health']}}}, last_check: {ok: false, problems: ['Unreal Connector has no project nebulaswamp.']}}};

const json = (status, body) => ({status, headers: {get: () => 'application/json'}, json: async () => body, text: async () => JSON.stringify(body)});
const plain401 = text => ({status: 401, headers: {get: () => 'text/plain'}, json: async () => { throw new Error('not json'); }, text: async () => text});

function backend(state) {
  return async (url, init) => {
    const {action, data} = JSON.parse(init.body);
    state.calls.push(action);
    const fail = state.fail401[action];
    if (fail && fail.count > 0) { fail.count--; return plain401(fail.text); }
    const result = {
      catalogue: {models: [], tools: [], magazine_version: '0.1.0-pi.10'},
      library: {projects: {available: true, workflows: [], truncated: false}},
      view_state: {run_id: run.run_id, run_exists: true},
      runs: {runs: [run], scheduler: {mode: 'sequential', current: run.run_id, queue: []}},
      status: run, ledger: {ledger: null, markdown: null, tokens: {available: false}}, results: {results: {}, handovers: {}},
      restore_points: {items: [], total_bytes: 0, reclaimable_bytes: 0, automatic_deletion: false}, trash: {items: []},
      worker_isolation: {mode: 'required', landlock_abi: 3, enforced: true, workspace: '/workspace', extra_write_paths: [], limits: []},
      chats: {chats: [], total: 0}, scheduler: {mode: 'sequential'},
      host_connections: {services: {
        unreal_connector: {label: 'Unreal Connector', configured: false, source: null, address: null, key_fingerprint: null},
        resource_kit: {label: '3D Resource Kit', configured: true, source: 'pi-mcp', address: 'http://host.docker.internal:8766', key_fingerprint: 'abc123def456'}}},
      host_connect: {services: {}},
    }[action];
    if (result === undefined) return json(400, {ok: false, error: 'unknown ' + action});
    return json(200, {ok: true, result});
  };
}

async function mount(state) {
  const {JSDOM} = await import(pathToFileURL(path.join(JSDOM_DIR, 'lib/api.js')).href);
  const dom = new JSDOM('<!doctype html><div id="root"></div>', {url: 'http://127.0.0.1/', pretendToBeVisual: true});
  const errors = [];
  for (const key of ['window', 'document', 'localStorage', 'getComputedStyle', 'Event', 'HTMLElement'])
    globalThis[key] = key === 'window' ? dom.window : dom.window[key];
  globalThis.CSS = globalThis.CSS || {escape: s => String(s)};
  globalThis.fetch = backend(state);
  dom.window.addEventListener('error', e => errors.push(e.message));
  const module = await import(ENTRY + '?' + Math.random());
  const el = dom.window.document.getElementById('root');
  const handle = module.default.mount(el);
  await new Promise(r => setTimeout(r, 500));
  return {dom, el, errors, q: id => el.querySelector(`[data-id="${id}"]`), handle};
}
const close = v => { if (typeof v.handle === 'function') v.handle(); v.dom.window.close(); };

test('read/write classification and 401 wording', async () => {
  const m = await import(ENTRY + '?unit');
  assert.equal(m.isReadOnly('status'), true);
  assert.equal(m.isReadOnly('view_state', {}), true);
  assert.equal(m.isReadOnly('view_state', {set: {run_id: 'x'}}), false);
  for (const a of ['start', 'stop', 'prepare', 'recover', 'repair_state', 'host_connect', 'unknown_future_action'])
    assert.equal(m.isReadOnly(a), false, a);
  assert.equal(m.authKind('unauthorized: PI_WEB_TOKEN required — 服务端口令已变更？已清除旧 token cookie'), 'stale');
  assert.equal(m.authKind('unauthorized: PI_WEB_TOKEN required (?token=…)'), 'missing');
  assert.match(m.authMessage('stale'), /cleared it/);
  assert.match(m.AUTH_TEXT.source, /Pi sign-in/);
});

test('a single transient 401 on a status poll is retried once and never shown', {skip}, async () => {
  const state = {calls: [], fail401: {}};
  const v = await mount(state);
  state.fail401.status = {count: 1, text: 'unauthorized: PI_WEB_TOKEN required (?token=…)'};
  try {
    v.q('progress').click();
    await new Promise(r => setTimeout(r, 1700));
    assert.doesNotMatch(v.q('message').textContent, /sign-in/i);
    assert.ok(state.calls.filter(a => a === 'status').length >= 2);
    assert.deepEqual(v.errors, []);
  } finally { close(v); }
});

test('a persistent 401 shows a warning that clears once requests succeed again', {skip}, async () => {
  const state = {calls: [], fail401: {}};
  const v = await mount(state);
  state.fail401.status = {count: 2, text: 'unauthorized: PI_WEB_TOKEN required (?token=…)'};
  try {
    v.q('progress').click();
    await new Promise(r => setTimeout(r, 1700));
    assert.match(v.q('message').textContent, /^Pi sign-in/);
    assert.ok(v.q('message').classList.contains('error'));
    v.q('progress').click();                       // Pi accepts again
    await new Promise(r => setTimeout(r, 300));
    assert.doesNotMatch(v.q('message').textContent, /^Pi sign-in/);
  } finally { close(v); }
});

test('a state-changing action rejected with 401 is never repeated automatically', {skip}, async () => {
  const state = {calls: [], fail401: {host_connect: {count: 5, text: 'unauthorized: PI_WEB_TOKEN required (?token=…)'}}};
  const v = await mount(state);
  try {
    v.el.querySelector('[data-id="nav-manage"]').click();
    await new Promise(r => setTimeout(r, 400));
    const box = v.q('host-connections');
    assert.match(box.textContent, /Unreal Connector/);
    assert.match(box.textContent, /reusing the key from Pi's 3D Resource Kit pairing/);
    assert.doesNotMatch(box.textContent, /rk_/);
    box.querySelector('input[type=password]').value = 'uehl_' + 'K'.repeat(40);
    [...box.querySelectorAll('button')].find(b => b.textContent === 'Connect').click();
    await new Promise(r => setTimeout(r, 1700));
    assert.equal(state.calls.filter(a => a === 'host_connect').length, 1);
    assert.match(v.q('message').textContent, /may not have been carried out/);
    assert.equal(box.querySelector('input[type=password]').value, '', 'the key is cleared from the field at once');
  } finally { close(v); }
});

test('declared host dependencies and their last check are shown with a recheck action', {skip}, async () => {
  const state = {calls: [], fail401: {}};
  const v = await mount(state);
  try {
    assert.match(v.q('run-summary').textContent, /Host applications: unreal_connector/);
    assert.match(v.q('run-summary').textContent, /no project nebulaswamp/);
    assert.ok([...v.q('run-summary').querySelectorAll('button')].some(b => b.textContent === 'Recheck dependencies'));
  } finally { close(v); }
});
