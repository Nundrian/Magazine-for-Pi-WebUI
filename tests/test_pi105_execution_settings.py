import copy
import asyncio
import json
from types import SimpleNamespace

import pytest

from model_selection import select_thinking
from ui_support import SupportError, validate_plan
from pi_dispatch import PiDispatch


def test_native_compaction_lifecycle_reaches_the_attempt_observer(tmp_path):
    async def check():
        dispatch = PiDispatch({}, tmp_path / 'sessions')
        stream = asyncio.StreamReader()
        events = [{'event': 'compaction', 'phase': 'started', 'reason': 'overflow', 'willRetry': False},
                  {'event': 'compaction', 'phase': 'completed', 'reason': 'overflow', 'willRetry': True}]
        for event in events + [{'event': 'result', 'ok': True, 'result': {'text': 'DONE'}}]:
            stream.feed_data((json.dumps(event) + '\n').encode())
        stream.feed_eof()
        dispatch.process = SimpleNamespace(stdout=stream)
        observed = []
        dispatch.on_event = observed.append
        assert await dispatch._read() == {'text': 'DONE'}
        assert observed == events
    asyncio.run(check())


MODEL = 'ollama/org/reasoning:Q6_K'
PLAIN = 'ollama/org/plain:Q8_0'


@pytest.fixture
def catalogue():
    native = {'sdk_version': '1.5.2', 'context_window': 8192, 'max_output': 2048,
              'compaction': {'enabled': True, 'reserveTokens': 1024, 'keepRecentTokens': 256},
              'inherited_thinking_level': 'medium', 'inheritance_source': 'pi_model',
              'thinking_level_map': {'low': 'low'}, 'thinking_levels': ['off', 'low', 'medium', 'high']}
    return {MODEL: native, PLAIN: {**native, 'thinking_levels': ['off'], 'inherited_thinking_level': 'off'}}


def test_reasoning_precedence_and_effective_settings(catalogue):
    data = {'default_thinking_level': 'low', 'cartridge_thinking_levels': {'b': 'inherit', 'd': 'high'}}
    assignments = {'a': MODEL, 'b': MODEL, 'c': MODEL, 'd': MODEL}
    settings = select_thinking(data, assignments, catalogue)
    assert [item['effective_thinking_level'] for item in settings.values()] == ['low', 'medium', 'low', 'high']
    assert settings['a']['provider_mapping'] == 'low'
    assert settings['b']['reasoning_scope'] == 'cartridge'
    assert settings['b']['reasoning_source'] == 'pi_model'
    assert settings['a']['compaction']['reserveTokens'] == 1024
    assert select_thinking({}, {'x': PLAIN}, catalogue)['x']['effective_thinking_level'] == 'off'


def test_explicit_nonreasoning_conflict_identifies_each_cartridge(catalogue):
    with pytest.raises(ValueError, match='a .*plain.* medium.*b .*plain.* medium'):
        select_thinking({'default_thinking_level': 'medium'}, {'a': PLAIN, 'b': PLAIN}, catalogue)


@pytest.mark.parametrize('extra', [
    {'cartridge_thinking_levels': {'unknown': 'low'}}, {'default_thinking_level': 'invalid'},
    {'default_thinking_level': None}, {'default_thinking_level': []},
    {'cartridge_thinking_levels': {'a': None}}, {'compaction': {'enabled': False}},
])
def test_invalid_schema_11_fields_fail_closed(extra, catalogue):
    plan = {'schema_version': '1.1', 'workflow_name': 'Fixture', 'cartridge_ids': ['a'],
            'default_model': MODEL, **extra}
    with pytest.raises(SupportError):
        validate_plan(plan, 'Fixture', ['a'], catalogue)


def test_legacy_json_leaves_reasoning_unspecified(catalogue):
    original = {'schema_version': '1.0', 'workflow_name': 'Fixture', 'cartridge_ids': ['a'], 'default_model': MODEL}
    plan = validate_plan(copy.deepcopy(original), 'Fixture', ['a'], catalogue)
    assert plan['cartridge_models'] == {'a': MODEL}
    assert 'default_thinking_level' not in plan and 'cartridge_thinking_levels' not in plan
    with pytest.raises(SupportError, match='Unknown model-plan field'):
        validate_plan({**original, 'default_thinking_level': 'off'}, 'Fixture', ['a'], catalogue)
