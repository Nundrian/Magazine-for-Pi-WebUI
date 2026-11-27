// The native view must stay usable with damaged, incomplete or legacy run data.
// Run: JSDOM_DIR=<node_modules/jsdom> node --test tests/host/client_damaged.test.mjs
// No server, model or browser: a jsdom document and a scripted RPC backend.
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const JSDOM_DIR = process.env.JSDOM_DIR;
const skip = !JSDOM_DIR && 'JSDOM_DIR not set';

const order = ['c001', 'c002', 'c003'];
const damaged = {run_id: 'd092667a6f6c', status: 'STATE_INVALID', display_status: 'STATE_INVALID',
  state_error: "MagazineStateError(\"Unknown state fields: ['project_root']\")", done: [], todo: [], order,
  current_cartridge: null, name: 'Nebula Swamp', ledger: {cartridges: [{id: 'c001', status: 'done', attempts: 1, tokens: null}]},
  tokens: {available: false, attempts: 1}, active: false, queue: {queued: false}, protocol: {handover_mode: 'result_file_v1'},
  repair: {available: true, detail: 'Magazine cannot read this run\'s controller state.'},
  emergency_stop: {requested_at: '2026-11-24T05:00:00Z', active_task: false, worker: 'none', worker_processes_after: 'absent'}};
// What a pre-pi.9 server returned for the same run: no done/todo/order lists at all.
const legacyBroken = {run_id: 'aaaaaaaaaaaa', status: 'error', display_status: 'error', error: 'MagazineStateError(...)'};
const restorePoints = {items: [
  {trash_id: 'a'.repeat(32), kind: 'run', id: '4d0869978ad4', name: 'Nebula', run: {status: 'HALTED', completed: 0, total: 130, cartridge: 'sw000'},
   created_at: 1795493023, state: 'complete', bytes: 868352, reclaimable_bytes: 868352, files: 40, shared_files: 0,
   can_restore: true, can_delete: true, protected_reason: null, reason: 'Saved run deleted from Manage'},
  {trash_id: 'b'.repeat(32), kind: 'run', id: 'ecbcfb6d1da4', name: null, run: null, created_at: 1795219659, state: 'interrupted',
   bytes: 4096, reclaimable_bytes: 4096, files: 2, shared_files: 0, can_restore: true, can_delete: false,
   protected_reason: 'A deletion was interrupted.', reason: 'An interrupted move to the trash'}],
  total_bytes: 872448, reclaimable_bytes: 868352, automatic_deletion: false};

function backend(statusFor, calls) {
  return async (url, init) => {
    const {action, data} = JSON.parse(init.body);
    calls.push(action);
    const result = {
      catalogue: {models: [], tools: [], magazine_version: '0.1.0-pi.9'},
      library: {projects: {available: true, workflows: [], truncated: false}},
      view_state: {run_id: statusFor.run_id, run_exists: true},
      runs: {runs: [statusFor, legacyBroken], scheduler: {mode: 'sequential', current: null, queue: []}},
      status: data.run_id === legacyBroken.run_id ? legacyBroken : statusFor,
      ledger: {ledger: null, markdown: null, tokens: {available: false}},
      results: {results: {}, handovers: {}},
      restore_points: restorePoints, trash: {items: []},
      worker_isolation: {mode: 'required', landlock_abi: 3, enforced: true, workspace: '/workspace', extra_write_paths: [], limits: []},
      chats: {chats: [], total: 0}, scheduler: {mode: 'sequential'},
    }[action];
    if (result === undefined) return {status: 400, headers: {get: () => 'application/json'}, json: async () => ({ok: false, error: 'unknown ' + action})};
    return {status: 200, headers: {get: () => 'application/json'}, json: async () => ({ok: true, result})};
  };
}

async function mount(statusFor) {
  const {JSDOM} = await import(pathToFileURL(path.join(JSDOM_DIR, 'lib/api.js')).href);
  const dom = new JSDOM('<!doctype html><div id="root"></div>', {url: 'http://127.0.0.1/', pretendToBeVisual: true});
  const errors = [], calls = [];
  for (const key of ['window', 'document', 'localStorage', 'getComputedStyle', 'Event', 'HTMLElement'])
    globalThis[key] = key === 'window' ? dom.window : dom.window[key];
  globalThis.CSS = globalThis.CSS || {escape: s => String(s)};
  globalThis.fetch = backend(statusFor, calls);
  dom.window.addEventListener('error', e => errors.push(e.message));
  process.on('unhandledRejection', e => errors.push(String(e && e.message || e)));
  const module = await import(pathToFileURL(path.join(ROOT, 'magazine-pi/client/entry.mjs')).href + '?' + Math.random());
  const el = dom.window.document.getElementById('root');
  const handle = module.default.mount(el);
  await new Promise(r => setTimeout(r, 400));
  return {dom, el, errors, calls, q: id => el.querySelector(`[data-id="${id}"]`), handle};
}

test('damaged controller state renders, keeps Stop and offers Repair', {skip}, async () => {
  const v = await mount(damaged);
  try {
    assert.deepEqual(v.errors, []);
    assert.match(v.q('run-summary').textContent, /controller state/);
    assert.match(v.q('run-summary').textContent, /Emergency stop recorded/);
    assert.equal(v.q('stop').disabled, false, 'Stop must stay available for a damaged run');
    assert.equal(v.q('repair').hidden, false);
    assert.equal(v.q('repair').disabled, false);
    assert.equal(v.q('recover').disabled, true, 'recovery waits for an explicit repair');
    assert.match(v.q('dashboard').textContent, /STATE DAMAGED/);
  } finally { if (typeof v.handle === "function") v.handle(); v.dom.window.close(); }
});

test('a legacy server response without done/order lists does not break the view', {skip}, async () => {
  const v = await mount(legacyBroken);
  try {
    assert.deepEqual(v.errors, []);
    assert.doesNotMatch(v.q('message').textContent, /undefined/);
  } finally { if (typeof v.handle === "function") v.handle(); v.dom.window.close(); }
});

test('Manage updates a finished run without replacing its focused deletion control', {skip}, async () => {
  const status = {...damaged, status:'RUNNING', display_status:'RUNNING', active:true};
  const v = await mount(status);
  try {
    v.q('nav-manage').click();
    await new Promise(r => setTimeout(r, 100));
    const button = v.el.querySelector(`[data-delete-run="${status.run_id}"]`);
    assert.equal(button.disabled, true);
    Object.assign(status, {status:'COMPLETED', display_status:'COMPLETED', active:false});
    v.q('progress').click();
    await new Promise(r => setTimeout(r, 100));
    assert.equal(v.el.querySelector(`[data-delete-run="${status.run_id}"]`), button);
    assert.equal(button.disabled, false);
    assert.match(button.parentElement.textContent, /COMPLETED/);
    button.focus();
    Object.assign(status, {status:'READY', display_status:'QUEUED', active:false});
    v.q('progress').click();
    await new Promise(r => setTimeout(r, 100));
    assert.equal(button.disabled, true, 'queued work remains protected');
    assert.deepEqual(v.errors, []);
  } finally { v.handle(); v.dom.window.close(); }
});

test('Manage lists restore points with size, run, time and protection', {skip}, async () => {
  const v = await mount(damaged);
  try {
    v.el.querySelector('[data-id="nav-manage"]').click();
    await new Promise(r => setTimeout(r, 300));
    const text = v.q('manage-trash').textContent;
    assert.match(text, /Nebula/);
    assert.match(text, /0 of 130 steps/);
    assert.match(text, /848\.0 KB/);
    assert.match(text, /Protected: A deletion was interrupted/);
    assert.match(v.q('rp-summary').textContent, /2 restore points/);
    const boxes = [...v.q('manage-trash').querySelectorAll('input[type=checkbox]')];
    assert.equal(boxes[1].disabled, true);
    v.q('rp-delete-all').click();
    await new Promise(r => setTimeout(r, 50));
    assert.match(v.q('rp-confirm').textContent, /permanently deleted/);
    assert.ok(!v.calls.includes('delete_restore_points'), 'nothing is deleted before explicit confirmation');
    assert.match(v.q('isolation-state').textContent, /Landlock/);
    assert.deepEqual(v.errors, []);
  } finally { if (typeof v.handle === "function") v.handle(); v.dom.window.close(); }
});
