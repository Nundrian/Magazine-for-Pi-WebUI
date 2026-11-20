"""Complete native Pi view regression: real host/SDK, deterministic provider.

Uses isolated Linux scratch storage, no production settings or credentials.
The full view is exercised in Firefox/Chromium, native dark/light themes,
and desktop/mobile viewports. Browser test dependencies are test-only.
"""
import argparse
import hashlib
import io
import json
import secrets
import shutil
import time
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright
from portable_auth_smoke import Host, Provider, document, digest_tree

CONTROLS = ['message', 'projects', 'reload',
    'zip', 'import', 'json', 'validate', 'preview', 'individual',
    'shared-label', 'shared-model', 'routing', 'tools', 'prepare', 'ready-hint',
    'history', 'run-summary', 'start', 'progress', 'stop', 'recover', 'export', 'download', 'download-jsonl', 'results']


def wait_text(page, selector, value):
    page.wait_for_function('([s,t])=>document.querySelector(s)?.textContent.includes(t)', arg=[selector, value])


def open_magazine(page):
    # Pi moves plugin tabs to its existing overflow menu on narrow screens.
    plugins = page.get_by_role('button', name='Plugins', exact=False)
    if not plugins.count():
        page.get_by_role('button', name='More plugin buttons', exact=False).click()
    plugins.click()
    page.get_by_text('Magazine', exact=True).filter(visible=True).click()


def load_document(page, prompts):
    details = page.locator('.mag details').filter(has=page.locator('[data-id=json]'))
    if details.get_attribute('open') is None: details.locator('summary').click()
    page.locator('[data-id=json]').fill(json.dumps(document(*prompts)))
    page.locator('[data-id=validate]').click()
    wait_text(page, '[data-id=message]', 'Document validated.')


def prepare(page, model='fixture/model-a', individual=False):
    page.locator('[data-id=individual]').set_checked(individual)
    page.locator('[data-id=shared-model]').select_option(model)
    if individual:
        assert page.locator('[data-id=shared-label]').is_visible()
        selects = page.locator('[data-id=routing] select')
        for i in range(selects.count()): selects.nth(i).select_option('fixture/model-b' if i % 2 else '')
    page.locator('[data-id=prepare]').click()
    wait_text(page, '[data-id=run-summary]', 'READY')
    assert page.locator('[data-id=start]').is_enabled()
    assert page.locator('[data-id=stop]').is_disabled()  # READY without execution is not Pause/Stop
    return page.evaluate("localStorage.getItem('magazine-pi-run')")


def download_run(page, out, rid):
    assert page.locator('[data-id=download-jsonl]').is_enabled()
    with page.expect_download() as download:
        page.get_by_role('button', name='Download Run JSONL', exact=True).click()
    assert download.value.suggested_filename == f'magazine-run-{rid}.jsonl'
    target = out / download.value.suggested_filename
    download.value.save_as(str(target))
    rows = [json.loads(line) for line in target.read_text(encoding='utf-8').splitlines()]
    assert rows and all(r['run_id'] == rid for r in rows)
    assert [r['sequence'] for r in rows] == list(range(1, len(rows) + 1))
    return rows


def layout(page):
    value = page.locator('.mag').evaluate('''e=>{
      const style=getComputedStyle(e), root=getComputedStyle(document.documentElement);
      const button=e.querySelector('[data-id=prepare]'), section=e.querySelector('section');
      return {scrollWidth:e.scrollWidth,width:e.clientWidth,font:style.fontFamily,color:style.color,
        hostFont:root.getPropertyValue('--sans').trim(),hostColor:root.getPropertyValue('--text').trim(),
        buttonRadius:getComputedStyle(button).borderRadius,sectionBackground:getComputedStyle(section).backgroundColor,
        sectionSideBorder:getComputedStyle(section).borderLeftWidth,
        controls:[...e.querySelectorAll('input,select,textarea,button,summary')].filter(x=>x.getBoundingClientRect().width>0)
          .map(x=>({name:x.dataset.id||x.textContent.slice(0,35),left:x.getBoundingClientRect().left,right:x.getBoundingClientRect().right,width:x.getBoundingClientRect().width}))};
    }''')
    assert value['scrollWidth'] <= value['width'] + 1, value
    assert value['sectionSideBorder'] == '0px' and value['sectionBackground'] == 'rgba(0, 0, 0, 0)'
    assert value['buttonRadius'] == '8px'
    width = page.viewport_size['width']
    assert all(c['left'] >= -1 and c['right'] <= width + 1 for c in value['controls']), value['controls']
    return {k: v for k, v in value.items() if k != 'controls'}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plugin-zip', type=Path, required=True)
    p.add_argument('--previous-plugin-zip', type=Path, required=True)
    p.add_argument('--scratch', type=Path, required=True)
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--engine', choices=['firefox', 'chromium'])
    p.add_argument('--case', help='Run one named engine-theme-screen case for final packaged smoke')
    p.add_argument('--skip-completed-report', type=Path, help='Skip full cases already proven in retained evidence')
    p.add_argument('--keep', action='store_true', help='Hold isolated host for visual review after checks')
    p.add_argument('--authenticated', action='store_true', help='Use a generated disposable host token and test export access rejection')
    p.add_argument('--port', type=int, default=8787)
    args = p.parse_args()
    completed = set()
    if args.skip_completed_report:
        completed = {row['case'] for row in json.loads(args.skip_completed_report.read_text())['cases'] if row.get('PASS')}
    assert args.port == 8787, 'Use a dedicated network namespace; fixture host port is 8787'
    assert not args.scratch.exists(), 'Scratch must be new'
    args.evidence.mkdir(parents=True, exist_ok=True)
    provider = Provider()
    host = Host(args.scratch, args.evidence, provider, secrets.token_hex(32) if args.authenticated else '')
    report = {'PASS': False, 'host': 'Pi Web UI 0.96.1 / SDK 1.0.2',
              'provider': 'deterministic HTTP fixture; not live LLM', 'cases': []}
    try:
        # Seed previous-version data, then upgrade with actual supported CLI.
        host.install(args.previous_plugin_zip, 'previous')
        host.start()
        owner = host.rpc('register')['result']['owner_key']
        seed = host.rpc('prepare', {'magazine': json.dumps(document('FIRST')),
            'model_map': {'job1': 'fixture/model-a'}, 'tool_ids': []}, owner)['result']
        completed_seed = host.rpc('prepare', {'magazine': json.dumps(document('FIRST', 'SECOND')),
            'model_map': {'job1': 'fixture/model-a', 'job2': 'fixture/model-b'}, 'tool_ids': []}, owner)['result']
        host.rpc('start', {'run_id': completed_seed['run_id']}, owner)
        deadline = time.monotonic() + 20
        while host.rpc('status', {'run_id': completed_seed['run_id']}, owner)['result']['status'] != 'COMPLETED':
            assert time.monotonic() < deadline
            time.sleep(.05)
        host.stop()
        stored = digest_tree(host.data / 'magazine')
        original_config = (host.data / 'plugins/magazine-pi/config.json').read_bytes()
        assert not host.install(args.plugin_zip, 'candidate', True)
        assert digest_tree(host.data / 'magazine') == stored
        assert (host.data / 'plugins/magazine-pi/config.json').read_bytes() == original_config
        # Fixture-only expose read to exercise selection; production remains untouched/no tools.
        config = json.loads(original_config); config['allowedTools'] = ['read']
        (host.data / 'plugins/magazine-pi/config.json').write_text(json.dumps(config))
        host.start()
        assert host.rpc('status', {'run_id': seed['run_id']}, owner)['result']['status'] == 'READY'
        if args.authenticated:
            host.rpc('download_jsonl', {'run_id': seed['run_id']}, owner, authenticated=False, expected=401)
        host.rpc('download_jsonl', {'run_id': seed['run_id']})
        assert host.rpc('results', {'run_id': completed_seed['run_id']})['result']['results'] == {'c1': 'FIRST_RESPONSE', 'c2': 'SECOND_RESPONSE'}
        host.rpc('download_jsonl', {'run_id': completed_seed['run_id']})
        assert digest_tree(host.data / 'magazine') == stored
        report['legacy_records_accessible_without_key_and_byte_unchanged'] = True
        report['upgrade_preserves_ready_data_config'] = True
        key = ''
        with sync_playwright() as pw:
            for engine in ([args.engine] if args.engine else ['firefox', 'chromium']):
                for theme in ('dark', 'ayu-light'):
                    for screen, dimensions in [('desktop', {'width': 1440, 'height': 1000}),
                                               ('mobile', {'width': 390, 'height': 844})]:
                        label = f'{engine}-{theme}-{screen}'
                        if args.case and args.case != label: continue
                        if label in completed: continue
                        out = args.evidence / label; out.mkdir()
                        row = {'case': label, 'checks': []}; report['cases'].append(row)
                        context = getattr(pw, engine).launch_persistent_context(str(args.scratch / label),
                            headless=True, viewport=dimensions, accept_downloads=True)
                        try:
                            page = context.pages[0]
                            page.add_init_script("localStorage.setItem('pi-web-ui:theme'," + json.dumps(theme) + ')')
                            page.goto(host.base + ('/?token=' + host.token if host.token else ''))
                            open_magazine(page)
                            page.wait_for_function("document.querySelector('[data-id=message]')?.className === 'status' && document.querySelector('[data-id=shared-model]')?.options.length === 3")
                            # Wait for the actual native theme stylesheet, not a synthetic palette.
                            if theme != 'dark':
                                page.wait_for_function("document.getElementById('theme-stylesheet')?.sheet !== null && getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() !== '#0d0e12'")
                            for control in CONTROLS: assert page.locator('.mag [data-id=' + control + ']').count() == 1, control
                            assert page.locator('[data-id=download-jsonl]').is_disabled()
                            assert page.locator('.mag input[type=password]').count() == 0
                            assert 'Your owner key' not in page.locator('.mag').inner_text()
                            assert 'administrator' not in page.locator('.mag').inner_text().lower()
                            assert page.evaluate("localStorage.getItem('magazine-pi-owner')") is None
                            row['checks'].append('all existing controls present')
                            row['layout'] = layout(page)
                            row['browser_version'] = context.browser.version
                            page.screenshot(path=str(out / 'empty.png'), full_page=True)
                            page.locator('[data-id=reload]').click()
                            page.locator('[data-id=shared-model] option').filter(has_text='model-b').wait_for(state='attached')
                            # Native ZIP upload/import, retained input, library load/preview.
                            archive = io.BytesIO()
                            with zipfile.ZipFile(archive, 'w') as z:
                                z.writestr('magazine.yaml', f'schema_version: "1.0"\nname: {label}\ncartridges:\n  - id: library-first\n    model: worker\n    prompt_file: cartridges/first.md\n')
                                z.writestr('cartridges/first.md', 'FIRST')
                            page.locator('[data-id=zip]').set_input_files({'name': label + '.zip', 'mimeType': 'application/zip', 'buffer': archive.getvalue()})
                            page.locator('[data-id=import]').click()
                            option = page.locator('[data-id=projects] option').filter(has_text=label)
                            option.wait_for(state='attached')
                            page.locator('[data-id=projects]').select_option(option.get_attribute('value'))
                            wait_text(page, '[data-id=preview]', 'library-first')
                            assert archive.getvalue() in [p.read_bytes() for p in (host.data / 'magazine/input-zips').glob('*.zip')]
                            row['checks'].append('no Magazine credentials, refresh, keyless native ZIP import/retention, library preview')
                            # Invalid JSON feedback and keyboard focus remain usable.
                            load_document(page, ['FIRST', 'SECOND'])
                            page.locator('[data-id=json]').fill('{invalid')
                            page.locator('[data-id=validate]').click()
                            page.wait_for_function("document.querySelector('[data-id=message]').classList.contains('error')")
                            load_document(page, ['FIRST', 'SECOND'])
                            page.locator('[data-id=json]').focus(); page.keyboard.press('Tab')
                            assert page.locator('[data-id=validate]').evaluate("e=>e.matches(':focus-visible') && getComputedStyle(e).outlineWidth === '2px'")
                            page.locator('[data-id=preview] summary').first.click()
                            assert page.locator('[data-id=preview] pre').first.is_visible()
                            page.get_by_text('Use a Magazine JSON document', exact=True).click()
                            before = len(provider.requests)
                            rid = prepare(page, individual=True)
                            assert download_run(page, out, rid)[-1]['record']['status'] == 'READY'
                            assert len(provider.requests) == before
                            assert 'fixture/model-b' in page.locator('[data-id=run-summary]').inner_text()
                            page.screenshot(path=str(out / 'ready.png'), full_page=True)
                            row['ready_layout'] = layout(page)
                            page.locator('[data-id=start]').click()
                            wait_text(page, '[data-id=run-summary]', 'COMPLETED')
                            page.locator('[data-id=progress]').click()
                            wait_text(page, '[data-id=message]', 'Progress checked. COMPLETED')
                            assert [r['model'] for r in provider.requests[before:]] == ['model-a', 'model-b']
                            assert page.locator('[data-id=results]').inner_text().count('RESPONSE') == 2
                            result = host.rpc('results', {'run_id': rid}, key)['result']
                            assert result['results'] == {'c1': 'FIRST_RESPONSE', 'c2': 'SECOND_RESPONSE'}
                            for r in provider.requests[before:]:
                                assert sum(m['role'] == 'user' for m in r['messages']) == 1
                                assert not any(m['role'] == 'assistant' for m in r['messages'])
                            sessions = list((host.data / 'magazine/runs/local' / rid / 'sessions').glob('*.jsonl'))
                            assert len(sessions) == 2
                            assert len({json.loads(p.read_text().splitlines()[0])['id'] for p in sessions}) == 2
                            page.locator('[data-id=export]').click()
                            wait_text(page, '[data-id=run-summary]', 'EXPORTED')
                            with page.expect_download() as download:
                                page.locator('[data-id=download]').click()
                            download.value.save_as(str(out / 'results.zip'))
                            with zipfile.ZipFile(out / 'results.zip') as z:
                                for c in json.loads(z.read('manifest.json'))['cartridges']:
                                    assert hashlib.sha256(z.read(c['file'])).hexdigest() == c['sha256']
                            page.screenshot(path=str(out / 'completed.png'), full_page=True)
                            completed_jsonl = download_run(page, out, rid)
                            assert {r['cartridge_id'] for r in completed_jsonl if r['kind'] == 'session_record'} == {'c1', 'c2'}
                            row['completed_layout'] = layout(page)
                            row['checks'].append('invalid/valid JSON, keyboard focus, prompt preview, per-cartridge routing, READY without inference, explicit Start, independent saved sessions, progress, COMPLETED, export and actual download')
                            # Shared model/tool controls, then STOP on actual active streaming SDK worker.
                            load_document(page, ['FIRST', 'HOLD', 'SECOND'])
                            page.get_by_text('Use a Magazine JSON document', exact=True).click()
                            page.locator('[data-id=tools] input').check()
                            before = len(provider.requests)
                            stop_id = prepare(page)
                            assert 'Tools: read' in page.locator('[data-id=run-summary]').inner_text()
                            page.locator('[data-id=start]').click()
                            deadline = time.monotonic() + 15
                            while len(provider.requests) < before + 2 and time.monotonic() < deadline: time.sleep(.05)
                            assert len(provider.requests) == before + 2
                            wait_text(page, '[data-id=run-summary]', 'RUNNING')
                            page.locator('[data-id=stop]').click()
                            wait_text(page, '[data-id=run-summary]', 'HALTED')
                            assert page.locator('[data-id=recover]').is_disabled()
                            assert page.locator('[data-id=start]').is_disabled()
                            assert len(provider.requests) == before + 2
                            assert host.rpc('results', {'run_id': stop_id}, key)['result']['results'] == {'c1': 'FIRST_RESPONSE'}
                            assert [t['function']['name'] for t in provider.requests[-1]['tools']] == ['read']
                            page.locator('[data-id=export]').click()
                            wait_text(page, '[data-id=run-summary]', 'EXPORTED')
                            page.screenshot(path=str(out / 'stopped.png'), full_page=True)
                            stopped_jsonl = download_run(page, out, stop_id)
                            assert any(r['source'] == 'run/control.json' for r in stopped_jsonl)
                            row['checks'].append('shared routing/tool selection, active STOP preserves first response, prevents next cartridge, disables recovery/Start, partial export')
                            # A failure is distinct from permanent STOP: UI recovery is explicit READY/Start.
                            page.locator('[data-id=tools] input').uncheck()
                            load_document(page, ['FIRST', 'SECOND'])
                            page.get_by_text('Use a Magazine JSON document', exact=True).click()
                            recovery_id = prepare(page)
                            provider.fail_once = True
                            page.locator('[data-id=start]').click()
                            wait_text(page, '[data-id=run-summary]', 'HALTED')
                            assert 'injected failure' in json.dumps(download_run(page, out, recovery_id))
                            page.locator('[data-id=recover]').click()
                            wait_text(page, '[data-id=run-summary]', 'READY')
                            before_recovery = len(provider.requests)
                            page.wait_for_timeout(150)
                            assert len(provider.requests) == before_recovery
                            page.locator('[data-id=start]').click()
                            wait_text(page, '[data-id=run-summary]', 'COMPLETED')
                            recovered_jsonl = download_run(page, out, recovery_id)
                            assert len({r['session_id'] for r in recovered_jsonl if r['kind'] == 'session_record'}) == 3
                            row['checks'].append('one-click whole-run JSONL: READY, completed multi-cartridge, stopped partial, halted error and all recovered sessions')
                            page.locator('[data-id=history]').select_option(rid)
                            wait_text(page, '[data-id=run-summary]', rid)
                            assert page.locator('[data-id=results]').inner_text().count('RESPONSE') == 2
                            page.reload()
                            open_magazine(page)
                            wait_text(page, '[data-id=run-summary]', rid)
                            assert page.locator('[data-id=results]').inner_text().count('RESPONSE') == 2
                            row['checks'].append('failure/recovery to READY, explicit restart, history selection and page-reload ownership/results preservation')
                            host.stop()
                            host.start()
                            assert host.rpc('results', {'run_id': rid})['result']['results'] == {'c1': 'FIRST_RESPONSE', 'c2': 'SECOND_RESPONSE'}
                            assert any(p['name'] == label for p in host.rpc('library')['result']['projects']['workflows'])
                            agent_after = digest_tree(host.agent)
                            changed = [name for name, digest in host.agent_before.items() if agent_after.get(name) != digest]
                            assert not changed, 'Protected agent files changed: ' + repr(changed)
                            row['native_agent_files_added'] = sorted(set(agent_after) - set(host.agent_before))
                            page.reload(); open_magazine(page)
                            wait_text(page, '[data-id=run-summary]', rid)
                            assert page.locator('.mag input[type=password]').count() == 0
                            row['checks'].append('Pi restart retains keyless history, results, projects, and unchanged provider/settings files')
                            row['run_ids'] = [rid, stop_id, recovery_id]
                            row['PASS'] = True
                        except Exception as error:
                            row['failure'] = str(error)
                            page.screenshot(path=str(out / 'failure.png'), full_page=True)
                            (out / 'failure-view.txt').write_text(page.locator('body').inner_text())
                            raise
                        finally: context.close()
        assert report['cases'], 'No matrix case matched'
        report['PASS'] = True
        if args.keep:
            print('NATIVE_UI_PASS; isolated visual-review host listening', flush=True)
            host.process.wait()
    finally:
        host.stop(); provider.close()
        report['provider_requests'] = len(provider.requests)
        (args.evidence / 'provider-requests.json').write_text(json.dumps(provider.requests, indent=2))
        (args.evidence / 'native-ui-results.json').write_text(json.dumps(report, indent=2) + '\n')
        # Keep actual canonical states and JSONL, excluding venv/browser profiles and dependencies.
        shutil.copytree(host.data / 'magazine', args.evidence / 'magazine-snapshot', dirs_exist_ok=True)
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
