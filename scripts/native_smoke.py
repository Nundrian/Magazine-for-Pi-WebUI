"""Isolated installed Pi Web UI host with the real native Magazine plugin."""
import argparse
import base64
import json
import io
import os
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
PLUGIN_SOURCE=ROOT/'magazine-pi'
sys.path.insert(0,str(ROOT/'tests'))
from test_pi_integration import Provider, document


def start_host(data,agent,cwd,port,token=''):
    plugin=data/'plugins/magazine-pi'
    shutil.copytree(PLUGIN_SOURCE,plugin,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    (plugin/'config.json').write_text(json.dumps({'workspace':str(cwd),'agentDir':str(agent),'python':'python','timeoutSeconds':60}))
    env={**os.environ,'PI_WEB_DATA_DIR':str(data),'PI_CODING_AGENT_DIR':str(agent),
         'PI_WEB_HOST':'0.0.0.0','PI_WEB_PORT':str(port),'PI_WEB_CWD':str(cwd),'PI_WEB_LOCALE':'en',
         'PI_WEB_SDK':'global','PI_WEB_PLUGIN_CATALOG_URL':'off','PI_WEB_TOKEN':token,
         'PI_OFFLINE':'1','PI_TELEMETRY':'0',
         'PYTHONDONTWRITEBYTECODE':'1'}
    log=(ROOT/f'evidence/native-host-{port}.log').open('wb')
    process=subprocess.Popen(['pi-web-ui','--no-browser'],env=env,cwd=cwd,stdout=log,stderr=subprocess.STDOUT)
    return process,log


def api(port,action,data=None,key='',token='',expected=200):
    headers={'Content-Type':'application/json','X-Magazine-Owner':key}
    if token:headers['Authorization']='Bearer '+token
    req=Request(f'http://127.0.0.1:{port}/plugins-api/magazine-pi/rpc',
        data=json.dumps({'action':action,'data':data or {}}).encode(),headers=headers)
    try:
        with urlopen(req,timeout=65) as response:
            code=response.status;raw=response.read()
    except HTTPError as error:code=error.code;raw=error.read()
    assert code==expected,(code,raw[:300])
    return json.loads(raw) if raw[:1]==b'{' else {'raw':raw.decode()}


def ready(port,process):
    for _ in range(100):
        if process.poll() is not None:raise RuntimeError('Native Pi host exited; inspect native-host log')
        try:
            with urlopen(f'http://127.0.0.1:{port}/api/health',timeout=1) as r:
                if r.status==200:return
        except Exception:time.sleep(.1)
    raise TimeoutError('Pi host not ready')


def main():
    global PLUGIN_SOURCE
    parser=argparse.ArgumentParser();parser.add_argument('--keep',action='store_true');parser.add_argument('--plugin-source',type=Path);args=parser.parse_args()
    if args.plugin_source:PLUGIN_SOURCE=args.plugin_source
    base=ROOT/'evidence/native-data';base.mkdir(exist_ok=True)
    if not any(base.iterdir()) and (ROOT/'evidence/native-snapshot').exists():
        shutil.copytree(ROOT/'evidence/native-snapshot',base,dirs_exist_ok=True)
    agent=base/'agent';agent.mkdir(exist_ok=True)
    cwd=ROOT/'evidence/native-workspace';cwd.mkdir(exist_ok=True)
    provider=Provider()
    models={'providers':{'fixture':{'baseUrl':provider.url,'api':'openai-completions','apiKey':'fixture-key',
        'models':[{'id':name,'name':name,'reasoning':False,'input':['text'],'contextWindow':8192,'maxTokens':256,
        'cost':{'input':0,'output':0,'cacheRead':0,'cacheWrite':0}} for name in ('model-a','model-b')]}}}
    (agent/'models.json').write_text(json.dumps(models));(agent/'auth.json').write_text('{}')
    host,log=start_host(base/'web',agent,cwd,8787)
    auth_host=auth_log=None
    try:
        ready(8787,host)
        subprocess.run(['node',str(ROOT/'scripts/activate-plugin.mjs'),'8787'],check=True)
        key=api(8787,'register')['result']['owner_key']
        assert not provider.requests
        catalogue=api(8787,'catalogue',key=key)['result']
        assert {'fixture/model-a','fixture/model-b'}=={m['id'] for m in catalogue['models']}
        assert 'write' in {t['id'] for t in catalogue['tools']}
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            archive.writestr('magazine.yaml','schema_version: "1.0"\nname: Native ZIP '+str(time.time_ns())+'\ncartridges:\n  - id: first\n    model: worker\n    prompt_file: cartridges/first.md\n')
            archive.writestr('cartridges/first.md','FIRST')
        import_key=(base/'web/magazine/import-key').read_text().strip()
        upload=Request('http://127.0.0.1:8787/plugins-api/magazine-pi/upload',data=buffer.getvalue(),headers={
            'Content-Type':'application/octet-stream','X-Magazine-Owner':key,
            'X-Magazine-Import-Key':import_key,'X-Magazine-File-Name':f'native-{time.time_ns()}.zip'})
        with urlopen(upload,timeout=10) as response:
            imported=json.load(response)
        assert imported['ok'],imported
        assert buffer.getvalue() in [p.read_bytes() for p in (base/'web/magazine/input-zips').glob('*.zip')]
        doc=document('FIRST','SECOND')
        run=api(8787,'prepare',{'magazine':json.dumps(doc),'model_map':{'job1':'fixture/model-a','job2':'fixture/model-b'},'tool_ids':[]},key)['result']
        assert run['status']=='READY' and not provider.requests
        rid=run['run_id'];api(8787,'start',{'run_id':rid},key)
        for _ in range(200):
            status=api(8787,'status',{'run_id':rid},key)['result']
            if status['status']=='COMPLETED':break
            time.sleep(.1)
        assert status['status']=='COMPLETED',status
        assert api(8787,'results',{'run_id':rid},key)['result']['results']=={'c1':'FIRST_RESPONSE','c2':'SECOND_RESPONSE'}
        stranger=api(8787,'register')['result']['owner_key']
        api(8787,'results',{'run_id':rid},stranger,expected=404)
        api(8787,'runs',expected=401)
        archive=api(8787,'download',{'run_id':rid},key)['result']
        (ROOT/'evidence/native-results.zip').write_bytes(base64.b64decode(archive['file_data_b64']))
        auth_host,auth_log=start_host(base/'auth-web',agent,cwd,8788,'fixture-shared-host-token')
        ready(8788,auth_host)
        subprocess.run(['node',str(ROOT/'scripts/activate-plugin.mjs'),'8788','fixture-shared-host-token'],check=True)
        api(8788,'register',expected=401)
        api(8788,'register',token='fixture-shared-host-token')
        auth_host.terminate();auth_host.wait(timeout=15);auth_log.close();auth_host=None
        report={'pi_web_ui':'0.96.1','pi_sdk':'1.0.2','native_tab_module_http':None,'checks':[
            'plugin activation and native HTTP route','catalogue without inference','READY without inference',
            'native binary ZIP import and exact retention',
            'explicit Start two sequential SDK sessions','canonical results and ZIP','owner separation',
            'missing owner rejection','shared Pi host token covers plugin endpoint'],
            'provider':'deterministic local HTTP fixture; no live LLM'}
        with urlopen('http://127.0.0.1:8787/plugins/magazine-pi/client/entry.mjs') as response:
            report['native_tab_module_http']=response.status
        (ROOT/'evidence/native-api.json').write_text(json.dumps(report,indent=2)+'\n')
        print('NATIVE_API_PASS; browser test host ready on port 8787',flush=True)
        if args.keep:
            done=False
            def stop(*_):
                nonlocal done;done=True
            signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
            while not done:time.sleep(.2)
    finally:
        if auth_host and auth_host.poll() is None:auth_host.terminate();auth_host.wait(timeout=15);auth_log.close()
        host.terminate();host.wait(timeout=15);log.close();provider.close()
        (ROOT/'evidence/native-provider-requests.json').write_text(json.dumps(provider.requests,indent=2)+'\n')
        shutil.copytree(base,ROOT/'evidence/native-snapshot',dirs_exist_ok=True)


if __name__=='__main__':main()
