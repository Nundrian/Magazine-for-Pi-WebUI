// Native plugin lifecycle under Pi Web UI's own createMockHost.
// Run: PI_WEB_UI_DIR=<node_modules/pi-web-ui> MAGAZINE_PYTHON=<python3.11> node --test tests/host/
// No model, no provider request: only catalogue/library routes are exercised.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

// Offline guard: no provider credentials reach the SDK catalogue or workers.
for (const name of Object.keys(process.env))
  if (/^(AWS|ANTHROPIC|OPENAI|AZURE|GOOGLE|GEMINI|VERTEX|BEDROCK|MISTRAL|GROQ|XAI|OPENROUTER|DEEPSEEK|OLLAMA|PI_WEB_TOKEN)/.test(name)) delete process.env[name];
process.env.PI_OFFLINE = '1';
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const WEB = process.env.PI_WEB_UI_DIR;
const skip = !WEB && 'PI_WEB_UI_DIR not set';

async function mockFactory() {
  for (const file of ['plugin-sdk/testing.mjs', 'plugin-sdk/index.mjs']) {
    const full = path.join(WEB, file);
    if (fs.existsSync(full)) {
      const mod = await import(pathToFileURL(full).href);
      if (mod.createMockHost) return mod.createMockHost;
    }
  }
  throw new Error('createMockHost not found in host plugin-sdk');
}

function installPlugin(extraConfig = {}) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mag-host-'));
  const dir = path.join(tmp, 'plugins', 'magazine-pi');
  fs.cpSync(path.join(ROOT, 'magazine-pi'), dir, {recursive: true, filter: s => !s.includes('__pycache__')});
  const agent = path.join(tmp, 'agent');
  fs.mkdirSync(agent);
  fs.writeFileSync(path.join(agent, 'models.json'), JSON.stringify({providers: {fixture: {baseUrl: 'http://127.0.0.1:9/v1',
    api: 'openai-completions', apiKey: 'fixture-key', models: [{id: 'model-a', name: 'model-a', reasoning: false, input: ['text'],
    contextWindow: 8192, maxTokens: 256, cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}}]}}}));
  fs.writeFileSync(path.join(agent, 'auth.json'), '{}');
  fs.writeFileSync(path.join(dir, 'config.json'), JSON.stringify({agentDir: agent, python: process.env.MAGAZINE_PYTHON || 'python3',
    allowedTools: ['read'], ...extraConfig}));
  return {tmp, dir};
}

function call(route, body) {
  return new Promise((resolve, reject) => {
    const res = {code: 200, status(c) { this.code = c; return this; }, json(v) { resolve({code: this.code, body: v}); }};
    route.handler({body, headers: {}}, res).catch(reject);
  });
}

async function activate(createMockHost, dir, tmp, hostBin) {
  const plugin = (await import(pathToFileURL(path.join(dir, 'index.mjs')).href + '?' + Math.random())).default;
  const host = createMockHost({dir, dataDir: path.join(tmp, 'data')});
  const argv = process.argv[1];
  process.argv[1] = hostBin;
  try { await plugin.activate(host); } finally { process.argv[1] = argv; }
  return {plugin, host};
}

test('activation, routes, reload and deactivation on the real installed host package', {skip}, async () => {
  const createMockHost = await mockFactory();
  const {tmp, dir} = installPlugin();
  const bin = path.join(WEB, 'bin', 'pi-web-ui.mjs');
  const first = await activate(createMockHost, dir, tmp, bin);
  try {
  const routes = first.host.mock.routes ?? first.host.calls.filter(c => c.method === 'route').map(c => ({method: c.args[0], path: c.args[1], handler: c.args[2]}));
  assert.deepEqual(routes.map(r => `${r.method} ${r.path}`).sort(), ['POST /rpc', 'POST /upload']);
  const rpc = routes.find(r => r.path === '/rpc');
  const catalogue = await call(rpc, {action: 'catalogue'});
  assert.equal(catalogue.code, 200, JSON.stringify(catalogue.body));
  assert.deepEqual(catalogue.body.result.models.map(m => m.id), ['fixture/model-a']);
  assert.deepEqual(catalogue.body.result.tools.map(t => t.id), ['read']);
  assert.equal(catalogue.body.result.compatibility.override, false);
  const library = await call(rpc, {action: 'library'});
  assert.equal(library.body.result.projects.available, true);
  assert.equal((await call(rpc, {})).code, 400);
  assert.equal((await call(rpc, {action: 'status', data: {run_id: '../../etc'}})).code, 404);
  first.plugin.deactivate();
  // Reload: a fresh activation against the same data succeeds and sees the same library.
  const second = await activate(createMockHost, dir, tmp, bin);
  const routes2 = second.host.mock.routes ?? second.host.calls.filter(c => c.method === 'route').map(c => ({method: c.args[0], path: c.args[1], handler: c.args[2]}));
  assert.equal((await call(routes2.find(r => r.path === '/rpc'), {action: 'runs'})).body.result.runs.length, 0);
  second.plugin.deactivate();
  } finally { first.plugin.deactivate(); fs.rmSync(tmp, {recursive: true, force: true}); }
});

test('unverified host refuses activation with a diagnostic and registers nothing', {skip}, async () => {
  const createMockHost = await mockFactory();
  const {tmp, dir} = installPlugin();
  const fake = path.join(tmp, 'fake-host');
  fs.mkdirSync(path.join(fake, 'bin'), {recursive: true});
  fs.writeFileSync(path.join(fake, 'package.json'), JSON.stringify({name: 'pi-web-ui', version: '0.121.0'}));
  fs.writeFileSync(path.join(fake, 'bin', 'pi-web-ui.mjs'), '');
  const plugin = (await import(pathToFileURL(path.join(dir, 'index.mjs')).href + '?refuse')).default;
  const host = createMockHost({dir, dataDir: path.join(tmp, 'data')});
  const argv = process.argv[1];
  process.argv[1] = path.join(fake, 'bin', 'pi-web-ui.mjs');
  try {
    await assert.rejects(plugin.activate(host), /has not verified Pi Web UI 0\.121\.0.*Nothing was changed/s);
  } finally { process.argv[1] = argv; }
  const routes = host.mock.routes ?? host.calls.filter(c => c.method === 'route');
  assert.equal(routes.length, 0);
  assert.equal(fs.existsSync(path.join(tmp, 'data', 'magazine')), false);
  fs.rmSync(tmp, {recursive: true, force: true});
});
