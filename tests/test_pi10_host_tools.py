"""pi.10: trusted host-tool broker and dependency preflight (no network: scripted connections)."""
from concurrent.futures import ThreadPoolExecutor
import json
import os

import pytest

import host_tools as ht

UC_KEY = 'uehl_' + 'A' * 40
RK_KEY = 'rk_' + 'B' * 44


class FakeResponse:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self, n=-1):
        return self._body if n < 0 else self._body[:n]


class FakeConn:
    def __init__(self, routes, log, fail=None):
        self.routes, self.log, self.fail = routes, log, fail

    def request(self, method, path, body=None, headers=None):
        if self.fail:
            raise self.fail
        self.log.append({'method': method, 'path': path, 'body': body, 'headers': headers})
        self.key = (method, path.split('?')[0])

    def getresponse(self):
        status, body = self.routes.get(self.key, (404, {'detail': 'not found'}))
        return FakeResponse(status, json.dumps(body).encode() if not isinstance(body, bytes) else body)

    def close(self):
        pass


def broker(tmp_path, routes=None, fail=None, agent=None):
    log = []
    b = ht.HostBroker(tmp_path / 'data', agent, connect=lambda h, p: (log.append((h, p)) or FakeConn(routes or {}, log, fail)))
    return b, log


def test_parse_requires():
    assert ht.parse_requires(None) == {}
    r = ht.parse_requires({'services': {'unreal_connector': {'projects': ['nebulaswamp'], 'min_api': '0.2.0'},
                                        'resource_kit': True}, 'exclusive_unreal': True})
    assert r['services']['unreal_connector']['operations'] == ['health']
    assert r['services']['resource_kit']['operations'] == ['whoami']
    assert 'open_project_in_unreal' not in r['services']['resource_kit']['operations']
    for bad in ({'services': {'other': {}}}, {'services': {'resource_kit': {'operations': ['open_project_in_unreal']}}},
                {'services': {'unreal_connector': {'operations': ['../admin/shutdown']}}}, {'extra': 1},
                {'services': {'unreal_connector': {'min_api': 'latest'}}}):
        with pytest.raises(ValueError):
            ht.parse_requires(bad)


def test_unconfigured_service_is_a_clear_refusal(tmp_path):
    b, log = broker(tmp_path)
    with pytest.raises(ht.HostToolError, match='not connected in Magazine'):
        b.raw_call('unreal_connector', 'health')
    assert log == []


def test_configure_validates_and_never_returns_the_key(tmp_path):
    b, _ = broker(tmp_path)
    with pytest.raises(ht.HostToolError):
        b.configure('resource_kit', 'not-a-key')
    with pytest.raises(ht.HostToolError):
        b.configure('unreal_connector', UC_KEY, host='evil.example')
    s = b.configure('unreal_connector', UC_KEY)
    assert s['unreal_connector']['configured'] and UC_KEY not in json.dumps(s)
    if os.name == 'posix':
        assert oct((tmp_path / 'data' / ht.CONFIG_NAME).stat().st_mode)[-3:] == '600'


def test_call_attaches_key_masks_it_and_records_evidence(tmp_path):
    routes = {('POST', '/project_status'): (200, {'project_id': 'p1', 'echo': UC_KEY, 'job_id': 'j20261125-1'})}
    b, log = broker(tmp_path, routes)
    b.configure('unreal_connector', UC_KEY)
    policy = ht.parse_requires({'services': {'unreal_connector': {'operations': ['project_status'], 'projects': ['p1']}}})
    ev = tmp_path / 'run' / 'host-calls' / 'c001.jsonl'
    ev.parent.parent.mkdir()
    r = b.call('unreal_connector', 'project_status', {'project_id': 'p1'}, policy, ev)
    assert log[0] == ('host.docker.internal', 8765)
    assert log[1]['headers']['Authorization'] == 'Bearer ' + UC_KEY and log[1]['method'] == 'POST'
    assert UC_KEY not in json.dumps(r) and r['data']['echo'] == '[REDACTED]'
    assert r['refs']['job_id'] == 'j20261125-1'
    rec = json.loads(ev.read_text())
    assert rec['operation'] == 'project_status' and rec['refs']['job_id'] == 'j20261125-1'
    assert UC_KEY not in ev.read_text() and rec['project_id'] == 'p1'


def test_policy_refusals(tmp_path):
    b, log = broker(tmp_path, {('POST', '/submit_change'): (200, {})})
    b.configure('unreal_connector', UC_KEY)
    policy = ht.parse_requires({'services': {'unreal_connector': {'operations': ['health', 'project_status'],
                                                                  'projects': ['p1']}}})
    with pytest.raises(ht.HostToolError, match='not among the operations'):
        b.call('unreal_connector', 'submit_change', {}, policy)
    with pytest.raises(ht.HostToolError, match='declared projects'):
        b.call('unreal_connector', 'project_status', {'project_id': 'production'}, policy)
    with pytest.raises(ht.HostToolError, match='does not declare resource_kit'):
        b.call('resource_kit', 'whoami', {}, policy)
    with pytest.raises(ht.HostToolError, match='Unknown operation'):
        b.raw_call('unreal_connector', 'admin_shutdown')
    with pytest.raises(ht.HostToolError, match='JSON object'):
        b.raw_call('unreal_connector', 'project_status', ['x'])
    with pytest.raises(ht.HostToolError, match='64 KiB'):
        b.raw_call('unreal_connector', 'project_status', {'x': 'y' * 70000})
    assert [x for x in log if isinstance(x, dict)] == []


def test_401_gets_a_do_not_retry_hint_and_unreachable_is_explained(tmp_path):
    b, _ = broker(tmp_path, {('GET', '/health'): (401, {'detail': 'missing or invalid bearer token'})})
    b.configure('unreal_connector', UC_KEY)
    policy = ht.parse_requires({'services': {'unreal_connector': {}}})
    assert 'Do not retry' in b.call('unreal_connector', 'health', {}, policy)['hint']
    b2, _ = broker(tmp_path, fail=ConnectionRefusedError())
    with pytest.raises(ht.HostToolError, match='not reachable'):
        b2.raw_call('unreal_connector', 'health')


def test_resource_kit_key_reused_from_pi_mcp_pairing(tmp_path):
    agent = tmp_path / 'agent'
    agent.mkdir()
    (agent / 'mcp.json').write_text(json.dumps({'mcpServers': {'3d-resource-kit': {
        'url': 'http://host.docker.internal:8766/mcp', 'headers': {'Authorization': 'Bearer ' + RK_KEY}}}}))
    b, log = broker(tmp_path, {('GET', '/tools/whoami'): (200, {
        'service': 'resource_kit', 'version': '0.4.2', 'scopes': ['discover', 'library']})}, agent=agent)
    s = b.summary()['resource_kit']
    assert s['configured'] and s['source'] == 'pi-mcp' and RK_KEY not in json.dumps(s)
    pre = b.preflight(ht.parse_requires({'services': {'resource_kit': {'scopes': ['library', 'projects_write']}}}))
    assert not pre['ok'] and 'projects_write' in pre['problems'][0]
    (agent / 'mcp.json').write_text(json.dumps({'mcpServers': {'3d-resource-kit': {
        'url': 'http://attacker.example:8766/mcp', 'headers': {'Authorization': 'Bearer ' + RK_KEY}}}}))
    assert not b.summary()['resource_kit']['configured']


def test_preflight_checks_projects_api_and_idle_unreal(tmp_path):
    routes = {('GET', '/health'): (200, {'service': 'uehl', 'version': '0.2.0', 'unreal_processes_running': 1}),
              ('GET', '/capabilities'): (200, {'operations': {'health': {}, 'submit_change': {}}}),
              ('GET', '/projects'): (200, {'projects': [{'project_id': 'uehldisposable'}]})}
    b, log = broker(tmp_path, routes)
    b.configure('unreal_connector', UC_KEY)
    req = ht.parse_requires({'services': {'unreal_connector': {'projects': ['nebulaswamp'], 'min_api': '0.2.0',
                                                               'capabilities': ['submit_change']}},
                             'exclusive_unreal': True})
    pre = b.preflight(req)
    names = {c['check']: c['ok'] for c in pre['checks']}
    assert names['unreal_connector.auth'] and names['unreal_connector.api'] and names['unreal_connector.capabilities']
    assert not names['unreal_connector.projects'] and not names['unreal_connector.unreal_idle']
    assert not pre['ok'] and any('nebulaswamp' in p for p in pre['problems'])
    # every request carried the bearer key: the protected API was really called
    assert all(x['headers']['Authorization'] == 'Bearer ' + UC_KEY for x in log if isinstance(x, dict))


def test_preflight_unauthorised_and_unreachable(tmp_path):
    b, _ = broker(tmp_path, {('GET', '/health'): (401, {})})
    b.configure('unreal_connector', UC_KEY)
    pre = b.preflight(ht.parse_requires({'services': {'unreal_connector': {}}}))
    assert not pre['ok'] and 'HTTP 401' in pre['problems'][0]
    b2, _ = broker(tmp_path, fail=OSError('refused'))
    pre = b2.preflight(ht.parse_requires({'services': {'unreal_connector': {}}}))
    assert not pre['ok'] and 'not reachable' in pre['problems'][0]


def test_tool_description_is_compact_and_task_specific():
    text = ht.tool_description(ht.parse_requires({'services': {'unreal_connector': {
        'operations': ['discover_projects', 'project_status'], 'projects': ['nebulaswamp']}}}))
    assert 'nebulaswamp' in text and 'project_status' in text and 'submit_change' not in text
    assert len(text) < 1500


def test_connector_rejects_its_other_keys_by_name(tmp_path):
    """Owner hit this live: the Resource Kit link key (ulk_) was stored as the API key and preflight got 401."""
    b, _ = broker(tmp_path)
    for key, words in (('ulk_' + 'a' * 43, 'link key'), ('fsk_' + 'b' * 43, 'browser-sync'), ('op_' + 'c' * 43, 'operator'),
                       ('rk_' + 'd' * 44, 'Resource Kit')):
        with pytest.raises(ht.HostToolError, match=words):
            b.configure('unreal_connector', key)
    assert not b.summary()['unreal_connector']['configured']


def test_preflight_checks_service_identity_and_never_follows_redirects(tmp_path):
    b, log = broker(tmp_path, {('GET', '/health'): (302, {'location': 'http://invalid.example/'})})
    b.configure('unreal_connector', UC_KEY)
    policy = ht.parse_requires({'services': {'unreal_connector': {}}})
    assert not b.preflight(policy)['ok']
    assert [entry['path'] for entry in log if isinstance(entry, dict)] == ['/health']


def test_wrong_success_payload_is_not_an_authenticated_probe(tmp_path):
    b, _ = broker(tmp_path, {('GET', '/health'): (200, {'ok': True})})
    b.configure('unreal_connector', UC_KEY)
    assert not b.preflight(ht.parse_requires({'services': {'unreal_connector': {}}}))['ok']


def test_all_response_fields_and_receipt_references_are_redacted(tmp_path):
    b, _ = broker(tmp_path, {('POST', '/project_status'): (200, {
        'job_id': UC_KEY, 'nested': {'token': RK_KEY, UC_KEY: UC_KEY}})})
    b.configure('unreal_connector', UC_KEY)
    b.configure('resource_kit', RK_KEY)
    evidence = tmp_path / 'evidence.jsonl'
    policy = ht.parse_requires({'services': {'unreal_connector': {
        'operations': ['project_status'], 'projects': ['sample']}}})
    result = b.call('unreal_connector', 'project_status', {'project_id': 'sample'}, policy, evidence)
    for secret in [UC_KEY, RK_KEY]:
        assert secret not in json.dumps(result) + evidence.read_text()
    assert 'job_id' not in result['refs']


def test_known_indirect_job_and_recovery_reuse_are_scoped(tmp_path):
    b, log = broker(tmp_path, {('POST', '/run_validation'): (200, {'job_id': 'fixture-job'}),
                               ('POST', '/get_job'): (200, {'status': 'complete'})})
    b.configure('unreal_connector', UC_KEY)
    evidence = tmp_path / 'evidence.jsonl'
    policy = ht.parse_requires({'services': {'unreal_connector': {
        'operations': ['run_validation', 'get_job'], 'projects': ['sample']}}})
    with pytest.raises(ht.HostToolError, match='identifier recorded'):
        b.call('unreal_connector', 'get_job', {'job_id': 'unknown-job'}, policy, evidence)
    args = {'project_id': 'sample'}
    assert b.call('unreal_connector', 'run_validation', args, policy, evidence)['ok']
    assert b.call('unreal_connector', 'run_validation', {**args, 'wait_seconds': 10}, policy, evidence)['replayed']
    assert b.call('unreal_connector', 'get_job', {'job_id': 'fixture-job'}, policy, evidence)['ok']
    assert [entry['path'] for entry in log if isinstance(entry, dict)] == ['/run_validation', '/get_job']


def test_ambiguous_mutation_is_never_automatically_resubmitted(tmp_path):
    b, log = broker(tmp_path, fail=TimeoutError())
    b.configure('resource_kit', RK_KEY)
    evidence = tmp_path / 'evidence.jsonl'
    policy = ht.parse_requires({'services': {'resource_kit': {'operations': ['update_library']}}})
    with pytest.raises(ht.HostToolError, match='not reachable'):
        b.call('resource_kit', 'update_library', {}, policy, evidence)
    with pytest.raises(ht.HostToolError, match='may have started'):
        b.call('resource_kit', 'update_library', {}, policy, evidence)
    assert len(log) == 1


def test_simultaneous_duplicate_requests_start_one_operation(tmp_path):
    b, log = broker(tmp_path, {('POST', '/tools/library/update'): (200, {'operation_id': 'sample-job'})})
    b.configure('resource_kit', RK_KEY)
    policy = ht.parse_requires({'services': {'resource_kit': {'operations': ['update_library']}}})
    evidence = tmp_path / 'receipts.jsonl'
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(b.call, 'resource_kit', 'update_library', {}, policy, evidence) for _ in range(2)]
        replies = [job.result() for job in jobs]
    assert all(reply['ok'] for reply in replies)
    assert sum(bool(reply.get('replayed')) for reply in replies) == 1
    assert sum(isinstance(row, dict) for row in log) == 1


def test_resource_kit_only_exclusivity(tmp_path):
    b, _ = broker(tmp_path, {
        ('GET', '/tools/whoami'): (200, {'service': 'resource_kit', 'version': '0.4.2', 'scopes': ['discover', 'unreal']}),
        ('GET', '/tools/unreal/status'): (200, {'editor': {'running': True}}),
    })
    b.configure('resource_kit', RK_KEY)
    required = ht.parse_requires({'services': {'resource_kit': {}}, 'exclusive_unreal': True})
    result = b.preflight(required)
    assert not result['ok'] and any(row['check'] == 'resource_kit.unreal_idle' for row in result['checks'])
