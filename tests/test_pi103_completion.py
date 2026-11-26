"""Validate the checkpoint completion ordering without a model or SDK."""
import asyncio
from types import SimpleNamespace

import pytest

import executor as ex
import handover as ho


@pytest.fixture
def completion(tmp_path, monkeypatch):
    engine = ex.CartridgeExecutor.__new__(ex.CartridgeExecutor)
    engine.protocol = {'project_checkpoint': {'steps': ['step']}, 'recovery': {'io_retries': 0}}
    engine.config, engine.meta = {}, {'document': {}}
    engine.run_base, engine.run_id = tmp_path, 'run-example'
    engine.layout = SimpleNamespace(attempt_dir=lambda *args: tmp_path)
    source = {'bytes': 12, 'sha256': '01' * 32}
    attempt = {'attempt_id': 'ef' * 16, 'attempt_number': 1, 'session_id': 'session',
               'status': 'interrupted', 'phase': 'committing', 'finish': 'stop',
               'outputs': [], 'checkpoint_request': {'step': 'step', 'attempt_id': 'ef' * 16},
               'checkpoint_completion': {'text': '{"status":"DONE"}', 'source': source}}
    step = {'status': 'interrupted', 'attempts': [attempt]}
    engine.ledger = {'cartridges': {'step': step}}
    events = []
    engine.save = lambda: events.append('ledger')
    engine.stop_boundary = lambda: None
    engine.finish = lambda *args: events.append('finish')

    def persist_handover(*args):
        events.append('handover')
        return source

    def persist_checkpoint(receipt, result_sha256):
        assert result_sha256 == source['sha256']
        events.append('checkpoint')
        return {'committed': receipt['step']}

    monkeypatch.setattr(ho, 'collect_outputs', lambda *args: ([], []))
    monkeypatch.setattr(ho, 'read_result_file', lambda *args: ({'payload': {}}, source))
    monkeypatch.setattr(ho, 'validate_envelope', lambda *args: [])
    monkeypatch.setattr(ho, 'commit_attempt_result', persist_handover)
    monkeypatch.setattr(ho, 'committed_payload', lambda *args: events.append('handover verified'))
    monkeypatch.setattr(ex, 'checkpoint_book', lambda *args: SimpleNamespace(commit=persist_checkpoint))
    return engine, attempt, events


def test_resumes_validated_attempt_before_dispatch(completion):
    engine, attempt, events = completion
    value = asyncio.run(engine.run_cartridge('step', 'Prompt is already completed', 'model'))
    assert value == '{"status":"DONE"}'
    assert engine.ledger['cartridges']['step']['attempts'] == [attempt]
    assert attempt['status'] == 'done'
    assert events == ['handover', 'handover verified', 'checkpoint', 'ledger', 'finish']


@pytest.mark.parametrize('transition', ['bad handover', 'missing output', 'wrong result', 'truncated'])
def test_recovery_refuses_invalid_completion(completion, monkeypatch, transition):
    engine, attempt, events = completion

    def invalid(*args):
        raise ho.ResultFailure('RESULT_SCHEMA_INVALID', 'Invalid saved handover')

    if transition == 'bad handover':
        monkeypatch.setattr(ho, 'validate_envelope', invalid)
    elif transition == 'missing output':
        monkeypatch.setattr(ho, 'collect_outputs', lambda *args: ([], ['file.txt']))
    elif transition == 'wrong result':
        monkeypatch.setattr(ho, 'read_result_file', lambda *args: ({}, {'sha256': 'ab' * 32}))
    else:
        attempt['finish'] = 'length'
    with pytest.raises(ex.CartridgeFailed):
        asyncio.run(engine.run_cartridge('step', 'Earlier prompt', 'model'))
    assert events == []
    assert attempt['status'] != 'done'
