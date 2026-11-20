"""One small keyless workflow through the packaged native plugin and Ollama.

All Pi/model/plugin files are disposable test files. The existing Ollama service
is queried and used, never reconfigured. No provider/model is built into Magazine.
"""
import argparse
import base64
import hashlib
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen

from portable_auth_smoke import Host, document, digest_tree


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plugin-zip', type=Path, required=True)
    p.add_argument('--scratch', type=Path, required=True)
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--ollama-url', required=True)
    p.add_argument('--model', required=True)
    args = p.parse_args()
    assert not args.scratch.exists()
    args.evidence.mkdir(parents=True, exist_ok=True)
    endpoint = args.ollama_url.rstrip('/')
    with urlopen(endpoint+'/api/tags', timeout=10) as response:
        tags = json.load(response)
    chosen = next(m for m in tags['models'] if m['name'] == args.model)
    host = Host(args.scratch, args.evidence, SimpleNamespace(url=endpoint+'/v1'), '')
    host.install(args.plugin_zip, 'candidate')
    models = {'providers': {'ollama': {'baseUrl': endpoint+'/v1',
        'api': 'openai-completions', 'apiKey': 'ollama', 'models': [{
        'id': args.model, 'name': args.model, 'reasoning': False, 'input': ['text'],
        'contextWindow': 8192, 'maxTokens': 512,
        'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}}]}}}
    (host.agent/'models.json').write_text(json.dumps(models))
    protected = digest_tree(host.agent)
    report = {'PASS': False, 'provider': 'live Ollama', 'model': args.model,
              'model_digest': chosen['digest'], 'host_settings_changed': False}
    try:
        host.start()
        cat = host.rpc('catalogue')['result']
        selected = 'ollama/'+args.model
        assert selected in {m['id'] for m in cat['models']}
        assert cat['tools'] == []
        run = host.rpc('prepare', {'magazine': json.dumps(document(
            'Reply with exactly MAGAZINE_TEST_ONE.',
            'Compute 17 + 25. Reply with the number only.')),
            'default_model': selected, 'tool_ids': []})['result']
        rid = run['run_id']; report['run_id'] = rid
        assert run['status'] == 'READY'
        host.rpc('start', {'run_id': rid})
        deadline = time.monotonic()+240
        while time.monotonic() < deadline:
            state = host.rpc('status', {'run_id': rid})['result']
            if state['status'] in ('COMPLETED','HALTED'): break
            time.sleep(.25)
        assert state['status'] == 'COMPLETED', state
        results = host.rpc('results', {'run_id': rid})['result']['results']
        assert results['c1'].strip().rstrip('.') == 'MAGAZINE_TEST_ONE', results
        assert results['c2'].strip() == '42', results
        file = host.rpc('download_jsonl', {'run_id': rid})['result']
        raw = base64.b64decode(file['file_data_b64'])
        records = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
        assert {r['cartridge_id'] for r in records if r['kind']=='session_record'} == {'c1','c2'}
        (args.evidence/file['file_name']).write_bytes(raw)
        host.stop(); host.start()
        assert host.rpc('results', {'run_id': rid})['result']['results'] == results
        after = digest_tree(host.agent)
        assert all(after.get(name)==digest for name,digest in protected.items())
        report.update(PASS=True, results=results, selected_model_passed_unchanged=True,
            two_fresh_sessions=True, no_magazine_credentials=True,
            jsonl_records=len(records), restart_persistence=True)
    except Exception as error:
        report['failure'] = str(error)
        raise
    finally:
        host.stop()
        shutil.copytree(host.data/'magazine', args.evidence/'magazine-snapshot', dirs_exist_ok=True)
        (args.evidence/'live-results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__': main()
