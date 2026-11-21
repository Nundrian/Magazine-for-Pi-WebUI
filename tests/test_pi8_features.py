"""Candidate UI/service acceptance using synthetic retained data, never an LLM.

The fixtures use the real Service and storage layout. They do not contact a
provider, run worker inference, or point at the owner's live data.
"""
import asyncio
import copy
import json
import os
import uuid
from pathlib import Path

import pytest

import ledger as lg
from service import Service
from ui_support import validate_plan
from pi6_fakes import project_zip


AVAILABLE = {'fixture/model-a', 'fixture/model-b'}


def plan(**extra):
    return {'schema_version': '1.0', 'workflow_name': 'Two steps',
            'cartridge_ids': ['a', 'b'],
            'cartridge_models': {'a': 'fixture/model-a', 'b': 'fixture/model-b'},
            'rationale': {'a': 'Bounded coding.', 'b': 'Independent check.'}, **extra}


def validate(value):
    return validate_plan(value, 'Two steps', ['a', 'b'], AVAILABLE)


def test_model_plan_resolves_exact_provider_ids_without_mutating_input():
    source = plan()
    saved = copy.deepcopy(source)
    result = validate(source)
    assert result['cartridge_models'] == source['cartridge_models']
    assert result['rationale'] == source['rationale']
    assert source == saved


def test_model_plan_default_covers_only_unassigned_cartridges():
    result = validate(plan(cartridge_models={'a': 'fixture/model-a'}, default_model='fixture/model-b'))
    assert result['cartridge_models'] == {'a': 'fixture/model-a', 'b': 'fixture/model-b'}


@pytest.mark.parametrize('changed', [
    {'schema_version': '2.0'},
    {'workflow_name': 'An old workflow'},
    {'cartridge_ids': ['b', 'a']},
    {'cartridge_ids': ['a', 'a']},
    {'cartridge_ids': ['a']},
    {'cartridge_models': {'a': 'fixture/model-a'}},
    {'cartridge_models': {'a': 'model-a', 'b': 'fixture/model-b'}},
    {'cartridge_models': {'a': 'fixture/model-a', 'b': 'ollama/installed-only'}},
    {'cartridge_models': {'a': 'fixture/model-a', 'b': 'fixture/model-b', 'foreign': 'fixture/model-a'}},
    {'default_model': 'ollama/installed-only'},
    {'cartridge_models': {'a': 'https://example.com/model', 'b': 'fixture/model-b'}},
    {'cartridge_models': {'a': '/projects/private', 'b': 'fixture/model-b'}},
    {'rationale': {'foreign': 'not this cartridge'}},
    {'rationale': {'a': {'execute': 'bad'}}},
    {'unexpected_path': '/etc/passwd'},
])
def test_model_plan_rejects_stale_partial_unavailable_and_untrusted_fields(changed):
    with pytest.raises((ValueError, TypeError)):
        validate(plan(**changed))


@pytest.mark.parametrize('raw', [
    '{"schema_version":"1.0","schema_version":"1.0"}',
    '{"schema_version":"1.0","cartridge_models":{"a":"fixture/model-a","a":"fixture/model-b"}}',
    '[]', 'null', '{"x":NaN}', '{broken', ' ' * (256 * 1024 + 1),
])
def test_model_plan_strict_json_and_size_bounds(raw):
    with pytest.raises((ValueError, TypeError)):
        validate(raw)


@pytest.fixture
def service(tmp_path):
    workspace = tmp_path / 'owner-project'
    workspace.mkdir()
    (workspace / 'must-keep.txt').write_text('owner project is not run storage')
    svc = Service(tmp_path / 'data', {'cwd': str(workspace), 'agentDir': str(tmp_path / 'agent'),
                                    'allowedTools': ['bash'], 'extensionPaths': []})
    async def catalogue():
        return {'models': [{'id': m, 'name': m, 'provider': 'fixture'} for m in sorted(AVAILABLE)],
                'tools': [{'id': 'bash', 'name': 'bash'}], 'piVersion': 'fixture'}
    svc.catalogue = catalogue
    yield svc
    asyncio.run(svc.close())


def call(svc, action, **data):
    return asyncio.run(svc.handle({'id': uuid.uuid4().hex, 'action': action, 'data': data}))


def make_run(svc, name='Project one', count=2, status='COMPLETED'):
    ids = [f'c{i:03}' for i in range(1, count + 1)]
    doc = {'schema_version': '1.0', 'cartridges': [
        {'id': cid, 'job_type': 'work', 'prompt': f'Fixture task {cid}'} for cid in ids]}
    result = call(svc, 'prepare', magazine=json.dumps(doc), model_map={'work': 'fixture/model-a'},
                  tool_ids=['bash'], handover_mode='result_file_v1')
    rid = result['run_id']
    base = svc.root / 'runs/local' / rid
    meta = json.loads((base / 'meta.json').read_text())
    meta.update(name=name, project_id=name.lower().replace(' ', '-'))
    (base / 'meta.json').write_text(json.dumps(meta))
    state = {'done': ids if status == 'COMPLETED' else [],
             'todo': [] if status == 'COMPLETED' else ids, 'current': None,
             'status': status, 'revision': 1}
    (base / 'run/state.json').write_text(json.dumps(state))
    return rid, base, ids


def add_attempt(base, rid, cid, *, number=1, status='done', text='Visible answer', missing=False, trailing=b''):
    ledger = lg.load(base) or lg.new(rid, [c['id'] for c in json.loads((base/'magazine.json').read_text())['cartridges']], 'result_file_v1')
    aid, sid = uuid.uuid4().hex, str(uuid.uuid4())
    path = base / 'sessions' / (sid + '.jsonl')
    path.parent.mkdir(exist_ok=True)
    attempt = {'attempt_id': aid, 'attempt_number': number, 'kind': 'work', 'status': status,
               'session_id': sid, 'session_file': path.relative_to(base).as_posix(),
               'model': 'fixture/model-a', 'started': f'2026-01-01T00:{number:02}:00+00:00',
               'finished': None if status == 'running' else f'2026-01-01T00:{number:02}:01+00:00'}
    ledger['cartridges'][cid]['attempts'].append(attempt)
    ledger['cartridges'][cid]['status'] = status
    lg.save(base, ledger)
    if not missing:
        records = [
            {'type': 'session', 'id': sid},
            {'type': 'message', 'id': aid+'u', 'timestamp': '2026-01-01T00:00:00Z',
             'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'User task <script>unsafe()</script>'}]}},
            {'type': 'message', 'id': aid+'a', 'timestamp': '2026-01-01T00:00:01Z',
             'message': {'role': 'assistant', 'model': 'model-a', 'provider': 'fixture', 'content': [
                 {'type': 'thinking', 'thinking': 'PRIVATE_THOUGHT_MUST_NOT_APPEAR'},
                 {'type': 'text', 'text': text},
                 {'type': 'toolCall', 'id': 'tool-'+aid, 'name': 'bash', 'arguments': {'command': 'printf fixture', 'api_key': 'PRIVATE_KEY'}}]}},
            {'type': 'message', 'id': aid+'t', 'timestamp': '2026-01-01T00:00:02Z',
             'message': {'role': 'toolResult', 'toolCallId': 'tool-'+aid, 'toolName': 'bash', 'isError': True,
                         'content': [{'type': 'text', 'text': 'Tool failed safely; Authorization: Bearer PRIVATE_TOKEN'}]}},
        ]
        path.write_bytes(('\n'.join(json.dumps(r) for r in records)+'\n').encode()+trailing)
    return attempt, path


def chats(svc):
    out, offset = [], 0
    while True:
        page = call(svc, 'chats', offset=offset, limit=2)
        assert 'Magazine' in page['scope']
        out.extend(page['chats'])
        offset = page.get('next_offset')
        if offset is None:
            return out


def chat_for(svc, rid, aid):
    return next(c for c in chats(svc) if c['run_id'] == rid and c['attempt_id'] == aid)


def test_history_lists_all_projects_retries_failed_missing_and_legacy(service):
    r1, b1, ids1 = make_run(service, 'First project')
    a1, _ = add_attempt(b1, r1, ids1[0], status='failed')
    a2, _ = add_attempt(b1, r1, ids1[0], number=2)
    a3, _ = add_attempt(b1, r1, ids1[1], missing=True)
    r2, b2, ids2 = make_run(service, 'Second project')
    a4, _ = add_attempt(b2, r2, ids2[0])
    # A retained pre-ledger session remains discoverable without inventing an attempt.
    r3, b3, _ = make_run(service, 'Legacy project')
    legacy = b3/'sessions/legacy.jsonl'; legacy.parent.mkdir()
    legacy.write_text(json.dumps({'type':'session','id':'legacy-sid'})+'\n'+json.dumps(
        {'type':'message','id':'old','message':{'role':'user','content':'Legacy user message'}})+'\n')
    values = chats(service)
    assert {v['run_id'] for v in values} == {r1, r2, r3}
    assert {v['attempt_id'] for v in values if v['run_id'] != r3} == {a1['attempt_id'],a2['attempt_id'],a3['attempt_id'],a4['attempt_id']}
    missing = call(service, 'chat', chat_id=chat_for(service,r1,a3['attempt_id'])['chat_id'])
    assert missing['state'] in ('missing','unavailable') and not missing['messages']
    legacy_chat = next(v for v in values if v['run_id'] == r3)
    assert 'Legacy user message' in json.dumps(call(service, 'chat', chat_id=legacy_chat['chat_id']))
    # Re-instantiating the service reads retained history rather than a browser cache.
    reopened = Service(service.root, service.config.copy())
    try:
        assert {c['chat_id'] for c in chats(reopened)} == {c['chat_id'] for c in values}
    finally:
        asyncio.run(reopened.close())


def test_chat_visible_messages_tool_errors_redaction_and_no_private_thinking(service):
    rid, base, ids = make_run(service)
    attempt, path = add_attempt(base,rid,ids[0],text='Before <think>HIDDEN_INLINE_THOUGHT</think> after')
    before = path.read_bytes()
    c = chat_for(service,rid,attempt['attempt_id'])
    result = call(service,'chat',chat_id=c['chat_id'])
    rendered = json.dumps(result)
    assert 'User task' in rendered and 'Before' in rendered and 'after' in rendered
    assert 'Tool failed safely' in rendered and 'bash' in rendered
    assert 'PRIVATE_THOUGHT' not in rendered and 'HIDDEN_INLINE_THOUGHT' not in rendered
    assert 'PRIVATE_KEY' not in rendered and 'PRIVATE_TOKEN' not in rendered
    assert path.read_bytes() == before  # displaying a conversation never resumes or edits it


def test_live_chat_reads_only_complete_lines_then_appends_without_duplication(service, monkeypatch):
    rid,base,ids = make_run(service,status='READY')
    attempt,path = add_attempt(base,rid,ids[0],status='running',trailing=b'{"type":"message","id":"next",')
    monkeypatch.setattr(service,'has_task',lambda owner, run_id: run_id==rid)
    c=chat_for(service,rid,attempt['attempt_id'])
    first=call(service,'chat',chat_id=c['chat_id'])
    assert first['live'] and 'next' not in {m.get('id') for m in first['messages']}
    with path.open('ab') as f:
        f.write(b'"message":{"role":"assistant","content":[{"type":"text","text":"Appended live message"}]}}\n')
    second=call(service,'chat',chat_id=c['chat_id'],offset=first['next_offset'],generation=first['generation'])
    assert len(second['messages']) == 1 and 'Appended live message' in json.dumps(second['messages'])
    third=call(service,'chat',chat_id=c['chat_id'],offset=second['next_offset'],generation=second['generation'])
    assert third['messages'] == []
    # The writer stays free to append while the view is open; switching chats is read-only.
    rid2,base2,ids2=make_run(service,'Other project')
    old,_=add_attempt(base2,rid2,ids2[0])
    call(service,'chat',chat_id=chat_for(service,rid2,old['attempt_id'])['chat_id'])
    with path.open('ab') as f:
        f.write((json.dumps({'type':'message','id':'later','message':{'role':'assistant','content':'Still progressing'}})+'\n').encode())
    fourth=call(service,'chat',chat_id=c['chat_id'],offset=third['next_offset'],generation=third['generation'])
    assert 'Still progressing' in json.dumps(fourth['messages'])


def test_chat_rotation_resets_cursor_and_bad_cursor_is_refused(service):
    rid,base,ids=make_run(service)
    attempt,path=add_attempt(base,rid,ids[0])
    c=chat_for(service,rid,attempt['attempt_id']); first=call(service,'chat',chat_id=c['chat_id'])
    replacement=path.with_suffix('.new')
    replacement.write_text(json.dumps({'type':'session','id':attempt['session_id']})+'\n'+json.dumps(
        {'type':'message','id':'replacement','message':{'role':'assistant','content':'After rotation'}})+'\n')
    replacement.replace(path)
    updated=call(service,'chat',chat_id=c['chat_id'],offset=first['next_offset'],generation=first['generation'])
    assert updated['reset'] and 'After rotation' in json.dumps(updated['messages'])
    with pytest.raises((ValueError,TypeError)):
        call(service,'chat',chat_id=c['chat_id'],offset=-1)


def test_long_conversation_paging_is_complete_ordered_and_without_duplicates(service):
    rid,base,ids=make_run(service)
    attempt,path=add_attempt(base,rid,ids[0])
    rows=[{'type':'session','id':attempt['session_id']}]
    rows += [{'type':'message','id':f'm{i:04}', 'message':{'role':'assistant','content':[{'type':'text','text':f'Line {i}: '+('x'*4096)}]}} for i in range(250)]
    path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    c=chat_for(service,rid,attempt['attempt_id']); offset=0; generation=None; seen=[]; pages=0
    while True:
        result=call(service,'chat',chat_id=c['chat_id'],offset=offset,**({'generation':generation} if generation else {}))
        pages+=1
        seen.extend(m['id'] for m in result['messages'])
        assert result['next_offset']>=offset
        if not result['more']: break
        assert result['next_offset']>offset and pages<20
        offset,generation=result['next_offset'],result['generation']
    assert pages>1 and seen==[f'm{i:04}' for i in range(250)]


def test_same_inode_truncate_and_regrowth_resets_conversation_cursor(service):
    rid,base,ids=make_run(service)
    attempt,path=add_attempt(base,rid,ids[0])
    entry=chat_for(service,rid,attempt['attempt_id'])
    first=call(service,'chat',chat_id=entry['chat_id']);inode=path.stat().st_ino
    replacement=[{'type':'session','id':attempt['session_id']},
                 {'type':'message','id':'rewritten','message':{'role':'assistant','content':'After same-inode rewrite '+('x'*8192)}}]
    path.write_text('\n'.join(json.dumps(r) for r in replacement)+'\n')
    assert path.stat().st_ino==inode and path.stat().st_size>=first['next_offset']
    fresh=call(service,'chat',chat_id=entry['chat_id'],offset=first['next_offset'],generation=first['generation'])
    assert fresh['reset'] and [m['id'] for m in fresh['messages']]==['rewritten']
    assert 'After same-inode rewrite' in json.dumps(fresh['messages'])


@pytest.mark.parametrize('kind',['traversal','symlink','hardlink','wrong_session'])
def test_chat_never_reads_outside_managed_store_or_mismatched_identity(service,tmp_path,kind):
    rid,base,ids=make_run(service)
    attempt,path=add_attempt(base,rid,ids[0])
    outside=tmp_path/'outside-secret'
    outside_text=json.dumps({'type':'session','id':attempt['session_id']})+'\n'+json.dumps(
        {'type':'message','id':'outside','message':{'role':'assistant','content':'OUTSIDE_PRIVATE_CONTENT'}})+'\n'
    outside.write_text(outside_text)
    ledger=lg.load(base)
    if kind=='traversal':
        ledger['cartridges'][ids[0]]['attempts'][0]['session_file']='../../../../outside-secret'
        lg.save(base,ledger)
    elif kind in ('symlink','hardlink'):
        path.unlink()
        path.symlink_to(outside) if kind=='symlink' else os.link(outside,path)
    else:
        path.write_text(json.dumps({'type':'session','id':'some-other-session'})+'\n'+json.dumps(
            {'type':'message','message':{'role':'assistant','content':'MISMATCHED_CONTENT'}})+'\n')
    try:
        entries=chats(service)
        entry=next((c for c in entries if c.get('attempt_id')==attempt['attempt_id']),None)
        result=call(service,'chat',chat_id=entry['chat_id']) if entry else entries
    except (ValueError,OSError):
        result={}
    assert 'OUTSIDE_PRIVATE_CONTENT' not in json.dumps(result)
    assert 'MISMATCHED_CONTENT' not in json.dumps(result)
    assert outside.read_text()==outside_text


def test_chat_rejects_browser_supplied_owner_or_path(service):
    rid,base,ids=make_run(service)
    attempt,_=add_attempt(base,rid,ids[0]); c=chat_for(service,rid,attempt['attempt_id'])
    for field,value in [('owner','a'*64),('path','/etc/passwd'),('session_file','../../private')]:
        with pytest.raises((ValueError,TypeError)):
            call(service,'chat',chat_id=c['chat_id'],**{field:value})


def test_empty_and_not_yet_created_attempts_are_clear_without_fabricated_chat(service):
    rid,base,ids=make_run(service,status='READY')
    attempt,path=add_attempt(base,rid,ids[0])
    path.write_bytes(b'')
    entry=chat_for(service,rid,attempt['attempt_id'])
    result=call(service,'chat',chat_id=entry['chat_id'])
    assert result['messages']==[] and result['state'] in ('empty','not_yet_created','unavailable','waiting_for_complete_record')
    # No session metadata exists for the second cartridge; do not manufacture a session.
    assert not any(c.get('cartridge_id')==ids[1] for c in chats(service))


@pytest.mark.parametrize('data',[{'offset':-1},{'offset':True},{'limit':0},{'limit':-1},{'limit':True},{'limit':100000},{'offset':'../../etc'}])
def test_chat_list_pagination_inputs_are_bounded(service,data):
    with pytest.raises((ValueError,TypeError)):
        call(service,'chats',**data)


def test_delete_requires_confirmation_and_never_touches_actual_project(service):
    rid,base,_=make_run(service,status='READY')
    marker=Path(service.config['cwd'])/'must-keep.txt'
    before=marker.read_bytes()
    with pytest.raises(ValueError):
        call(service,'delete_run',run_id=rid,confirmation='wrong')
    assert base.exists()
    preview=call(service,'delete_preview',run_id=rid)
    assert marker.as_posix() not in json.dumps(preview)
    result=call(service,'delete_run',run_id=rid,confirmation=rid)
    assert not base.exists()
    assert marker.read_bytes()==before
    assert result


@pytest.mark.parametrize('guard',['active','queued','running_ledger','stop_unconfirmed'])
def test_delete_rechecks_activity_and_queue_guards(service,monkeypatch,guard):
    rid,base,ids=make_run(service,status='READY')
    if guard=='active': monkeypatch.setattr(service,'has_task',lambda owner,run_id: run_id==rid)
    elif guard=='queued': service.scheduler.admit(rid,'local',0)
    elif guard=='running_ledger': add_attempt(base,rid,ids[0],status='running')
    else:
        original=__import__('beta5_helpers')._status_payload
        def status(owner,run_id):
            value=original(owner,run_id)
            if run_id==rid: value['control']={'request':'STOP','termination_status':'REQUESTED'}
            return value
        monkeypatch.setattr(__import__('beta5_helpers'),'_status_payload',status)
    with pytest.raises(ValueError): call(service,'delete_run',run_id=rid,confirmation=rid)
    assert base.exists()
    assert (Path(service.config['cwd'])/'must-keep.txt').exists()


def test_delete_failure_keeps_selection_and_run(service,monkeypatch):
    rid,base,_=make_run(service,status='READY')
    call(service,'view_state',set={'run_id':rid})
    original=os.rename
    def fail_rename(*args,**kwargs):
        raise OSError('injected rename failure')
    monkeypatch.setattr(os,'rename',fail_rename)
    with pytest.raises((ValueError,OSError)):
        call(service,'delete_run',run_id=rid,confirmation=rid)
    monkeypatch.setattr(os,'rename',original)
    assert base.exists()
    assert call(service,'view_state')['run_id']==rid


def test_delete_refuses_when_run_becomes_queued_after_preview(service):
    rid,base,_=make_run(service,status='READY')
    call(service,'delete_preview',run_id=rid)
    service.scheduler.admit(rid,'local',0)
    before=service.scheduler.journal.read_bytes()
    with pytest.raises(ValueError):
        call(service,'delete_run',run_id=rid,confirmation=rid)
    assert base.exists() and service.scheduler.journal.read_bytes()==before


def test_delete_rollback_when_second_managed_move_fails(service,monkeypatch):
    rid,base,_=make_run(service,status='READY')
    owned=Path(service.config['cwd'])/'magazine-runs'/rid
    owned.mkdir(parents=True); (owned/'retained.txt').write_text('workspace receipt')
    original=os.rename; calls=[]
    def fail_second(source,destination,*args,**kwargs):
        calls.append((source,destination))
        if len(calls)==2: raise OSError('injected second-move failure')
        return original(source,destination,*args,**kwargs)
    monkeypatch.setattr(os,'rename',fail_second)
    with pytest.raises((ValueError,OSError)):
        call(service,'delete_run',run_id=rid,confirmation=rid)
    assert base.exists() and (owned/'retained.txt').read_text()=='workspace receipt'


def test_delete_refuses_symlinked_run_and_browser_owner(service,tmp_path):
    rid,base,_=make_run(service,status='READY')
    for extra in [{'owner':'b'*64},{'path':str(tmp_path)}]:
        with pytest.raises(ValueError): call(service,'delete_run',run_id=rid,confirmation=rid,**extra)
    # Replace a run directory with a link: it cannot turn deletion into a project delete.
    backup=base.with_name('retained-'+rid); base.rename(backup)
    base.symlink_to(Path(service.config['cwd']),target_is_directory=True)
    with pytest.raises((ValueError,OSError)): call(service,'delete_run',run_id=rid,confirmation=rid)
    assert (Path(service.config['cwd'])/'must-keep.txt').exists()


def test_deleted_run_and_owned_workspace_are_restorable_without_external_changes(service):
    rid,base,_=make_run(service,status='READY')
    owned=Path(service.config['cwd'])/'magazine-runs'/rid
    owned.mkdir(parents=True); (owned/'result.txt').write_text('verified task workspace')
    marker=Path(service.config['cwd'])/'must-keep.txt'; original=marker.read_bytes()
    deleted=call(service,'delete_run',run_id=rid,confirmation=rid)
    assert deleted['recoverable'] and not base.exists() and not owned.exists()
    retained=call(service,'trash')['items']
    assert any(i['trash_id']==deleted['trash_id'] and i['id']==rid for i in retained)
    result=call(service,'restore',trash_id=deleted['trash_id'])
    assert result['restored'] and base.exists() and (owned/'result.txt').read_text()=='verified task workspace'
    assert marker.read_bytes()==original and call(service,'status',run_id=rid)['display_status']=='READY'
    assert not any(i['trash_id']==deleted['trash_id'] for i in call(service,'trash')['items'])


def test_restore_refuses_to_overwrite_new_record_with_same_identity(service):
    rid,base,_=make_run(service,status='READY')
    deleted=call(service,'delete_run',run_id=rid,confirmation=rid)
    base.mkdir(); (base/'new-record').write_text('new content')
    with pytest.raises(ValueError): call(service,'restore',trash_id=deleted['trash_id'])
    assert (base/'new-record').read_text()=='new content'
    assert any(i['trash_id']==deleted['trash_id'] for i in call(service,'trash')['items'])


def import_two_step(svc, name, sidecar=None):
    files={'a.md':'Bounded first task','b.md':'Independent verification'}
    if sidecar is not None: files['model-assignments.json']=sidecar
    raw=project_zip('name: Two steps\ncartridges:\n  - {id: a, model: work, prompt_file: a.md}\n  - {id: b, model: work, prompt_file: b.md}\n',files)
    return call(svc,'import',file_name=name+'.zip',file_data_b64=raw)['project_id']


def test_valid_sidecar_survives_import_but_does_not_apply_itself(service):
    pid=import_two_step(service,'ValidSidecar',json.dumps(plan()))
    original=(service.root/'library'/pid/'model-assignments.json').read_bytes()
    preview=call(service,'preview',project_id=pid)
    assert preview['valid'] and preview['model_plan']['state']=='valid'
    assert preview['model_plan']['plan']['cartridge_models']==plan()['cartridge_models']
    assert [c['job_type'] for c in preview['document']['cartridges']]==['work','work']
    assert (service.root/'library'/pid/'model-assignments.json').read_bytes()==original
    assert call(service,'runs')['runs']==[]


@pytest.mark.parametrize('sidecar',['{broken',json.dumps(plan(workflow_name='stale')),json.dumps(plan(cartridge_models={'a':'fixture/model-a','b':'ollama/installed-only'}))])
def test_invalid_sidecar_warns_without_breaking_an_otherwise_valid_workflow(service,sidecar):
    pid=import_two_step(service,'BadSidecar'+uuid.uuid4().hex[:8],sidecar)
    preview=call(service,'preview',project_id=pid)
    assert preview['valid'] and preview['model_plan']['state']=='invalid'
    assert preview['model_plan'].get('error') and 'plan' not in preview['model_plan']
    assert call(service,'runs')['runs']==[]


def test_pasted_json_plan_validates_without_altering_prepared_runs(service):
    rid,base,_=make_run(service,status='READY')
    before={p.name:p.read_bytes() for p in base.iterdir() if p.is_file()}
    doc={'schema_version':'1.0','cartridges':[{'id':'a','job_type':'work','prompt':'A'},{'id':'b','job_type':'work','prompt':'B'}]}
    # Pasted core documents have the Service's documented name, not a guessed ZIP identity.
    name=service.parse_document(json.dumps(doc))[2]
    value=plan(workflow_name=name)
    checked=call(service,'model_plan',magazine=json.dumps(doc),plan=value)['plan']
    assert checked['cartridge_models']==value['cartridge_models']
    after={p.name:p.read_bytes() for p in base.iterdir() if p.is_file()}
    assert before==after


def test_planning_brief_has_real_tasks_live_ids_tools_and_sensitive_copy_warning(service):
    pid=import_two_step(service,'PlanningBrief')
    result=call(service,'planning_brief',project_id=pid)
    text=result['brief']; data=json.loads(text)
    assert data['cartridge_ids']==['a','b']
    assert {m['id'] for m in data['available_models']}==AVAILABLE
    assert [c['order'] for c in data['cartridges']]==[1,2]
    assert data['cartridges'][0]['prompt']=='Bounded first task'
    assert any(t['id']=='bash' for t in data['available_tools'])
    assert 'non-thinking' in text and 'not a reasoning model' in text
    assert 'failures' in text and 'not comparative' in text
    assert 'sensitive' in result['warning'].lower()


def test_unassociated_retained_session_beside_ledger_stays_in_history(service):
    rid,base,ids=make_run(service)
    attempt,_=add_attempt(base,rid,ids[0])
    extra=base/'sessions/orphan-old.jsonl'
    extra.write_text(json.dumps({'type':'session','id':'orphan-old'})+'\n'+json.dumps(
        {'type':'message','id':'orphan-message','message':{'role':'user','content':'Retained older conversation'}})+'\n')
    history=chats(service)
    assert any(c.get('attempt_id')==attempt['attempt_id'] for c in history)
    orphan=[c for c in history if c.get('state')=='legacy_unassociated' and c['run_id']==rid]
    assert len(orphan)==1
    assert 'Retained older conversation' in json.dumps(call(service,'chat',chat_id=orphan[0]['chat_id']))


def test_template_delete_preserves_run_snapshot_and_blocks_live_dependency(service,monkeypatch):
    pid=import_two_step(service,'DeleteTemplate')
    preview=call(service,'preview',project_id=pid)
    prepared=call(service,'prepare',magazine=json.dumps(preview['document']),project_id=pid,
                  default_model='fixture/model-a',tool_ids=[])
    rid=prepared['run_id']; base=service.root/'runs/local'/rid
    original=(base/'magazine.json').read_bytes()
    monkeypatch.setattr(service,'has_task',lambda owner,run_id: run_id==rid)
    with pytest.raises(ValueError): call(service,'delete_template',project_id=pid,confirmation=pid)
    assert (service.root/'library'/pid).is_dir() and (base/'magazine.json').read_bytes()==original
    monkeypatch.setattr(service,'has_task',lambda owner,run_id:False)
    deleted=call(service,'delete_template',project_id=pid,confirmation=pid)
    assert deleted['recoverable'] and not (service.root/'library'/pid).exists()
    assert (base/'magazine.json').read_bytes()==original
    restored=call(service,'restore',trash_id=deleted['trash_id'])
    assert restored['restored'] and (service.root/'library'/pid).is_dir()


def test_prepare_saves_resolved_plan_manual_override_and_preserves_past_run(service):
    old,oldbase,_=make_run(service,status='READY')
    original=(oldbase/'meta.json').read_bytes()
    pid=import_two_step(service,'PreparedPlan')
    preview=call(service,'preview',project_id=pid)
    requested=plan()
    result=call(service,'prepare',project_id=pid,magazine=json.dumps(preview['document']),
                cartridge_models={'a':'fixture/model-b','b':'fixture/model-b'},tool_ids=[],model_plan=requested)
    assert result['display_status']=='READY'
    assert result['cartridge_models']=={'a':'fixture/model-b','b':'fixture/model-b'}
    stored=json.loads((service.root/'runs/local'/result['run_id']/'meta.json').read_text())['model_plan']
    assert stored['cartridge_models']==requested['cartridge_models']
    assert stored['manual_overrides']=={'a':'fixture/model-b'}
    assert (oldbase/'meta.json').read_bytes()==original


def test_delete_during_start_catalogue_wait_cannot_resurrect_or_schedule_run(service, monkeypatch):
    rid, base, _ = make_run(service, status='READY')

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_catalogue():
            entered.set()
            await release.wait()
            return {'models': [{'id': 'fixture/model-a'}], 'tools': [{'id': 'bash'}]}

        monkeypatch.setattr(service, 'catalogue', delayed_catalogue)
        start = asyncio.create_task(service.handle({'action': 'start', 'data': {'run_id': rid}}))
        await asyncio.wait_for(entered.wait(), 2)
        removed = await service.handle({'action': 'delete_run', 'data': {'run_id': rid, 'confirmation': rid}})
        assert removed['deleted'] and not base.exists()
        release.set()
        with pytest.raises(ValueError):
            await start
        assert service.scheduler.entry(rid) is None
        assert not base.exists()

    asyncio.run(scenario())


@pytest.mark.parametrize('moved_parts', [[], ['record'], ['record', 'workspace']])
def test_interrupted_delete_intent_is_visible_and_restorable_after_restart(service, moved_parts):
    rid, base, _ = make_run(service, status='READY')
    workspace = Path(service.config['cwd']) / 'magazine-runs' / rid
    workspace.mkdir(parents=True)
    (workspace / 'retained.txt').write_text('work product')
    original = (base / 'magazine.json').read_bytes()
    token = uuid.uuid4().hex
    transaction = service.root / 'trash' / token
    transaction.mkdir(parents=True)
    intent = {'kind': 'run', 'id': rid, 'owner': 'local', 'created_at': 123,
              'parts': ['record', 'workspace'], 'state': 'interrupted'}
    (transaction / 'intent.json').write_text(json.dumps(intent))
    # Simulate a process stopping between each pair of durable renames.
    for part, source in [('record', base), ('workspace', workspace)]:
        if part in moved_parts:
            source.rename(transaction / part)
    reopened = Service(service.root, dict(service.config))
    try:
        assert any(t['trash_id'] == token and t['state'] == 'interrupted' for t in call(reopened, 'trash')['items'])
        assert call(reopened, 'restore', trash_id=token)['restored']
        assert (base / 'magazine.json').read_bytes() == original
        assert (workspace / 'retained.txt').read_text() == 'work product'
        assert not call(reopened, 'trash')['items']
    finally:
        asyncio.run(reopened.close())
