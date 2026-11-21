import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import readline from 'node:readline';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';

let bridge;
export class ServiceBridge {
  constructor(dir,root,config,log) {
    this.pending=new Map();
    this.process=spawn(config.python || 'python3',[path.join(dir,'service.py'),root,JSON.stringify(config)],
      {cwd:config.cwd,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['pipe','pipe','pipe']});
    this.ready=new Promise((resolve,reject)=>{this.resolveReady=resolve;this.rejectReady=reject;});
    readline.createInterface({input:this.process.stdout}).on('line',line=>{
      try {
        const msg=JSON.parse(line);
        if(msg.event==='ready') {this.resolveReady();return;}
        const task=this.pending.get(msg.id);
        if(task) {this.pending.delete(msg.id);clearTimeout(task.timer);task.resolve(msg);}
      } catch {log('error','Invalid Magazine service response');}
    });
    readline.createInterface({input:this.process.stderr}).on('line',line=>
      log('error',line.startsWith('Magazine filesystem: ')?line:
        'Magazine service reported a failure; inspect run execution metadata'));
    this.process.on('error',error=>this.fail(error));
    this.process.on('exit',()=>this.fail(new Error('Magazine service exited; restart plugin and inspect unfinished runs')));
    this.process.stdin.on('error',error=>this.fail(error));
  }
  fail(error) {
    this.rejectReady(error);
    for(const task of this.pending.values()){clearTimeout(task.timer);task.reject(error);}
    this.pending.clear();
  }
  async call(action,data={}) {
    await this.ready;
    const id=crypto.randomUUID();
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error('Magazine request timed out; inspect status before retrying'));},60000);
      this.pending.set(id,{resolve,reject,timer});
      this.process.stdin.write(JSON.stringify({id,action,data})+'\n');
    });
  }
  close(){this.process.stdin.end();}
}

/** The running pi-web-ui package, found from the host's own entry path. */
export function findHost(entry=process.argv[1]) {
  let current=path.dirname(fs.realpathSync(entry));
  for(let i=0;i<6;i++){
    const pkgFile=path.join(current,'package.json');
    if(fs.existsSync(pkgFile)){
      const pkg=JSON.parse(fs.readFileSync(pkgFile,'utf8'));
      if(pkg.name==='pi-web-ui') return {version:pkg.version,dir:current};
    }
    current=path.dirname(current);
  }
  return {version:undefined,dir:undefined};
}

/** Node's lookup order without importing the package (its exports hide package.json). */
export function packageDir(fromFile,name) {
  for(const base of createRequire(fromFile).resolve.paths(name)||[]){
    const dir=path.join(base,name);
    if(fs.existsSync(path.join(dir,'package.json'))) return dir;
  }
  return undefined;
}

/** The Pi SDK the host itself resolves (or MAGAZINE_PI_SDK, which must agree). */
export function findSdk(hostDir, env=process.env) {
  const read=dir=>{const pkg=JSON.parse(fs.readFileSync(path.join(dir,'package.json'),'utf8'));return {dir,version:pkg.version};};
  let host;
  if(hostDir){const dir=packageDir(path.join(hostDir,'package.json'),'@earendil-works/pi-coding-agent');if(dir)host=read(dir);}
  if(env.MAGAZINE_PI_SDK && fs.existsSync(path.join(env.MAGAZINE_PI_SDK,'package.json'))){
    const configured=read(env.MAGAZINE_PI_SDK);
    if(host && host.version!==configured.version)
      throw new Error(`MAGAZINE_PI_SDK is Pi SDK ${configured.version} but the host uses ${host.version}; workers must use the host's SDK`);
    return configured;
  }
  return host || {dir:undefined,version:undefined};
}

/** Exact verified pairs only; any other pair needs an explicit exact acknowledgement. */
export function checkCompatibility(hostVersion, sdkVersion, table, override) {
  const pairs=table.pairs||[];
  const pair=pairs.find(p=>p.host===hostVersion&&p.sdk===sdkVersion);
  if(pair) return {host:hostVersion,sdk:sdkVersion,level:pair.level,override:false};
  const verified=pairs.map(p=>`Pi Web UI ${p.host} + SDK ${p.sdk} (${p.level})`).join('; ');
  if(override && override.host===hostVersion && override.sdk===sdkVersion && hostVersion && sdkVersion)
    return {host:hostVersion,sdk:sdkVersion,level:'unverified-operator-override',override:true};
  throw new Error(`Magazine has not verified Pi Web UI ${hostVersion||'unknown'} with Pi SDK ${sdkVersion||'unknown'}. `+
    `Verified: ${verified}. Nothing was changed. To run this exact unverified pair anyway, add `+
    `"compatibilityOverride": {"host": "${hostVersion}", "sdk": "${sdkVersion}"} to the plugin's config.json.`);
}

export default {
  async activate(host) {
    if(process.platform!=='linux') throw new Error('Magazine currently requires the Linux Pi Web UI deployment');
    // 0.96.1's compiled host engine checker can miss its package version, so
    // Magazine checks the running host package and its resolved SDK itself.
    const configFile=path.join(host.dir,'config.json');
    const local=fs.existsSync(configFile)?JSON.parse(fs.readFileSync(configFile,'utf8')):{};
    const table=JSON.parse(fs.readFileSync(path.join(host.dir,'compatibility.json'),'utf8'));
    const hostInfo=findHost();
    const sdk=findSdk(hostInfo.dir);
    const compatibility=checkCompatibility(hostInfo.version,sdk.version,table,local.compatibilityOverride);
    if(compatibility.override) host.log('warn',`Magazine is running on an UNVERIFIED pair (Pi Web UI ${hostInfo.version}, SDK ${sdk.version}) by operator acknowledgement.`);
    const workspace=local.workspace || path.join(host.dataDir,'magazine','workspace');
    if(!path.isAbsolute(workspace)) throw new Error('Magazine workspace must be an absolute path');
    fs.mkdirSync(workspace,{recursive:true,mode:0o700});
    const config={cwd:workspace,
      agentDir:local.agentDir || process.env.PI_CODING_AGENT_DIR || path.join(os.homedir(),'.pi/agent'),
      python:local.python || process.env.MAGAZINE_PYTHON || 'python3',node:process.execPath,
      // 1,800 s per fresh attempt by default; an explicitly configured smaller
      // limit is respected. Nothing is assumed when it is not configured.
      ...(Number.isInteger(local.timeoutSeconds) && local.timeoutSeconds>0 ? {timeoutSeconds:local.timeoutSeconds} : {}),
      extensionPaths:local.workerExtensionPaths || [],
      allowedTools:local.allowedTools || [],
      sdkDir:sdk.dir,sdkVersion:sdk.version,sdkOverride:compatibility.override,compatibility};
    const root=path.join(host.dataDir,'magazine');
    bridge=new ServiceBridge(host.dir,root,config,host.log.bind(host));
    await bridge.ready;
    host.effect('Magazine service',()=>bridge?.close());
    host.route('POST','/rpc',async(req,res)=>{
      try {
        const body=req.body;
        if(!body || typeof body!=='object' || typeof body.action!=='string') return res.status(400).json({ok:false,error:'Invalid request'});
        const value=await bridge.call(body.action,body.data || {});
        res.status(value.ok?200:(value.code || 500)).json(value);
      } catch(error){res.status(503).json({ok:false,error:String(error.message)});}
    });
    // The host's JSON limit is 10 MiB; base64 would exceed it for an 8 MiB
    // upload. Stream binary bytes through a native route, preserving beta.5's
    // complete upload limit and exact retained ZIP without changing the host.
    host.route('POST','/upload',async(req,res)=>{
      try{
        const chunks=[];let size=0;
        for await(const chunk of req){size+=chunk.length;if(size>8*1024*1024)return res.status(413).json({ok:false,error:'ZIP exceeds 8 MiB'});chunks.push(chunk);}
        let file_name;
        try{file_name=decodeURIComponent(String(req.headers['x-magazine-file-name'] || ''));}
        catch{return res.status(400).json({ok:false,error:'Invalid file name'});}
        const value=await bridge.call('import',{file_name,file_data_b64:Buffer.concat(chunks).toString('base64')});
        res.status(value.ok?200:(value.code || 500)).json(value);
      }catch(error){res.status(503).json({ok:false,error:String(error.message)});}
    });
    host.log('info',`Magazine ready (Pi Web UI ${hostInfo.version}, SDK ${sdk.version}, ${compatibility.level}).`);
  },
  deactivate(){bridge?.close();bridge=undefined;}
};
