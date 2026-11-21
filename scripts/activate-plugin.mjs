// Exercise the installed host's real WebSocket hello/plugin discovery protocol.
import {createRequire} from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const executable=fs.realpathSync(execFileSync('which',['pi-web-ui'],{encoding:'utf8'}).trim());
const {WebSocket}=createRequire(path.resolve(path.dirname(executable),'../package.json'))('ws');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const port=process.argv[2] || '8787',token=process.argv[3];
const ws=new WebSocket(`ws://127.0.0.1:${port}/ws`,{headers:token?{Authorization:'Bearer '+token}:{}});
const timer=setTimeout(()=>{console.error('Plugin discovery timeout');process.exit(1);},30000);
ws.on('open',()=>ws.send(JSON.stringify({type:'hello',clientId:'magazine-native-acceptance'})));
ws.on('message',data=>{
  const value=JSON.parse(data);
  if(value.type!=='plugins')return;
  const plugin=value.plugins.find(p=>p.id==='magazine-pi');
  if(!plugin || plugin.error){console.error(JSON.stringify(value));process.exit(1);}
  const evidence=process.env.MAGAZINE_TEST_EVIDENCE || path.join(root,'evidence');
  fs.mkdirSync(evidence,{recursive:true});
  fs.writeFileSync(path.join(evidence,`native-plugin-${port}.json`),JSON.stringify(plugin,null,2)+'\n');
  clearTimeout(timer);ws.close();
});
ws.on('error',error=>{console.error(error.message);process.exit(1);});
