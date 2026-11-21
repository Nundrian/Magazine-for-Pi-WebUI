"""Bounded real Ollama inference through the actual Magazine/Pi adapter."""
import hashlib
import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from test_pi_integration import RPC

parser=argparse.ArgumentParser()
parser.add_argument('--model',required=True)
parser.add_argument('--ollama-url',required=True,help='Reachable Ollama root URL, without /v1')
args=parser.parse_args()
MODEL=args.model
OLLAMA=args.ollama_url.rstrip('/')
base=ROOT/'evidence/live-data';base.mkdir(exist_ok=True)
agent=base/'agent';agent.mkdir(exist_ok=True)
cwd=ROOT/'evidence/live-workspace';cwd.mkdir(exist_ok=True)
(cwd/'live-input.txt').write_text('MAGAZINE_PI_FILE_VERIFIED\n')
with urlopen(OLLAMA+'/api/tags',timeout=5) as response:catalogue=json.load(response)
actual=next(m for m in catalogue['models'] if m['name']==MODEL)
(agent/'models.json').write_text(json.dumps({'providers':{'ollama':{'baseUrl':OLLAMA+'/v1',
    'api':'openai-completions','apiKey':'ollama-local','models':[{'id':MODEL,'name':MODEL,'reasoning':False,
    'input':['text'],'contextWindow':8192,'maxTokens':2048,'cost':{'input':0,'output':0,'cacheRead':0,'cacheWrite':0}}]}}}))
(agent/'auth.json').write_text('{}')
config={'cwd':str(cwd),'agentDir':str(agent),'timeoutSeconds':180,'extensionPaths':[]}
rpc=RPC(base/'magazine',config);key=rpc.call('register')['owner_key']
report={'provider':'live local Ollama','model':MODEL,'ollama_digest':actual['digest'],'tests':{}}


def prepare(prompts,tools):
    doc={'schema_version':'1.0','cartridges':[{'id':f'live-{i+1}','job_type':'worker','prompt':p} for i,p in enumerate(prompts)]}
    return rpc.call('prepare',{'magazine':json.dumps(doc),'model_map':{'worker':'ollama/'+MODEL},'tool_ids':tools},key)['run_id']


def wait(rid):
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        status=rpc.call('status',{'run_id':rid},key)
        if status['status'] in ('COMPLETED','HALTED'):return status
        time.sleep(.2)
    raise TimeoutError('Live run did not settle')


try:
    rid=prepare(['Reply with exactly MAGAZINE_PI_LIVE_ONE.','Compute 17 + 25. Reply with the number only.'],[])
    rpc.call('start',{'run_id':rid},key);state=wait(rid)
    assert state['status']=='COMPLETED',state
    results=rpc.call('results',{'run_id':rid},key)
    assert results['results']=={'live-1':'MAGAZINE_PI_LIVE_ONE','live-2':'42'},results
    report['tests']['two_independent_live_sessions']={'PASS':True,'run_id':rid,'results':results['results']}
    print('LIVE_SEQUENTIAL_PASS',flush=True)
    rid=prepare(['Use the read tool to read live-input.txt in the current working directory. Reply only with its single line.'],['read'])
    rpc.call('start',{'run_id':rid},key);state=wait(rid)
    assert state['status']=='COMPLETED',state
    result=rpc.call('results',{'run_id':rid},key)['results']['live-1']
    assert result=='MAGAZINE_PI_FILE_VERIFIED',result
    owner=hashlib.sha256(key.encode()).hexdigest()
    transcripts=[p.read_text() for p in (rpc.root/'runs'/owner/rid/'sessions').glob('*.jsonl')]
    assert any('"toolName":"read"' in text or '"toolName": "read"' in text for text in transcripts)
    report['tests']['live_read_tool']={'PASS':True,'run_id':rid,'result':result}
    print('LIVE_READ_TOOL_PASS',flush=True)
    rid=prepare(['Write the integers from 1 to 100000, one per line. Continue until all are written.','Reply with NEXT_SHOULD_NOT_START.'],[])
    rpc.call('start',{'run_id':rid},key)
    activity=rpc.root/'runs'/owner/rid/'active-worker.json'
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        if activity.exists() and json.loads(activity.read_text()).get('phase')=='streaming':break
        time.sleep(.05)
    assert activity.exists() and json.loads(activity.read_text())['phase']=='streaming'
    stop=rpc.call('stop',{'run_id':rid},key);state=wait(rid)
    assert state['status']=='HALTED' and state['done']==[] and state['todo']==['live-2'],state
    assert state['control']['cancellation_status']=='CONFIRMED',state
    report['tests']['live_streaming_stop']={'PASS':True,'run_id':rid,'state':state}
    print('LIVE_STOP_PASS',flush=True)
except Exception as error:
    report['failure']=str(error)
    raise
finally:
    rpc.close()
    (ROOT/'evidence/live-llm.json').write_text(json.dumps(report,indent=2)+'\n')
    shutil.copytree(base,ROOT/'evidence/live-session-evidence',dirs_exist_ok=True)
