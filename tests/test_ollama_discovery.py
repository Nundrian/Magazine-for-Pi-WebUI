"""Real Pi SDK and Magazine service; metadata-only fake Ollama, no inference."""
import json
import os
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pi6_fakes import RPC

ROOT=Path(__file__).resolve().parents[1]
pytestmark=pytest.mark.skipif(not os.environ.get('MAGAZINE_PI_SDK'),reason='requires real SDK tree')


@pytest.fixture
def ollama(tmp_path):
    state={'names':['kept','downloaded','embed'],'failed':False,'requests':[]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def answer(self,data,code=200):
            self.send_response(code);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        def do_GET(self):
            state['requests'].append(self.path)
            assert self.path=='/api/tags','unexpected network operation'
            self.answer({'models':[{'name':n} for n in state['names']]},503 if state['failed'] else 200)
        def do_POST(self):
            state['requests'].append(self.path)
            assert self.path=='/api/show','inference is forbidden in discovery acceptance'
            value=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            caps=['embedding','tools','thinking'] if value['model']=='embed' else ['completion','vision']
            self.answer({'capabilities':caps,'model_info':{'general.architecture':'fixture','fixture.context_length':8192}})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    agent=tmp_path/'agent';agent.mkdir();cwd=tmp_path/'workspace';cwd.mkdir()
    definition={'id':'kept','name':'Custom kept','reasoning':True,'input':['text'],
                'contextWindow':4096,'maxTokens':1024,'cost':{'input':0,'output':0,'cacheRead':0,'cacheWrite':0}}
    model_file=agent/'models.json'
    model_file.write_text(json.dumps({'providers':{
        'ollama':{'baseUrl':f'http://127.0.0.1:{server.server_port}/v1','api':'openai-completions','apiKey':'fixture',
                  'models':[definition,dict(definition,id='removed')]},
        'other':{'baseUrl':'http://127.0.0.1:9/v1','api':'openai-completions','apiKey':'fixture',
                 'models':[dict(definition,id='other-model')]}}}))
    before=model_file.read_bytes()
    rpc=RPC(tmp_path/'data',{'cwd':str(cwd),'agentDir':str(agent),'allowedTools':['read'],'extensionPaths':[]})
    try:
        yield state,rpc,agent,cwd
        assert model_file.read_bytes()==before
        assert all(p in ('/api/tags','/api/show') for p in state['requests'])
    finally:
        rpc.close();server.shutdown();server.server_close()


def test_live_catalogue_tracks_install_remove_outage_and_recovery_without_restarting(ollama):
    state,rpc,agent,cwd=ollama
    ids=lambda:{m['id'] for m in rpc.call('catalogue')['models']}
    assert ids()=={'ollama/kept','ollama/downloaded','other/other-model'}
    state['names']=['downloaded','later','embed']
    assert ids()=={'ollama/downloaded','ollama/later','other/other-model'}
    state['failed']=True
    failed=rpc.call('catalogue')
    assert {m['id'] for m in failed['models']}=={'other/other-model'}
    assert failed['ollama']['warnings']
    state['failed']=False
    assert ids()=={'ollama/downloaded','ollama/later','other/other-model'}


def test_discovery_preserves_saved_assignments_and_refuses_removed_models_for_new_work(ollama):
    state,rpc,agent,cwd=ollama
    doc={'schema_version':'1.0','cartridges':[{'id':'one','job_type':'j','prompt':'never executed'}]}
    prepared=rpc.call('prepare',{'magazine':json.dumps(doc),'model_map':{'j':'ollama/kept'},'tool_ids':[]})
    meta=rpc.root/'runs/local'/prepared['run_id']/'meta.json';before=meta.read_bytes()
    state['names']=['downloaded','embed']
    assert 'ollama/kept' not in {m['id'] for m in rpc.call('catalogue')['models']}
    assert rpc.call('status',{'run_id':prepared['run_id']})['model_map']=={'j':'ollama/kept'}
    assert meta.read_bytes()==before
    refused=rpc.call('prepare',{'magazine':json.dumps(doc),'model_map':{'j':'ollama/kept'},'tool_ids':[]},ok=False)
    assert refused['code']==400 and 'unavailable' in refused['error'].lower()
    rejected=rpc.call('start',{'run_id':prepared['run_id']},ok=False)
    assert rejected['code'] in (400,409)
    assert rpc.call('status',{'run_id':prepared['run_id']})['status']=='READY'


def test_real_worker_resolves_downloaded_model_and_stops_before_any_prompt(ollama):
    state,rpc,agent,cwd=ollama
    script='''
import {execute} from './magazine-pi/worker.mjs';
import fs from 'node:fs';
const request=JSON.parse(fs.readFileSync(0,'utf8'));
try {await execute(request,()=>{},()=>true,()=>{});throw new Error('unexpected success');}
catch(error){if(error.message!=='Worker stopped before inference')throw error;}
process.stdout.write('DISCOVERED_MODEL_RESOLVED_WITHOUT_INFERENCE');
'''
    session_dir=cwd/'sessions';session_dir.mkdir()
    request={'action':'run','model':'ollama/downloaded','tools':['read'],'prompt':'must not execute',
             'cwd':str(cwd),'agentDir':str(agent),'dataDir':str(rpc.root),'sessionDir':str(session_dir)}
    result=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(request),
                          cwd=ROOT,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'DISCOVERED_MODEL_RESOLVED_WITHOUT_INFERENCE' in result.stdout
