import json
import os
from pathlib import Path

import pytest

import run_export as ex


@pytest.fixture
def run(tmp_path):
    (tmp_path / 'run/results').mkdir(parents=True)
    (tmp_path / 'sessions').mkdir()
    values = {'magazine.json': {'schema_version': '1.0', 'cartridges': [
        {'id': 'c1', 'job_type': 'primary', 'prompt': 'Grüße\r\n第二'},
        {'id': 'c2', 'job_type': 'primary', 'prompt': 'Second'}]},
        'meta.json': {'created_at': '2026-01-01T00:00:00Z', 'model_map': {'primary': 'provider/model'}},
        'run/state.json': {'done': ['c1'], 'current': 'c2', 'todo': [], 'status': 'HALTED', 'revision': 1},
        'run/execution.json': {'attempt': 2, 'halt_reason': 'test failure'},
        'run/control.json': {'request': 'STOP', 'termination_status': 'CONFIRMED'}}
    for name, value in values.items():
        (tmp_path / name).write_text(json.dumps(value), encoding='utf-8')
    (tmp_path / 'run/results/c1.txt').write_bytes('Grüße\r\n第二'.encode())
    for index, prompt in enumerate(('Grüße\r\n第二', 'Second', 'Second')):
        records = [
            {'type': 'session', 'id': f's{index}', 'timestamp': f'2026-01-01T00:00:0{index}Z'},
            {'type': 'model_change', 'provider': 'provider', 'modelId': 'model'},
            {'type': 'message', 'id': 'u', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': prompt}]}},
            {'type': 'message', 'message': {'role': 'assistant', 'content': [{'type': 'toolCall', 'name': 'read', 'arguments': {'path': 'file'}}]}},
            {'type': 'message', 'message': {'role': 'toolResult', 'content': [{'type': 'text', 'text': 'FULL TOOL RESULT ' + 'x' * 10000}]}},
            {'type': 'message', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'FULL RESPONSE'}], 'errorMessage': 'error' if index == 1 else None}}]
        # Reverse filenames to prove timestamp, not directory order.
        (tmp_path / f'sessions/{9-index}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')
    return tmp_path


def rows(run):
    return [json.loads(line) for line in ex.export_run(run, 'abcdef123456').decode('utf-8').splitlines()]


def test_full_source_lines_all_attempts_and_snapshots(run):
    result = rows(run)
    assert [r['sequence'] for r in result] == list(range(1, len(result)+1))
    assert {r['run_id'] for r in result} == {'abcdef123456'}
    events = [r for r in result if r['kind'] == 'session_record']
    assert len(events) == 18
    assert [r['session_id'] for r in events[::6]] == ['s0', 's1', 's2']
    assert [r['cartridge_id'] for r in events[::6]] == ['c1', 'c2', 'c2']
    for file in (run / 'sessions').glob('*.jsonl'):
        original = [json.loads(line) for line in file.read_text().splitlines()]
        selected = [r for r in events if r['source'] == 'sessions/' + file.name]
        assert [r['record'] for r in selected] == original
        assert [r['source_line'] for r in selected] == list(range(1, 7))
    assert next(r['record']['text'] for r in result if r['kind'] == 'result_snapshot') == 'Grüße\r\n第二'
    assert any(r['source'] == 'run/control.json' for r in result)
    assert result[-1]['record']['status'] == 'HALTED'


@pytest.mark.parametrize('status', ['READY', 'RUNNING', 'COMPLETED', 'HALTED'])
def test_every_state_including_no_sessions(run, status):
    state = run / 'run/state.json'
    state.write_text(json.dumps({'status': status, 'done': []}))
    for p in (run / 'sessions').iterdir(): p.unlink()
    (run / 'sessions').rmdir()
    assert rows(run)[-1]['record']['status'] == status


def test_partial_tail_and_corruption(run):
    p = run / 'sessions/9.jsonl'
    with p.open('ab') as f: f.write(b'{"unfinished":')
    result = rows(run)
    assert any(r['kind'] == 'retention_notice' for r in result)
    with p.open('ab') as f: f.write(b'\n')
    with pytest.raises(ex.ExportError): rows(run)


@pytest.mark.parametrize('link', ['symlink', 'hardlink', 'directory'])
def test_unsafe_source_refused(run, link):
    target = run / 'sessions/9.jsonl'
    target.unlink()
    other = run / 'private'; other.write_text('{"secret":"NEVER_EXPORT"}\n')
    if link == 'symlink': target.symlink_to(other)
    elif link == 'hardlink': os.link(other, target)
    else: target.mkdir()
    with pytest.raises(ex.ExportError): rows(run)


def test_duplicate_prompts_do_not_invent_cartridge_identity(run):
    path = run / 'magazine.json'
    value = json.loads(path.read_text()); value['cartridges'][1]['prompt'] = value['cartridges'][0]['prompt']
    path.write_text(json.dumps(value))
    first = next(r for r in rows(run) if r['kind'] == 'session_record')
    assert first['cartridge_id'] is None and first['cartridge_candidates'] == ['c1', 'c2']


def test_redaction_without_truncation():
    value = {'apiKey': 'HIDDEN1', 'headers': {'Authorization': 'Bearer HIDDEN2'},
        'text': 'password=HIDDEN3 Bearer HIDDEN4 {"api_key":"HIDDEN5"} OWNER_CAP Authorization: Basic HIDDEN6',
        'maxTokens': 42, 'prompt': 'Keep all unicode 第二\r\n' + 'x'*100000}
    cleaned = ex.redact(value, ('OWNER_CAP',))
    assert 'HIDDEN' not in json.dumps(cleaned) and 'OWNER_CAP' not in json.dumps(cleaned)
    assert cleaned['maxTokens'] == 42 and cleaned['prompt'] == value['prompt']


def test_missing_completed_result_refused(run):
    (run / 'run/results/c1.txt').unlink()
    with pytest.raises(ex.ExportError, match='result is missing'): rows(run)


def test_symlinked_session_directory_refused(run):
    for p in (run / 'sessions').iterdir(): p.unlink()
    (run / 'sessions').rmdir()
    (run / 'other').mkdir()
    (run / 'sessions').symlink_to(run / 'other', target_is_directory=True)
    with pytest.raises(ex.ExportError): rows(run)


def test_changed_snapshot_refused(run, monkeypatch):
    original = ex.read_file
    count = 0
    def changing(root, relative):
        nonlocal count
        data = original(root, relative)
        if relative == 'run/state.json':
            count += 1
            if count == 2: return b'{"status":"CHANGED"}'
        return data
    monkeypatch.setattr(ex, 'read_file', changing)
    with pytest.raises(ex.ExportError, match='changed'): rows(run)
