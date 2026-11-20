"""Packaged native installation without a custom Magazine config or credentials."""
import argparse
import json
import time
from pathlib import Path

from portable_auth_smoke import Host, Provider, document


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plugin-zip', type=Path, required=True)
    p.add_argument('--scratch', type=Path, required=True)
    p.add_argument('--evidence', type=Path, required=True)
    args = p.parse_args()
    assert not args.scratch.exists()
    args.evidence.mkdir(parents=True, exist_ok=True)
    provider = Provider()
    host = Host(args.scratch, args.evidence, provider, '')
    report = {'PASS': False}
    try:
        host.install(args.plugin_zip, 'candidate')
        (host.data/'plugins/magazine-pi/config.json').unlink()  # fixture only
        host.start()
        assert (host.data/'magazine/workspace').is_dir()
        assert not (host.data/'magazine/owners').exists()
        assert not (host.data/'magazine/import-key').exists()
        cat = host.rpc('catalogue')['result']
        assert cat['tools'] == []
        assert 'administrator' not in host.rpc('library')['result']['projects']['message'].lower()
        selected = 'fixture/model-a'
        assert selected in {m['id'] for m in cat['models']}
        run = host.rpc('prepare', {'magazine': json.dumps(document('FIRST')),
            'default_model': selected, 'tool_ids': []})['result']
        assert not provider.requests
        host.rpc('start', {'run_id': run['run_id']})
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            result = host.rpc('status', {'run_id': run['run_id']})['result']
            if result['status'] in ('COMPLETED','HALTED'): break
            time.sleep(.05)
        assert result['status'] == 'COMPLETED', result
        report.update(PASS=True, no_config_required=True, no_magazine_credentials=True,
            no_tools_by_default=True, default_workspace_created=True, run_id=run['run_id'])
    finally:
        host.stop(); provider.close()
        (args.evidence/'default-results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__': main()
