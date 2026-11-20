"""Real Pi CLI/SDK and restarted Firefox/Chromium; isolated deterministic provider.

Run on the supported Linux host with browser test requirements and Playwright
browsers installed. All scratch paths must be new, isolated Linux storage.
Never pass production tokens: this script generates its own disposable token.
"""
import argparse
import base64
import hashlib
import io
import json
import os
import secrets
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_pi_integration import Provider, document


def digest_tree(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


class Host:
    def __init__(self, root, evidence, provider, token):
        self.root, self.evidence, self.token = root, evidence, token
        self.data, self.agent, self.cwd = (root / p for p in ('web', 'agent', 'workspace'))
        for p in (self.data, self.agent, self.cwd): p.mkdir(parents=True)
        (self.cwd / 'AGENTS.md').write_text('FORBIDDEN_PROJECT_CONTEXT_SENTINEL')
        (self.agent / 'APPEND_SYSTEM.md').write_text('FORBIDDEN_GLOBAL_CONTEXT_SENTINEL')
        (self.agent / 'auth.json').write_text('{}')
        (self.agent / 'settings.json').write_text('{"portableTestSetting":true}')
        models = {'providers': {'fixture': {'baseUrl': provider.url, 'api': 'openai-completions',
            'apiKey': 'fixture-key', 'models': [{'id': n, 'name': n, 'reasoning': False,
                'input': ['text'], 'contextWindow': 8192, 'maxTokens': 256,
                'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}}
                for n in ('model-a', 'model-b')]}}}
        (self.agent / 'models.json').write_text(json.dumps(models))
        # Other plugin/settings data must not be affected by installation.
        (self.data / 'operator-settings.json').write_text('{"preserve":true}')
        other = self.data / 'plugins/unrelated-data'; other.mkdir(parents=True)
        (other / 'keep.txt').write_text('PRESERVE_OTHER_PLUGIN')
        self.agent_before = digest_tree(self.agent)
        self.process = self.log = None
        self.base = 'http://127.0.0.1:8787'

    def install(self, archive, label, upgrade=False):
        extracted = self.root / label
        with zipfile.ZipFile(archive) as z: z.extractall(extracted)
        config = self.data / 'plugins/magazine-pi/config.json'
        saved = config.read_bytes() if upgrade else None
        command = ['pi-web-ui', 'install', str(extracted / 'magazine-pi'), '--data-dir', str(self.data)]
        if upgrade: command.append('--force')
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        (self.evidence / (label + '-install.log')).write_text(result.stdout + result.stderr)
        overwritten = saved is not None and (not config.exists() or config.read_bytes() != saved)
        if saved is not None:
            config.write_bytes(saved)  # documented operator upgrade step
        else:
            config.write_text(json.dumps({'workspace': str(self.cwd), 'agentDir': str(self.agent),
                'python': sys.executable, 'timeoutSeconds': 60, 'allowedTools': [], 'workerExtensionPaths': []}))
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                assert (self.data / 'plugins' / name).read_bytes() == z.read(name), name
        return overwritten

    def start(self):
        env = {**os.environ, 'PI_WEB_DATA_DIR': str(self.data), 'PI_CODING_AGENT_DIR': str(self.agent),
            'PI_WEB_HOST': '127.0.0.1', 'PI_WEB_PORT': '8787', 'PI_WEB_CWD': str(self.cwd),
            'PI_WEB_LOCALE': 'en', 'PI_WEB_SDK': 'global', 'PI_WEB_PLUGIN_CATALOG_URL': 'off',
            'PI_WEB_TOKEN': self.token, 'PI_OFFLINE': '1', 'PI_TELEMETRY': '0', 'PYTHONDONTWRITEBYTECODE': '1'}
        self.log = (self.evidence / 'host.log').open('ab')
        self.process = subprocess.Popen(['pi-web-ui', '--no-browser'], env=env, cwd=self.cwd,
                                        stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(200):
            assert self.process.poll() is None, 'Host exited: inspect isolated host log'
            try:
                with urlopen(self.base + '/api/health', timeout=1) as r:
                    assert r.status == 200
                    assert not r.headers.get('Set-Cookie'), 'Health exposed credential'
                    break
            except OSError: time.sleep(.1)
        else: raise TimeoutError('Pi startup')
        subprocess.run(['node', str(ROOT / 'scripts/activate-plugin.mjs'), '8787', self.token],
            env={**env, 'MAGAZINE_TEST_EVIDENCE': str(self.evidence)}, check=True)

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate(); self.process.wait(timeout=20)
        if self.log: self.log.close()

    def rpc(self, action, data=None, owner='', authenticated=True, expected=200):
        headers = {'Content-Type': 'application/json', 'X-Magazine-Owner': owner}
        if self.token and authenticated: headers['Authorization'] = 'Bearer ' + self.token
        request = Request(self.base + '/plugins-api/magazine-pi/rpc',
            data=json.dumps({'action': action, 'data': data or {}}).encode(), headers=headers)
        try:
            with urlopen(request, timeout=65) as r: code, raw = r.status, r.read()
        except HTTPError as e: code, raw = e.code, e.read()
        assert code == expected, (action, code, raw[:200])
        return json.loads(raw) if code == 200 else None


def browser_check(pw, host, engine, profile, first, screenshot):
    browser = getattr(pw, engine)
    context = browser.launch_persistent_context(str(profile), headless=True)
    try:
        page = context.pages[0]
        page.goto(host.base + ('/?token=' + host.token if first and host.token else '/'))
        page.wait_for_function("!new URL(location.href).searchParams.has('token')")
        page.wait_for_timeout(1500)
        # Native menu label discovery retained as non-secret evidence.
        (screenshot.parent / (engine + '-buttons.txt')).write_text('\n'.join(page.get_by_role('button').all_text_contents()))
        page.screenshot(path=str(screenshot.parent / (engine + '-before-menu.png')))
        (screenshot.parent / (engine + '-button-elements.json')).write_text(json.dumps(page.locator('button').evaluate_all('(els)=>els.map(e=>({text:e.textContent,label:e.getAttribute("aria-label"),title:e.title}))'), indent=2))
        page.get_by_role('button', name='Plugins', exact=False).click()
        plugin_button = page.get_by_text('Magazine', exact=True).filter(visible=True)
        (screenshot.parent / (engine + '-plugin-menu.txt')).write_text(page.locator('body').inner_text())
        plugin_button.first.click(timeout=10000)
        page.locator('.mag h1').wait_for()
        page.wait_for_function("document.querySelector('.mag [data-id=message]')?.textContent !== 'Loading Magazine…'")
        assert page.locator('.mag [data-id=message]').get_attribute('class') == 'status'
        owner = page.evaluate("localStorage.getItem('magazine-pi-owner')")
        assert owner
        response = page.request.post(host.base + '/plugins-api/magazine-pi/rpc',
            headers={'X-Magazine-Owner': owner}, data={'action': 'catalogue', 'data': {}})
        assert response.status == 200
        assert response.json()['result']['tools'] == []
        cookies = context.cookies()
        credential = next((c for c in cookies if c['name'] == 'pi_web_token'), None)
        if host.token:
            assert credential and credential['httpOnly'] and credential['sameSite'] == 'Strict'
            assert 31400000 < credential['expires'] - time.time() < 31600000
        else: assert credential is None
        page.screenshot(path=str(screenshot))
        return {'engine': engine, 'version': browser.name, 'native_view': True,
                'ordinary_url': page.url == host.base + '/', 'owner_hash': hashlib.sha256(owner.encode()).hexdigest(),
                'persistent_cookie': bool(credential), 'httpOnly': credential['httpOnly'] if credential else None,
                'sameSite': credential['sameSite'] if credential else None}
    finally: context.close()  # terminates actual browser process; next call relaunches same profile


def workflow(host, provider):
    key = host.rpc('register')['result']['owner_key']
    before = len(provider.requests)
    catalogue = host.rpc('catalogue', owner=key)['result']
    assert catalogue['tools'] == []
    assert {'fixture/model-a', 'fixture/model-b'} == {m['id'] for m in catalogue['models']}
    run = host.rpc('prepare', {'magazine': json.dumps(document('FIRST', 'SECOND')),
        'model_map': {'job1': 'fixture/model-a', 'job2': 'fixture/model-b'}, 'tool_ids': []}, key)['result']
    rid = run['run_id']; assert run['status'] == 'READY' and len(provider.requests) == before
    host.rpc('start', {'run_id': rid}, key)
    for _ in range(200):
        state = host.rpc('status', {'run_id': rid}, key)['result']
        if state['status'] == 'COMPLETED': break
        time.sleep(.1)
    assert state['status'] == 'COMPLETED', state
    results = host.rpc('results', {'run_id': rid}, key)['result']
    assert results['results'] == {'c1': 'FIRST_RESPONSE', 'c2': 'SECOND_RESPONSE'}
    requests = provider.requests[before:]
    assert [r['model'] for r in requests] == ['model-a', 'model-b']
    for r in requests:
        assert sum(m['role'] == 'user' for m in r['messages']) == 1
        assert not any(m['role'] == 'assistant' for m in r['messages'])
        assert not r.get('tools') and 'FORBIDDEN_' not in json.dumps(r)
    owner = hashlib.sha256(key.encode()).hexdigest()
    sessions = list((host.data / 'magazine/runs' / owner / rid / 'sessions').glob('*.jsonl'))
    assert len(sessions) == 2
    assert len({json.loads(p.read_text().splitlines()[0])['id'] for p in sessions}) == 2
    exported = host.rpc('download', {'run_id': rid}, key)['result']
    raw = base64.b64decode(exported['file_data_b64'])
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for c in json.loads(z.read('manifest.json'))['cartridges']:
            assert hashlib.sha256(z.read(c['file'])).hexdigest() == c['sha256']
    (host.evidence / 'results.zip').write_bytes(raw)
    stranger = host.rpc('register')['result']['owner_key']
    host.rpc('results', {'run_id': rid}, stranger, expected=404)
    host.rpc('runs', expected=401)
    return key, rid, results


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plugin-zip', type=Path, required=True)
    p.add_argument('--previous-plugin-zip', type=Path, required=True)
    p.add_argument('--scratch', type=Path, required=True)
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--case', choices=['no-token-fresh', 'no-token-upgrade', 'token-fresh', 'token-upgrade'])
    args = p.parse_args()
    assert not args.scratch.exists(), 'Scratch must be new; existing work will not be overwritten'
    args.evidence.mkdir(parents=True, exist_ok=True)
    provider = Provider(); report = {'PASS': False, 'provider': 'deterministic HTTP fixture; actual Pi host/SDK',
        'pi_web_ui': '0.96.1', 'pi_sdk': '1.0.2', 'cases': []}
    try:
        with sync_playwright() as pw:
            for authenticated in (False, True):
                for upgrade in (False, True):
                    name = ('token' if authenticated else 'no-token') + ('-upgrade' if upgrade else '-fresh')
                    if args.case and args.case != name: continue
                    evidence = args.evidence / name; evidence.mkdir()
                    host = Host(args.scratch / name, evidence, provider, secrets.token_hex(32) if authenticated else '')
                    row = {'name': name, 'browsers': []}; report['cases'].append(row)
                    try:
                        host.install(args.previous_plugin_zip if upgrade else args.plugin_zip, 'initial')
                        host.start()
                        # Pi may initialize its own auth/settings on first startup;
                        # compare upgrades/restarts with that host-initialized baseline.
                        row['pi_initial_agent_changes'] = sorted(k for k in set(digest_tree(host.agent)) | set(host.agent_before)
                            if digest_tree(host.agent).get(k) != host.agent_before.get(k))
                        host.agent_before = digest_tree(host.agent)
                        host.rpc('register', authenticated=False, expected=401 if authenticated else 200)
                        key, rid, results = workflow(host, provider)
                        row['run_id'] = rid; row['workflow'] = 'READY, explicit Start, COMPLETED, two isolated saved sessions, routing, results ZIP'
                        for engine in ('firefox', 'chromium'):
                            profile = host.root / ('profile-' + engine)
                            first = browser_check(pw, host, engine, profile, True, evidence / (engine + '-initial.png'))
                            reopened = browser_check(pw, host, engine, profile, False, evidence / (engine + '-reopened.png'))
                            assert first['owner_hash'] == reopened['owner_hash']
                            row['browsers'].append({**reopened, 'process_restart': True})
                        host.stop()
                        stored = digest_tree(host.data / 'magazine')
                        config = (host.data / 'plugins/magazine-pi/config.json').read_bytes()
                        if upgrade:
                            row['cli_replaced_config'] = host.install(args.plugin_zip, 'upgrade', True)
                            assert config == (host.data / 'plugins/magazine-pi/config.json').read_bytes()
                            assert stored == digest_tree(host.data / 'magazine')
                        host.start()
                        assert host.rpc('results', {'run_id': rid}, key)['result'] == results
                        assert stored == digest_tree(host.data / 'magazine')
                        for engine in ('firefox', 'chromium'):
                            reopened = browser_check(pw, host, engine, host.root / ('profile-' + engine), False,
                                evidence / (engine + '-host-restarted.png'))
                            assert reopened['owner_hash'] == next(b['owner_hash'] for b in row['browsers'] if b['engine'] == engine)
                        row['host_restart_and_preserved_data'] = True
                        if upgrade:
                            # Prove new packaged version executes after update, not merely loading old results.
                            workflow(host, provider); row['post_upgrade_workflow'] = True
                        if authenticated:
                            host.stop(); host.token = secrets.token_hex(32); host.start()
                            host.rpc('register', authenticated=False, expected=401)
                            for engine in ('firefox', 'chromium'):
                                context = getattr(pw, engine).launch_persistent_context(str(host.root / ('profile-' + engine)), headless=True)
                                try:
                                    response = context.request.get(host.base + '/')
                                    assert response.status == 401
                                    assert not any(c['name'] == 'pi_web_token' for c in context.cookies())
                                finally: context.close()
                                browser_check(pw, host, engine, host.root / ('profile-' + engine), True,
                                              evidence / (engine + '-rotated-token.png'))
                            row['token_rotation_rejects_old_cookie_and_native_resignin'] = True
                            # Deliberately expire a test cookie, not production credentials.
                            for engine in ('firefox', 'chromium'):
                                context = getattr(pw, engine).launch_persistent_context(str(host.root / ('profile-' + engine)), headless=True)
                                try:
                                    old = next(c for c in context.cookies() if c['name'] == 'pi_web_token')
                                    context.add_cookies([{**old, 'expires': time.time() - 60}])
                                    assert context.request.get(host.base + '/').status == 401
                                finally: context.close()
                                browser_check(pw, host, engine, host.root / ('profile-' + engine), True,
                                              evidence / (engine + '-expired-cookie.png'))
                            row['expired_cookie_rejected_native_resignin'] = True
                        after_agent = digest_tree(host.agent)
                        row['agent_changes'] = sorted(k for k in host.agent_before
                            if after_agent.get(k) != host.agent_before[k])
                        row['native_pi_added_agent_files'] = sorted(set(after_agent) - set(host.agent_before))
                        assert not row['agent_changes'], row['agent_changes']
                        assert (host.data / 'operator-settings.json').read_text() == '{"preserve":true}'
                        assert (host.data / 'plugins/unrelated-data/keep.txt').read_text() == 'PRESERVE_OTHER_PLUGIN'
                        row['settings_models_other_plugin_preserved'] = True
                        row['PASS'] = True
                    finally: host.stop()
        report['PASS'] = True
    finally:
        provider.close()
        report['provider_requests'] = len(provider.requests)
        (args.evidence / 'provider-requests.json').write_text(json.dumps(provider.requests, indent=2))
        (args.evidence / 'portable-auth-results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
