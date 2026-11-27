import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {discoveredModel,ollamaApiUrl,syncOllamaModels} from '../../magazine-pi/ollama_models.mjs';

const configured={id:'kept',name:'My configured model',contextWindow:4096,maxTokens:1024,
  reasoning:true,input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},compat:{supportsDeveloperRole:false}};
const provider={baseUrl:'http://127.0.0.1:11434/v1',api:'openai-completions',apiKey:'fixture',models:[configured,{...configured,id:'removed'}]};
const metadata=(capabilities=['completion'])=>({capabilities,model_info:{'general.architecture':'fixture','fixture.context_length':8192}});

test('endpoint derivation supports native, OpenAI and reverse-proxy prefixes',()=>{
  for(const base of ['http://localhost:11434','http://localhost:11434/v1/','http://localhost:11434/api'])
    assert.equal(ollamaApiUrl(base,'tags'),'http://localhost:11434/api/tags');
  assert.equal(ollamaApiUrl('https://example.test/ollama/v1?key=private','show'),'https://example.test/ollama/api/show');
  assert.throws(()=>ollamaApiUrl('file:///tmp/config','tags'));
});

test('configured settings are preserved exactly, independent of advertised capabilities',()=>{
  const before=structuredClone(configured);
  assert.deepEqual(discoveredModel('kept',metadata(['completion','vision']),provider),before);
  assert.deepEqual(configured,before);
});

test('Pi definitions with omitted display name and input are completed from metadata',()=>{
  const model={id:'org/model:Q6_K',reasoning:true,contextWindow:8092,maxTokens:2048};
  const observed=discoveredModel(model.id,metadata(['completion','vision']),{models:[model]});
  assert.equal(observed.name,model.id);
  assert.deepEqual(observed.input,['text','image']);
  assert.equal(observed.reasoning,true);
  assert.equal(observed.contextWindow,8092);
  assert.equal(observed.maxTokens,2048);
  assert.equal(model.name,undefined);
});

test('new models derive capabilities and retain existing local context/output limits',()=>{
  const m=discoveredModel('downloaded',metadata(['completion','vision','thinking']),provider);
  assert.deepEqual(m.input,['text','image']);assert.equal(m.reasoning,true);
  assert.equal(m.contextWindow,4096);assert.equal(m.maxTokens,1024);
  const small=metadata();small.model_info['fixture.context_length']=512;
  assert.equal(discoveredModel('small',small,provider).maxTokens,512);
});

test('embedding-only, classifier and unknown capability models are never offered',()=>{
  for(const caps of [['embedding'],['embedding','tools','thinking'],['vision'],[]])
    assert.equal(discoveredModel('embed',metadata(caps),provider),null);
  assert.throws(()=>discoveredModel('unknown',{},provider));
  assert.ok(discoveredModel('hybrid',metadata(['completion','embedding']),provider));
});

async function fixture(fn){
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'mag-ollama-'));
  const file=path.join(dir,'models.json');fs.writeFileSync(file,JSON.stringify({providers:{ollama:provider}}));
  const original=fs.readFileSync(file);const registrations=[];
  const runtime={getAuth:async()=>({apiKey:'resolved-fixture-key',headers:{'X-Fixture':'yes'}}),
    registerProvider:(id,config)=>registrations.push({id,config})};
  try{await fn({file,runtime,registrations});assert.deepEqual(fs.readFileSync(file),original);}
  finally{fs.rmSync(dir,{recursive:true,force:true});}
}

test('downloads/removals synchronize in memory without modifying configuration',()=>fixture(async({file,runtime,registrations})=>{
  let names=['kept','downloaded','embed'];const calls=[];
  const fetcher=async(url,options)=>{
    calls.push(url);assert.equal(options.headers.Authorization,'Bearer resolved-fixture-key');
    const data=url.endsWith('/tags')?{models:names.map(name=>({name}))}:
      metadata(JSON.parse(options.body).model==='embed'?['embedding','thinking','tools']:['completion']);
    return {ok:true,json:async()=>data};
  };
  const first=await syncOllamaModels(runtime,file,{fetcher});
  assert.deepEqual([...first.ids],['kept','downloaded']);assert.equal(first.status.excluded,1);
  assert.deepEqual(registrations[0].config.models[0],configured);
  names=['downloaded','later'];
  assert.deepEqual([...(await syncOllamaModels(runtime,file,{fetcher})).ids],names);
  assert.ok(calls.every(u=>/\/api\/(tags|show)$/.test(u)));
}));

test('discovery outage or malformed tags never falls back to stale definitions',()=>fixture(async({file,runtime})=>{
  for(const fetcher of [async()=>{throw new Error('private endpoint detail');},
    async()=>({ok:false,status:503}),async()=>({ok:true,json:async()=>({models:[{}]})})]){
    const result=await syncOllamaModels(runtime,file,{fetcher});
    assert.equal(result.ids.size,0);assert.equal(result.status.available,0);
    assert.match(result.status.warnings.join(' '),/unavailable/);
    assert.doesNotMatch(result.status.warnings.join(' '),/private endpoint detail/);
  }
}));

test('failed metadata is omitted while other models remain usable',()=>fixture(async({file,runtime})=>{
  const fetcher=async(url,options)=>url.endsWith('/tags')?{ok:true,json:async()=>({models:[{name:'kept'},{name:'unknown'}]})}:
    JSON.parse(options.body).model==='unknown'?{ok:false,status:404}:{ok:true,json:async()=>metadata()};
  const result=await syncOllamaModels(runtime,file,{fetcher});
  assert.deepEqual([...result.ids],['kept']);assert.equal(result.status.warnings.length,1);
}));

test('no configured Ollama provider performs no network or registration',async()=>{
  let calls=0;
  const result=await syncOllamaModels({},path.join(os.tmpdir(),'nonexistent-magazine-models.json'),{fetcher:()=>calls++});
  assert.equal(result.ids,null);assert.equal(result.status,null);assert.equal(calls,0);
});
