"""Actual Pi SDK + actual Magazine core; deterministic HTTP provider, no LLM."""
import base64
import hashlib
import io
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class Provider:
    def __init__(self):
        self.requests=[]
        self.fail_once=False
        self.hold=threading.Event()
        fixture=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                value=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.requests.append(value)
                if fixture.fail_once:
                    fixture.fail_once=False
                    self.send_response(500);self.end_headers();self.wfile.write(b'{"error":{"message":"injected failure"}}');return
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.end_headers()
                def chunk(delta,reason=None):
                    value={'id':'chat-test','object':'chat.completion.chunk','created':int(time.time()),
                           'model':value_model,'choices':[{'index':0,'delta':delta,'finish_reason':reason}]}
                    self.wfile.write(('data: '+json.dumps(value)+'\n\n').encode());self.wfile.flush()
                value_model=value['model']
                user=[m for m in value['messages'] if m['role']=='user'][-1]['content']
                if isinstance(user,list):user=''.join(p.get('text','') for p in user)
                last=value['messages'][-1]
                try:
                    chunk({'role':'assistant'})
                    if user=='HOLD':
                        chunk({'content':'Working'})
                        fixture.hold.wait(20)
                    elif user=='TOOL_WRITE' and last['role']!='tool':
                        chunk({'tool_calls':[{'index':0,'id':'call-write','type':'function','function':{'name':'write','arguments':json.dumps({'path':'tool-output.txt','content':'ACTUAL_PI_TOOL_OUTPUT'})}}]},'tool_calls')
                        self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush();return
                    elif user=='TOOL_ECHO' and last['role']!='tool':
                        chunk({'tool_calls':[{'index':0,'id':'call-echo','type':'function','function':{'name':'fixture_echo','arguments':'{"text":"ECHO_INPUT"}'}}]},'tool_calls')
                        self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush();return
                    text='Grüße\r\n第二 строка' if user=='UNICODE' else ('TOOL_DONE' if user in ('TOOL_WRITE','TOOL_ECHO') else ('SECOND_RESPONSE' if user=='SECOND' else 'FIRST_RESPONSE'))
                    chunk({'content':text},'stop')
                    self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    @property
    def url(self):return f'http://127.0.0.1:{self.server.server_port}/v1'
    def close(self):self.hold.set();self.server.shutdown();self.server.server_close()


class RPC:
    def __init__(self,root,config):
        self.root=root;self.config=config;self.responses=queue.Queue()
        self.stderr=(root.parent/'service-stderr.log').open('ab')
        self.process=subprocess.Popen([sys.executable,str(ROOT/'magazine-pi/service.py'),str(root),json.dumps(config)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr,text=True,bufsize=1,env=os.environ.copy())
        def reader():
            for line in self.process.stdout:
                self.responses.put(json.loads(line))
        threading.Thread(target=reader,daemon=True).start()
        assert self.responses.get(timeout=20)=={'event':'ready'}
    def call(self,action,data=None,key='',ok=True):
        self.process.stdin.write(json.dumps({'id':'request','action':action,'owner_key':key,'data':data or {}})+'\n');self.process.stdin.flush()
        value=self.responses.get(timeout=40)
        assert value['id']=='request',value
        if ok:assert value['ok'],value
        return value['result'] if value['ok'] else value
    def close(self,kill=False):
        if self.process.poll() is None:
            if kill:self.process.kill()
            else:self.process.stdin.close()
            self.process.wait(timeout=15)
        self.stderr.close()


@pytest.fixture
def setup(tmp_path):
    provider=Provider()
    agent=tmp_path/'agent';agent.mkdir()
    models={'providers':{'fixture':{'baseUrl':provider.url,'api':'openai-completions','apiKey':'fixture-key',
        'models':[{'id':name,'name':name,'reasoning':False,'input':['text'],
        'contextWindow':8192,'maxTokens':256,'cost':{'input':0,'output':0,'cacheRead':0,'cacheWrite':0}}
        for name in ('model-a','model-b')]}}}
    (agent/'models.json').write_text(json.dumps(models));(agent/'auth.json').write_text('{}')
    cwd=tmp_path/'workspace';cwd.mkdir();(cwd/'AGENTS.md').write_text('FORBIDDEN_PROJECT_CONTEXT_SENTINEL')
    (agent/'APPEND_SYSTEM.md').write_text('FORBIDDEN_GLOBAL_CONTEXT_SENTINEL')
    config={'cwd':str(cwd),'agentDir':str(agent),'timeoutSeconds':30,'extensionPaths':[]}
    rpc=RPC(tmp_path/'data',config)
    key=''  # Normal plugin requests need no Magazine credential.
    try:yield rpc,key,provider,cwd
    finally:
        rpc.close();provider.close()
        evidence = Path(os.environ.get('MAGAZINE_TEST_EVIDENCE_DIR', ROOT/'evidence/pi-session-evidence'))
        shutil.copytree(tmp_path,evidence/tmp_path.name,dirs_exist_ok=True)


def document(*prompts):
    return {'schema_version':'1.0','cartridges':[{'id':f'c{i+1}','job_type':f'job{i+1}','prompt':p} for i,p in enumerate(prompts)]}


def prepare(rpc,key,*prompts,tools=None):
    doc=document(*prompts)
    return rpc.call('prepare',{'magazine':json.dumps(doc),'model_map':{c['job_type']:'fixture/model-'+('a' if i%2==0 else 'b') for i,c in enumerate(doc['cartridges'])},'tool_ids':tools or []},key)['run_id']


def wait_state(rpc,key,rid,status):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        value=rpc.call('status',{'run_id':rid},key)
        if value['status']==status:return value
        time.sleep(.05)
    raise AssertionError(value)


def run_jsonl(rpc, key, rid):
    file = rpc.call('download_jsonl', {'run_id': rid}, key)
    assert file['file_name'] == f'magazine-run-{rid}.jsonl'
    raw = base64.b64decode(file['file_data_b64']).decode('utf-8')
    assert raw.endswith('\n')
    records = [json.loads(line) for line in raw.splitlines()]
    assert all(isinstance(r, dict) and r['run_id'] == rid for r in records)
    assert [r['sequence'] for r in records] == list(range(1, len(records) + 1))
    return records


def test_sequential_routing_context_persistence_export(setup):
    rpc,key,provider,cwd=setup
    rid=prepare(rpc,key,'FIRST','SECOND')
    assert not provider.requests
    assert rpc.call('status',{'run_id':rid},key)['status']=='READY'
    rpc.call('start',{'run_id':rid},key)
    wait_state(rpc,key,rid,'COMPLETED')
    results=rpc.call('results',{'run_id':rid},key)
    assert results['done']==['c1','c2']
    assert results['results']=={'c1':'FIRST_RESPONSE','c2':'SECOND_RESPONSE'}
    assert [r['model'] for r in provider.requests]==['model-a','model-b']
    for req in provider.requests:
        assert sum(m['role']=='user' for m in req['messages'])==1
        assert not any(m['role']=='assistant' for m in req['messages'])
        assert 'FORBIDDEN_' not in json.dumps(req)
        assert not req.get('tools')
    owner='local'
    # pi.9: each attempt's session lives in its own folder (the sandbox grants only that folder).
    sessions=list((rpc.root/'runs'/owner/rid/'sessions').glob('*/*.jsonl'))
    assert len(sessions)==2
    assert len({json.loads(p.read_text().splitlines()[0])['id'] for p in sessions})==2
    records = run_jsonl(rpc, key, rid)
    events = [r for r in records if r['kind'] == 'session_record']
    assert {r['cartridge_id'] for r in events} == {'c1', 'c2'}
    for session in sessions:
        persisted = [json.loads(line) for line in session.read_text().splitlines()]
        exported = [r['record'] for r in events if r['source'] == 'sessions/' + session.parent.name + '/' + session.name]
        assert exported == persisted
    archive=rpc.call('download',{'run_id':rid},key)
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(archive['file_data_b64']))) as z:
        manifest=json.loads(z.read('manifest.json'))
        assert [c['id'] for c in manifest['cartridges']]==['c1','c2']
        for c in manifest['cartridges']:
            assert hashlib.sha256(z.read(c['file'])).hexdigest()==c['sha256']
    rpc.call('export',{'run_id':rid},key)
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (rpc.root/'finished-work').rglob('*') if p.is_file()}
    rpc.call('export',{'run_id':rid},key)
    assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (rpc.root/'finished-work').rglob('*') if p.is_file()}
    rpc.close()
    new=RPC(rpc.root,rpc.config)
    try:
        assert new.call('results',{'run_id':rid},key)==results
        assert new.call('runs',key=key)['runs'][0]['status']=='COMPLETED'
        assert len(provider.requests)==2
    finally:new.close()


def test_actual_tool_and_allowlist(setup):
    rpc,key,provider,cwd=setup
    rid=prepare(rpc,key,'TOOL_WRITE',tools=['write'])
    rpc.call('start',{'run_id':rid},key);wait_state(rpc,key,rid,'COMPLETED')
    assert (cwd/'tool-output.txt').read_text()=='ACTUAL_PI_TOOL_OUTPUT'
    assert rpc.call('results',{'run_id':rid},key)['results']['c1']=='TOOL_DONE'
    assert len(provider.requests)==2
    for request in provider.requests:
        assert [t['function']['name'] for t in request['tools']]==['write']
    records = run_jsonl(rpc, key, rid)
    messages = [r['record']['message'] for r in records if r['kind'] == 'session_record' and r['record'].get('type') == 'message']
    assert any(c.get('type') == 'toolCall' for m in messages for c in m.get('content', []))
    assert any(m.get('role') == 'toolResult' for m in messages)
    assert 'ACTUAL_PI_TOOL_OUTPUT' in json.dumps(records)


def test_active_stop_preserves_completed_prevents_next(setup):
    rpc,key,provider,cwd=setup
    rid=prepare(rpc,key,'FIRST','HOLD','SECOND')
    rpc.call('start',{'run_id':rid},key)
    deadline=time.monotonic()+20
    while len(provider.requests)<2 and time.monotonic()<deadline:time.sleep(.05)
    assert len(provider.requests)==2
    stopped=rpc.call('stop',{'run_id':rid},key)
    state=wait_state(rpc,key,rid,'HALTED')
    assert state['done']==['c1'] and state['current_cartridge']=='c2' and state['todo']==['c3']
    assert state['control']['termination_status']=='CONFIRMED'
    assert state['control']['cancellation_status']=='CONFIRMED'
    assert rpc.call('results',{'run_id':rid},key)['results']=={'c1':'FIRST_RESPONSE'}
    assert len(provider.requests)==2
    assert rpc.call('recover',{'run_id':rid},key,ok=False)['code']==409
    assert rpc.call('start',{'run_id':rid},key,ok=False)['code']==409
    assert rpc.call('stop',{'run_id':rid},key)['status']=='stopped'
    archive=rpc.call('download',{'run_id':rid},key)
    assert [c['id'] for c in archive['manifest']['cartridges']]==['c1']
    records = run_jsonl(rpc, key, rid)
    assert any(r['source'] == 'run/control.json' and r['record']['termination_status'] == 'CONFIRMED' for r in records)
    assert {r['cartridge_id'] for r in records if r['kind'] == 'session_record'} == {'c1', 'c2'}


def test_failure_recovery_requires_explicit_start(setup):
    rpc,key,provider,cwd=setup
    provider.fail_once=True
    rid=prepare(rpc,key,'FIRST','SECOND')
    rpc.call('start',{'run_id':rid},key);wait_state(rpc,key,rid,'HALTED')
    assert len(provider.requests)==1
    halted_records = run_jsonl(rpc, key, rid)
    assert 'injected failure' in json.dumps(halted_records)
    assert rpc.call('recover',{'run_id':rid},key)['status']=='READY'
    assert len(provider.requests)==1
    rpc.call('start',{'run_id':rid},key);state=wait_state(rpc,key,rid,'COMPLETED')
    assert state['done']==['c1','c2']
    assert len(provider.requests)==3
    recovered_records = run_jsonl(rpc, key, rid)
    assert len({r['session_id'] for r in recovered_records if r['kind'] == 'session_record'}) == 3
    assert 'injected failure' in json.dumps(recovered_records)


def test_host_workspace_validation_and_ready_stop(setup):
    rpc,key,provider,cwd=setup
    rid=prepare(rpc,key,'FIRST')
    # Old owner headers/fields are ignored, never identity selectors.
    assert rpc.call('status',{'run_id':rid},'a'*64)['run_id']==rid
    assert rpc.call('status',{'run_id':'../'+rid},key,ok=False)['code']==404
    assert rpc.call('runs',key='a'*64)['runs'][0]['run_id']==rid
    bad={'magazine':json.dumps(document('FIRST')),'model_map':{'job1':'fixture/missing'},'tool_ids':[]}
    assert rpc.call('prepare',bad,key,ok=False)['code']==400
    bad['model_map']={'job1':'fixture/model-a'};bad['tool_ids']=['magazine']
    assert rpc.call('prepare',bad,key,ok=False)['code']==400
    bad['tool_ids']=['write','write']
    assert rpc.call('prepare',bad,key,ok=False)['code']==400
    # READY created by preparation has no execution claim. Baseline refuses
    # STOP when no execution exists; no inference can occur before Start.
    assert rpc.call('stop',{'run_id':rid},key,ok=False)['code']==409
    assert not provider.requests
    records = run_jsonl(rpc, key, rid)
    assert records[-1]['record']['status'] == 'READY'
    assert rpc.call('register',ok=False)['ok'] is False
    assert not (rpc.root/'owners').exists() and not (rpc.root/'import-key').exists()
    secret='retired-owner-secret'
    secret_run = prepare(rpc, key, 'Bearer ' + secret + ' api_key=EXPORT_SECRET_SENTINEL')
    cleaned = json.dumps(run_jsonl(rpc, key, secret_run))
    assert secret not in cleaned and 'EXPORT_SECRET_SENTINEL' not in cleaned


@pytest.mark.parametrize('selected', ['fixture/model-a', 'fixture/model-b'])
def test_selected_default_override_and_exact_host_ids(setup, selected):
    rpc, key, provider, cwd = setup
    cat = rpc.call('catalogue', key=key)
    assert {'fixture/model-a', 'fixture/model-b'} <= {m['id'] for m in cat['models']}
    doc = document('FIRST', 'SECOND')
    rid = rpc.call('prepare', {'magazine': json.dumps(doc), 'default_model': selected, 'tool_ids': []}, key)['run_id']
    assert set(rpc.call('status', {'run_id': rid}, key)['cartridge_models'].values()) == {selected}
    rpc.call('start', {'run_id': rid}, key); wait_state(rpc, key, rid, 'COMPLETED')
    assert [r['model'] for r in provider.requests] == [selected.split('/', 1)[1]] * 2
    other = 'fixture/model-b' if selected.endswith('a') else 'fixture/model-a'
    rid = rpc.call('prepare', {'magazine': json.dumps(doc), 'default_model': selected,
        'model_map': {'legacy-alias': other}, 'cartridge_models': {'c2': 'legacy-alias'}, 'tool_ids': []}, key)['run_id']
    assert rpc.call('status', {'run_id': rid}, key)['cartridge_models'] == {'c1': selected, 'c2': other}
    rpc.call('start', {'run_id': rid}, key); wait_state(rpc, key, rid, 'COMPLETED')
    assert [r['model'] for r in provider.requests[-2:]] == [selected.split('/', 1)[1], other.split('/', 1)[1]]


def test_safe_library_import_exact_retention_and_identity(setup):
    rpc,key,provider,cwd=setup
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        z.writestr('magazine.yaml','schema_version: "1.0"\nname: Test Project\ncartridges:\n  - id: first\n    model: general\n    prompt_file: cartridges/first.md\n')
        z.writestr('cartridges/first.md','FIRST')
    data={'file_name':'test.zip','file_data_b64':base64.b64encode(buffer.getvalue()).decode()}
    result=rpc.call('import',data,key)
    retained=list((rpc.root/'input-zips').glob('*.zip'));assert len(retained)==1
    assert retained[0].read_bytes()==buffer.getvalue()
    preview=rpc.call('preview',{'project_id':result['project_id']},key);assert preview['valid']
    doc=preview['document'];doc['cartridges'][0]['job_type']='configured'
    created=rpc.call('prepare',{'magazine':json.dumps(doc),'project_id':result['project_id'],'model_map':{'configured':'fixture/model-a'},'tool_ids':[]},key)
    assert created['status']=='READY'
    doc['cartridges'][0]['prompt']='TAMPERED'
    assert rpc.call('prepare',{'magazine':json.dumps(doc),'project_id':result['project_id'],'model_map':{'configured':'fixture/model-a'},'tool_ids':[]},key,ok=False)['code']==409
    assert rpc.call('import',data,key,ok=False)['code']==409
    assert len(list((rpc.root/'input-zips').glob('*.zip')))==1
    malicious=io.BytesIO()
    with zipfile.ZipFile(malicious,'w') as z:z.writestr('../escape.txt','bad')
    data['file_name']='bad.zip';data['file_data_b64']=base64.b64encode(malicious.getvalue()).decode()
    assert rpc.call('import',data,key,ok=False)['code']==400
    assert not (rpc.root/'escape.txt').exists()


def test_restart_running_halts_without_resumption(setup):
    rpc,key,provider,cwd=setup
    rid=prepare(rpc,key,'HOLD','SECOND')
    rpc.call('start',{'run_id':rid},key)
    deadline=time.monotonic()+20
    while not provider.requests and time.monotonic()<deadline:time.sleep(.05)
    assert len(provider.requests)==1
    rpc.close(kill=True)
    # Closed stdin tells the SDK worker to abort when its owning service dies.
    new=RPC(rpc.root,rpc.config)
    try:
        status=new.call('status',{'run_id':rid},key);assert status['status']=='RUNNING'
        result=new.call('start',{'run_id':rid},key,ok=False)
        assert result['code']==409
        assert new.call('status',{'run_id':rid},key)['status']=='HALTED'
        assert len(provider.requests)==1
        assert new.call('recover',{'run_id':rid},key)['status']=='READY'
        assert len(provider.requests)==1
    finally:new.close()


def test_concurrent_start_admission_is_single_owner(setup):
    rpc,key,provider,cwd=setup
    rid=prepare(rpc,key,'FIRST','SECOND')
    for request_id in ('start-one','start-two'):
        rpc.process.stdin.write(json.dumps({'id':request_id,'action':'start','owner_key':key,'data':{'run_id':rid}})+'\n')
    rpc.process.stdin.flush()
    values=[rpc.responses.get(timeout=40),rpc.responses.get(timeout=40)]
    assert sum(v['ok'] for v in values)==1,values
    assert next(v for v in values if not v['ok'])['code']==409
    wait_state(rpc,key,rid,'COMPLETED')
    assert len(provider.requests)==2


def test_start_rechecks_availability_and_bad_inputs(setup):
    rpc,key,provider,cwd=setup
    for raw in ('{bad','{"schema_version":"1.0","cartridges":[]}',json.dumps(document('FIRST')).replace('"job1"','null')):
        assert rpc.call('validate',{'magazine':raw},key,ok=False)['code']==400
    rid=prepare(rpc,key,'FIRST')
    model_file=Path(rpc.config['agentDir'])/'models.json'
    before=model_file.read_text();value=json.loads(before);value['providers']['fixture']['models']=value['providers']['fixture']['models'][1:]
    model_file.write_text(json.dumps(value))
    assert rpc.call('start',{'run_id':rid},key,ok=False)['code']==400
    assert rpc.call('status',{'run_id':rid},key)['status']=='READY'
    assert not provider.requests
    model_file.write_text(before)


def test_operator_configured_extension_tool(setup):
    rpc,key,provider,cwd=setup
    extension=cwd/'echo.mjs'
    extension.write_text('export default function(pi) { pi.registerTool({name:"fixture_echo",label:"Fixture echo",description:"Echo fixture text",parameters:{type:"object",properties:{text:{type:"string"}},required:["text"]},async execute(id,params) {return {content:[{type:"text",text:params.text}],details:{}};}}); }')
    assert 'fixture_echo' not in {t['id'] for t in rpc.call('catalogue',key=key)['tools']}
    rpc.close();config={**rpc.config,'extensionPaths':[str(extension)],'allowedTools':['fixture_echo']}
    new=RPC(rpc.root,config)
    try:
        assert [t['id'] for t in new.call('catalogue',key=key)['tools']]==['fixture_echo']
        rid=prepare(new,key,'TOOL_ECHO',tools=['fixture_echo'])
        new.call('start',{'run_id':rid},key);wait_state(new,key,rid,'COMPLETED')
        assert new.call('results',{'run_id':rid},key)['results']['c1']=='TOOL_DONE'
        assert len(provider.requests)==2
        for req in provider.requests:assert [t['function']['name'] for t in req['tools']]==['fixture_echo']
    finally:new.close()


def test_unicode_and_crlf_export_are_exact(setup):
    rpc,key,provider,cwd=setup
    rid=prepare(rpc,key,'UNICODE')
    rpc.call('start',{'run_id':rid},key);wait_state(rpc,key,rid,'COMPLETED')
    file=rpc.call('download',{'run_id':rid},key)
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(file['file_data_b64']))) as archive:
        manifest=json.loads(archive.read('manifest.json'))
        entry=manifest['cartridges'][0]
        data=archive.read(entry['file'])
        assert data=='Grüße\r\n第二 строка'.encode('utf-8')
        assert hashlib.sha256(data).hexdigest()==entry['sha256']
