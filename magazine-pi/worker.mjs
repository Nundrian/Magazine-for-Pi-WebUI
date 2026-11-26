// Pi SDK adapter. One process, one fresh saved cartridge session.
//
// The worker never decides workflow outcomes. It reports the actual saved
// session outcome, forwards structured tool/usage events (never reasoning or
// message text) for Antiloop and accounting, and, for result_file_v1 attempts,
// exposes one capability-limited publisher tool that can write only this
// attempt's result file with Magazine-supplied identities.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import readline from 'node:readline';
import {syncOllamaModels} from './ollama_models.mjs';

const send = value => process.stdout.write(JSON.stringify(value) + '\n');
console.log = (...args) => console.error(...args);

export const PUBLISHER_TOOL = 'magazine_publish_result';
export const HOST_TOOL = 'magazine_host';
export const CHECKPOINT_TOOL = 'magazine_checkpoint';
const RESERVED_TOOLS = new Set([PUBLISHER_TOOL, HOST_TOOL, CHECKPOINT_TOOL]);
const HOST_TIMEOUT_MS = 150000;
const pendingHost = new Map();
const HERE = path.dirname(fileURLToPath(import.meta.url));
const TOOL_NAME = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const MAX_RESULT_BYTES = 1024 * 1024;

export function compatibility() {
  const raw = JSON.parse(fs.readFileSync(path.join(HERE, 'compatibility.json'), 'utf8'));
  if (raw.schema !== 1 || !Array.isArray(raw.pairs)) throw new Error('Invalid Magazine compatibility table');
  return raw.pairs;
}

function sdkCandidates(request) {
  const candidates = [process.env.MAGAZINE_PI_SDK, request.sdkDir].filter(Boolean);
  for (const base of createRequire(import.meta.url).resolve.paths('@earendil-works/pi-coding-agent') || [])
    candidates.push(path.join(base, '@earendil-works/pi-coding-agent'));
  candidates.push('/usr/local/lib/node_modules/@earendil-works/pi-coding-agent', '/usr/lib/node_modules/@earendil-works/pi-coding-agent');
  return candidates;
}

export async function loadSdk(request = {}) {
  const supported = new Set(compatibility().map(p => p.sdk));
  for (const dir of sdkCandidates(request)) {
    if (!fs.existsSync(path.join(dir, 'package.json'))) continue;
    const pkg = JSON.parse(fs.readFileSync(path.join(dir, 'package.json')));
    if (request.sdkVersion && pkg.version !== request.sdkVersion)
      throw new Error(`Pi SDK at ${dir} is ${pkg.version}, but the host was verified with ${request.sdkVersion}; configure MAGAZINE_PI_SDK to the host's own SDK`);
    if (!supported.has(pkg.version) && !request.sdkOverride)
      throw new Error(`Magazine has not verified Pi SDK ${pkg.version}; verified: ${[...supported].join(', ')}`);
    return { sdk: await import(pathToFileURL(path.resolve(dir, pkg.main)).href), version: pkg.version, dir };
  }
  throw new Error('Pi SDK not found; configure MAGAZINE_PI_SDK');
}

// ---------------------------------------------------------------- helpers
export function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object')
    return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
  return JSON.stringify(value === undefined ? null : value);
}
const sha256 = data => crypto.createHash('sha256').update(data).digest('hex');

function resultContent(result) {
  const content = Array.isArray(result?.content) ? result.content : [];
  return content.map(part => part?.type === 'text' ? { type: 'text', text: String(part.text ?? '') }
    : { type: String(part?.type ?? 'unknown'), sha256: sha256(String(part?.data ?? '')) });
}

export function reportsNoChange(value) {
  if (typeof value === 'string') { try { return reportsNoChange(JSON.parse(value)); } catch { return false; } }
  if (Array.isArray(value)) return value.some(reportsNoChange);
  if (value && typeof value === 'object')
    return value.changed === false || reportsNoChange(value.text) || reportsNoChange(value.details) || reportsNoChange(value.content);
  return false;
}

function usageOf(usage) {
  if (!usage || typeof usage !== 'object') return null;
  const pick = key => (Number.isInteger(usage[key]) && usage[key] >= 0 ? usage[key] : null);
  return { input: pick('input'), output: pick('output'), cacheRead: pick('cacheRead'),
    cacheWrite: pick('cacheWrite'), totalTokens: pick('totalTokens'), reasoning: pick('reasoning') };
}

// Strict JSON: duplicate keys, trailing data and non-JSON values are refused.
export function strictJson(text) {
  let i = 0;
  const ws = () => { while (i < text.length && ' \t\n\r'.includes(text[i])) i++; };
  const fail = m => { throw new Error(`payload_json: ${m} at ${i}`); };
  const str = () => {
    const start = i; i++;
    while (i < text.length && text[i] !== '"') { if (text[i] === '\\') i++; i++; }
    if (text[i] !== '"') fail('unterminated string');
    i++; return JSON.parse(text.slice(start, i));
  };
  const val = depth => {
    if (depth > 64) fail('nesting too deep');
    ws(); const c = text[i];
    if (c === '{') {
      i++; const obj = {}; const seen = new Set(); ws();
      if (text[i] === '}') { i++; return obj; }
      for (;;) {
        ws(); if (text[i] !== '"') fail('expected key');
        const key = str(); if (seen.has(key)) fail('duplicate key ' + JSON.stringify(key)); seen.add(key);
        ws(); if (text[i] !== ':') fail('expected :'); i++;
        Object.defineProperty(obj, key, { value: val(depth + 1), enumerable: true, writable: true, configurable: true });
        ws(); if (text[i] === ',') { i++; continue; } if (text[i] === '}') { i++; return obj; } fail('expected , or }');
      }
    }
    if (c === '[') {
      i++; const arr = []; ws(); if (text[i] === ']') { i++; return arr; }
      for (;;) { arr.push(val(depth + 1)); ws(); if (text[i] === ',') { i++; continue; } if (text[i] === ']') { i++; return arr; } fail('expected , or ]'); }
    }
    if (c === '"') return str();
    const m = /^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?|^true|^false|^null/.exec(text.slice(i));
    if (!m) fail('invalid value');
    i += m[0].length; const v = JSON.parse(m[0]);
    if (typeof v === 'number' && !Number.isFinite(v)) fail('non-finite number');
    return v;
  };
  const value = val(0); ws(); if (i !== text.length) fail('trailing data');
  return value;
}

function stringList(value, name) {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 256 || !value.every(v => typeof v === 'string' && v.length <= 4096))
    throw new Error(`${name} must be a list of at most 256 strings`);
  return value;
}

/** The only write capability a result_file_v1 attempt receives from Magazine. */
export function publisherTool(publish, getSessionId, notify) {
  return {
    name: PUBLISHER_TOOL,
    label: 'Publish Magazine result',
    description: 'Publish this cartridge attempt\'s result for Magazine. Call it after doing and checking the work. ' +
      'Use verified/note/files/checks for the usual /result handover, or payload_json (a JSON object as text) for other handover keys. ' +
      'Magazine supplies the run, cartridge, attempt and session identities.',
    parameters: { type: 'object', additionalProperties: false, properties: {
      verified: { type: 'boolean', description: 'Your claim that you checked the work (Magazine does not run checks).' },
      note: { type: 'string', description: 'Outcome. For a text deliverable with no files, put the full deliverable text here.' },
      files: { type: 'array', items: { type: 'string' }, description: 'Files you produced or changed.' },
      checks: { type: 'array', items: { type: 'string' }, description: 'Checks you actually performed and their outcome.' },
      payload_json: { type: 'string', description: 'Complete handover payload as a JSON object, when other keys are required.' } } },
    async execute(toolCallId, params) {
      const sessionId = getSessionId();
      if (!sessionId) throw new Error('Session identity has not been assigned; publication is unavailable.');
      let payload;
      if (typeof params?.payload_json === 'string') {
        payload = strictJson(params.payload_json);
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('payload_json must be a JSON object');
      } else {
        const note = params?.note ?? '';
        if (typeof note !== 'string' || note.length > 65536) throw new Error('note must be a string of at most 64 KiB');
        if (params?.verified !== undefined && typeof params.verified !== 'boolean') throw new Error('verified must be boolean');
        payload = { result: { step: publish.cartridgeId, verified: params?.verified === true,
          files: stringList(params?.files, 'files'), checks: stringList(params?.checks, 'checks'), note } };
      }
      const document = { run_id: publish.runId, cartridge_id: publish.cartridgeId, attempt_id: publish.attemptId,
        session_id: sessionId, schema: 'magazine.result.v1', payload };
      const raw = Buffer.from(JSON.stringify(document) + '\n', 'utf8');
      if (raw.length > MAX_RESULT_BYTES) throw new Error('Result exceeds 1 MiB.');
      const target = publish.resultPath;
      const dir = path.dirname(target);
      const st = fs.lstatSync(dir);
      if (!st.isDirectory() || st.isSymbolicLink()) throw new Error('Unsafe publication directory');
      const temp = path.join(dir, `.result.${crypto.randomUUID()}.tmp`);
      const fd = fs.openSync(temp, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | (fs.constants.O_NOFOLLOW || 0), 0o600);
      try {
        fs.writeSync(fd, raw); fs.fsyncSync(fd);
      } finally { fs.closeSync(fd); }
      try {
        fs.renameSync(temp, target);
        const dfd = fs.openSync(dir, 'r'); try { fs.fsyncSync(dfd); } finally { fs.closeSync(dfd); }
      } finally { if (fs.existsSync(temp)) fs.unlinkSync(temp); }
      const back = fs.readFileSync(target);
      if (!back.equals(raw)) throw new Error('Result read-back failed.');
      const receipt = { published: 'publish/result.json', bytes: raw.length, sha256: sha256(raw) };
      notify({ event: 'published', toolCallId: String(toolCallId), ...receipt });
      return { content: [{ type: 'text', text: JSON.stringify(receipt) }], details: receipt };
    },
  };
}

/** pi.10: host-application tools. Credentials and URLs stay with Magazine; this process only forwards the
 * model's arguments over its private channel and returns Magazine's answer. */
export function hostTools(spec, notify) {
  if (!spec) return [];
  const call = (kind, payload) => new Promise((resolve, reject) => {
    const id = crypto.randomUUID();
    const timer = setTimeout(() => { pendingHost.delete(id); reject(new Error('Magazine did not answer the host call in time')); }, HOST_TIMEOUT_MS);
    pendingHost.set(id, { resolve: v => { clearTimeout(timer); resolve(v); }, reject: e => { clearTimeout(timer); reject(e); } });
    notify({ event: 'host_call', id, kind, ...payload });
  });
  const reply = value => ({ content: [{ type: 'text', text: JSON.stringify(value) }], details: value });
  const tools = [];
  const services = Object.keys(spec.services || {});
  if (services.length) tools.push({
    name: HOST_TOOL, label: 'Host application', description: String(spec.description || ''),
    parameters: { type: 'object', additionalProperties: false, required: ['service', 'operation'], properties: {
      service: { type: 'string', enum: services },
      operation: { type: 'string', description: 'One of the operations listed for that service.' },
      arguments: { type: 'object', description: 'JSON object of the operation\'s own arguments.' } } },
    async execute(toolCallId, params) {
      return reply(await call('host', { service: String(params?.service ?? ''), operation: String(params?.operation ?? ''),
        args: params?.arguments ?? {} }));
    },
  });
  if (spec.checkpoint) tools.push({
    name: CHECKPOINT_TOOL, label: 'Validate project checkpoint', description: String(spec.checkpoint),
    parameters: { type: 'object', additionalProperties: false, properties: {} },
    async execute() { return reply(await call('checkpoint', {})); },
  });
  return tools;
}

export function deliverHostResult(message) {
  const waiter = pendingHost.get(String(message?.id ?? ''));
  if (!waiter) return false;
  pendingHost.delete(String(message.id));
  if (message.ok === true) waiter.resolve(message.result);
  else waiter.reject(new Error(String(message.error || 'Magazine refused the host call')));
  return true;
}

function textOf(message) {
  return (message?.content || []).filter(p => p.type === 'text').map(p => p.text).join('\n');
}

// ---------------------------------------------------------------- execution
const TOOL_PREFIX_SDKS = new Set(['1.5.2']);

export function sandboxEvidence() {
  try { return process.env.MAGAZINE_SANDBOX ? JSON.parse(process.env.MAGAZINE_SANDBOX) : null; } catch { return null; }
}

export async function execute(request, onSession = () => {}, shouldAbort = () => false, notify = send) {
  // The launcher confines this process before Node starts; refuse to run a
  // model at all if Magazine required that confinement and it is absent.
  const confinement = sandboxEvidence();
  if (request.requireSandbox && !confinement) throw new Error('Worker sandbox evidence missing; refusing to start inference');
  const { sdk, version } = await loadSdk(request);
  // The tool domain relies on the SDK prepending shellCommandPrefix to every
  // shell command on its own line; refuse SDKs where that was not verified.
  if (request.shellCommandPrefix && (request.tools || []).includes('bash') && !TOOL_PREFIX_SDKS.has(version))
    throw new Error(`Worker sandbox: Pi SDK ${version} is not verified to run shell commands in the tool domain`);
  const { ModelRuntime, DefaultResourceLoader, SettingsManager, SessionManager, createAgentSession } = sdk;
  const runtime = await ModelRuntime.create({authPath:path.join(request.agentDir,'auth.json'),
    modelsPath:path.join(request.agentDir,'models.json'),
    modelsStorePath:request.modelsStorePath || path.join(request.dataDir,'models-store.json'), allowModelNetwork:false});
  if (runtime.getError()) throw new Error(`Pi model configuration: ${runtime.getError()}`);
  const discovery=await syncOllamaModels(runtime,path.join(request.agentDir,'models.json'));
  const models = (await runtime.getAvailable()).filter(m => runtime.getPhysicalModel(m.provider, m.id))
    .filter(m=>m.provider!=='ollama'||discovery.ids===null||discovery.ids.has(m.id));
  const catalogue = models.map(m=>({id:`${m.provider}/${m.id}`,name:m.name,provider:m.provider}));
  const selected = models.find(m=>`${m.provider}/${m.id}` === request.model);
  const isCatalogue = request.action === 'catalogue';
  if (!isCatalogue && !selected) throw new Error(`Assigned model unavailable: ${request.model}`);
  if (isCatalogue && !models.length) return {models:[],tools:[],piVersion:version,ollama:discovery.status};
  const requested = isCatalogue ? [] : (request.tools || []);
  if (!Array.isArray(requested) || requested.some(t => typeof t !== 'string' || !TOOL_NAME.test(t) || RESERVED_TOOLS.has(t)))
    throw new Error('Invalid tool allowlist entry');
  const extraTools = isCatalogue ? [] : hostTools(request.host, notify);
  const expectedTools = [...requested, ...(request.publish ? [PUBLISHER_TOOL] : []), ...extraTools.map(t => t.name)];
  // Unchanged pi.5 resource policy: no extensions, skills, prompt templates,
  // themes, context files, compaction, automatic retry or cache warming.
  // When confined, every shell command re-executes itself inside the nested
  // tool domain first (sandbox.py TOOL_PREFIX), away from this process's
  // channel to Magazine and from the attempt's session/publication folders.
  const settingsManager = SettingsManager.inMemory({packages:[],extensions:[],skills:[],prompts:[],themes:[],
    compaction:{enabled:false},retry:{enabled:false,provider:{maxRetries:0}},cacheWarming:{mode:'off'},
    ...(request.shellCommandPrefix ? {shellCommandPrefix:request.shellCommandPrefix} : {})});
  const resourceLoader = new DefaultResourceLoader({cwd:request.cwd,agentDir:request.agentDir,settingsManager,
    noExtensions:true,noSkills:true,noPromptTemplates:true,noThemes:true,noContextFiles:true,
    additionalExtensionPaths:request.extensionPaths || [],
    systemPrompt:'Execute the current independently prepared cartridge. Use only the available tools when needed. Report your actual work and any uncertainty. Do not claim checks you have not performed.',
    appendSystemPrompt:[],agentsFilesOverride:()=>({agentsFiles:[]}),appendSystemPromptOverride:()=>[]});
  await resourceLoader.reload();
  const extensionErrors = resourceLoader.getExtensions().errors;
  if (extensionErrors?.length) throw new Error('Configured worker extension failed to load');
  const sessionManager = isCatalogue ? SessionManager.inMemory(request.cwd) :
    SessionManager.create(request.cwd,request.sessionDir);
  const customTools = [...(request.publish ? [publisherTool(request.publish, () => sessionManager.getSessionId(), notify)] : []),
    ...extraTools];
  const {session,modelFallbackMessage} = await createAgentSession({cwd:request.cwd,agentDir:request.agentDir,
    // Catalogue builds an in-memory discovery session only. Execution always
    // uses the exact selected model validated above, with no fallback.
    modelRuntime:runtime,model:isCatalogue ? models[0] : selected,thinkingLevel:'off',settingsManager,resourceLoader,
    sessionManager,tools:isCatalogue ? undefined : expectedTools,
    noTools:isCatalogue ? undefined : 'all', ...(customTools.length ? {customTools} : {})});
  onSession(session);
  try {
    if (modelFallbackMessage) throw new Error('Pi attempted model substitution');
    await session.bindExtensions({});
    const tools = session.getAllTools().map(t=>({id:t.name,name:t.label || t.name,description:t.description}));
    if (isCatalogue) return {models:catalogue,tools:tools.filter(t=>t.id!==PUBLISHER_TOOL),piVersion:version,ollama:discovery.status};
    if (expectedTools.some(t=>!tools.some(x=>x.id===t))) throw new Error('Requested tool unavailable');
    session.setActiveToolsByName(expectedTools);
    if (session.getActiveToolNames().sort().join('\0') !== [...expectedTools].sort().join('\0')) throw new Error('Pi tool allowlist differs');
    if (session.messages.length !== 0) throw new Error('Worker context was not fresh');
    if (shouldAbort()) throw new Error('Worker stopped before inference');
    notify({event:'session',id:sessionManager.getSessionId(),file:sessionManager.getSessionFile(),sdkVersion:version,
      activeTools:session.getActiveToolNames().sort(),sandbox:confinement});
    let signalled=false;
    const unsubscribe=session.subscribe(event=>{
      try {
        if(!signalled&&event.type==='message_update') {signalled=true;notify({event:'streaming'});}
        if(event.type==='message_end'&&event.message?.role==='assistant') {
          const m=event.message;
          notify({event:'assistant',stopReason:m.stopReason ?? null,usage:usageOf(m.usage),
            toolCalls:(m.content||[]).filter(c=>c?.type==='toolCall').map(c=>({id:String(c.id),name:String(c.name),
              argsSha256:sha256(canonical(c.arguments ?? null))}))});
        }
        if(event.type==='tool_execution_end') {
          notify({event:'tool_result',toolCallId:String(event.toolCallId),toolName:String(event.toolName),
            isError:event.isError===true,resultSha256:sha256(canonical(resultContent(event.result))),
            noChange:reportsNoChange(event.result)});
        }
      } catch {}
    });
    if (shouldAbort()) throw new Error('Worker stopped before inference');
    try { await session.prompt(request.prompt,{expandPromptTemplates:false}); } finally { unsubscribe(); }
    if (shouldAbort()) throw new Error('Worker aborted');
    if (session.model.provider !== selected.provider || session.model.id !== selected.id) throw new Error('Worker changed assigned model');
    const last = session.messages.filter(m=>m.role==='assistant').at(-1);
    const text = textOf(last);
    const file = sessionManager.getSessionFile();
    if (!file || !fs.existsSync(file)) throw new Error('Saved Pi worker session missing');
    // Read the actual persisted transcript and verify both boundary messages.
    const transcript = fs.readFileSync(file,'utf8').trim().split('\n').map(l=>JSON.parse(l));
    const header = transcript[0];
    if (header?.type !== 'session' || header.id !== sessionManager.getSessionId()) throw new Error('Saved Pi session identity mismatch');
    const messages = transcript.filter(e=>e.type==='message').map(e=>e.message);
    const users = messages.filter(m=>m.role==='user');
    if (users.length !== 1 || textOf(users[0]) !== request.prompt) throw new Error('Saved Pi session prompt mismatch');
    const savedLast = messages.filter(m=>m.role==='assistant').at(-1);
    if (last && (savedLast?.stopReason !== last.stopReason || textOf(savedLast) !== text))
      throw new Error('Saved Pi session result mismatch');
    const fd = fs.openSync(file,'r'); try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
    return {text,finish:last?.stopReason || 'missing',errorMessage:last?.errorMessage ? String(last.errorMessage).slice(0,600) : null,
      sessionFile:file,sessionId:sessionManager.getSessionId(),model:request.model,tools:requested,sdkVersion:version};
  } finally { session.dispose(); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  let session, started=false, aborted=false;
  const input = readline.createInterface({input:process.stdin});
  process.stdin.on('end',async()=>{ if(session?.isStreaming) await session.abort(); process.exit(1); });
  input.on('line',async line=>{
    // pi.10: answers to this worker's own host calls arrive while the session runs.
    try { const early=JSON.parse(line); if (early?.action==='host_result') { deliverHostResult(early); return; } } catch {}
    try {
      const value=JSON.parse(line);
      if (value.action==='abort') {
        aborted=true;
        if (session) { await session.abort(); send({event:'abort',confirmed:!session.isStreaming}); }
        else send({event:'abort',confirmed:false});
        return;
      }
      if (started) throw new Error('One worker request per process');
      started=true;
      const result=await execute(value,s=>{session=s;if(aborted) void s.abort();},()=>aborted);
      send({event:'result',ok:true,result});
    } catch(error) { send({event:'result',ok:false,error:String(error.message),aborted}); }
    finally { if(started && !session?.isStreaming) { input.close(); process.stdin.pause(); } }
  });
}
