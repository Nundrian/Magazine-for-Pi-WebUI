// Live Ollama discovery. Metadata requests only; never writes configuration.
import fs from 'node:fs';

export function ollamaApiUrl(baseUrl, operation) {
  const url=new URL(baseUrl);
  if(!['http:','https:'].includes(url.protocol))throw new Error('Invalid Ollama URL');
  url.pathname=url.pathname.replace(/\/(v1|api)\/?$/,'').replace(/\/$/,'')+'/api/'+operation;
  url.search='';url.hash='';
  return url.href;
}

export function discoveredModel(id, info, provider) {
  const capabilities=info.capabilities;
  if(!Array.isArray(capabilities))throw new Error('Missing model capabilities');
  if(!capabilities.includes('completion'))return null; // including embedding-only models
  const configured=provider.models?.find(m=>m.id===id);
  if(configured)return {...configured};
  const defaults=provider.models?.find(m=>Number.isInteger(m.contextWindow)&&Number.isInteger(m.maxTokens));
  const architecture=info.model_info?.['general.architecture'];
  const trainedContext=info.model_info?.[architecture+'.context_length'];
  const contextWindow=Math.min(defaults?.contextWindow||32768,
    Number.isInteger(trainedContext)&&trainedContext>0?trainedContext:32768);
  return {id,name:id,reasoning:capabilities.includes('thinking'),
    input:capabilities.includes('vision')?['text','image']:['text'],
    contextWindow,maxTokens:Math.min(defaults?.maxTokens||8192,contextWindow),
    cost:{input:0,output:0,cacheRead:0,cacheWrite:0}};
}

export async function syncOllamaModels(runtime, modelsPath, {fetcher=fetch,timeoutMs=3000}={}) {
  const config=fs.existsSync(modelsPath)?JSON.parse(fs.readFileSync(modelsPath,'utf8')):{};
  const provider=config.providers?.ollama;
  if(!provider)return {ids:null,status:null}; // other providers keep their normal behaviour
  const ids=new Set(),warnings=[];
  const status={provider:'ollama',available:0,excluded:0,warnings};
  try {
    if(!provider.baseUrl)throw new Error('Missing Ollama base URL');
    const auth=await runtime.getAuth('ollama');
    const headers={Accept:'application/json',...auth?.headers};
    if(auth?.apiKey)headers.Authorization='Bearer '+auth.apiKey;
    const request=async(operation,body)=>{
      const response=await fetcher(ollamaApiUrl(provider.baseUrl,operation),{
        method:body?'POST':'GET',headers:{...headers,...(body?{'Content-Type':'application/json'}:{})},
        ...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(timeoutMs)});
      if(!response.ok)throw new Error('HTTP '+response.status);
      return response.json();
    };
    const tags=await request('tags');
    if(!Array.isArray(tags.models)||tags.models.some(m=>typeof m?.name!=='string'||!m.name))
      throw new Error('Invalid model list');
    const names=[...new Set(tags.models.map(m=>m.name))];
    const models=(await Promise.all(names.map(async id=>{
      try {
        const model=discoveredModel(id,await request('show',{model:id}),provider);
        if(model)ids.add(id);else status.excluded++;
        return model;
      } catch {warnings.push('Metadata unavailable for '+id);return null;}
    }))).filter(Boolean);
    if(models.length)runtime.registerProvider('ollama',{models});
    status.available=models.length;
  } catch {
    // Never advertise the stale configured list when discovery cannot verify it.
    // The on-disk definitions and saved-run identities remain untouched.
    ids.clear();warnings.push('Ollama catalogue unavailable; no Ollama models are offered. Retrying automatically.');
  }
  return {ids,status};
}
