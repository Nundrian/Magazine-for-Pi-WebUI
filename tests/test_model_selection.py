import copy

import pytest

from model_selection import select_models


MODELS = {'provider/model-a:variant', 'other/model/b'}
DOC = {'schema_version': '1.0', 'cartridges': [
    {'id': 'one', 'job_type': 'primary', 'prompt': 'First'},
    {'id': 'two', 'job_type': 'user-defined-legacy-alias', 'prompt': 'Second'}]}


@pytest.mark.parametrize('selected', sorted(MODELS))
def test_any_host_id_default_and_no_document_mutation(selected):
    before = copy.deepcopy(DOC)
    doc, mapping = select_models(DOC, {'default_model': selected}, MODELS)
    assert [mapping[c['job_type']] for c in doc['cartridges']] == [selected, selected]
    assert DOC == before


def test_alias_mapping_over_default_and_cartridge_override_over_alias():
    a, b = sorted(MODELS)
    doc, mapping = select_models(DOC, {'default_model': a,
        'model_map': {'primary': b, 'custom': a}, 'cartridge_models': {'one': 'custom'}}, MODELS)
    assert [mapping[c['job_type']] for c in doc['cartridges']] == [a, a]
    doc, mapping = select_models(DOC, {'default_model': a, 'model_map': {'primary': b}}, MODELS)
    assert [mapping[c['job_type']] for c in doc['cartridges']] == [b, a]


def test_legacy_map_and_document_pass_unchanged():
    mapping = {'primary': 'other/model/b', 'user-defined-legacy-alias': 'provider/model-a:variant'}
    assert select_models(DOC, {'model_map': mapping}, MODELS) == (DOC, mapping)


def test_explicit_host_model_in_cartridge_precedes_default():
    doc = copy.deepcopy(DOC)
    doc['cartridges'][0]['job_type'] = 'other/model/b'
    prepared, mapping = select_models(doc, {'default_model': 'provider/model-a:variant'}, MODELS)
    assert [mapping[c['job_type']] for c in prepared['cartridges']] == ['other/model/b', 'provider/model-a:variant']


def test_concrete_override_not_shadowed_by_alias():
    a, b = sorted(MODELS)
    prepared, mapping = select_models(DOC, {'default_model': a,
        'model_map': {b: a}, 'cartridge_models': {'one': b}}, MODELS)
    assert mapping[prepared['cartridges'][0]['job_type']] == b


@pytest.mark.parametrize('data', [
    {'default_model': 'missing'}, {'default_model': ' other/model/b'},
    {'default_model': ''}, {'default_model': False}, {'model_map': None},
    {'default_model': 'other/model/b', 'model_map': {'unused': 'missing'}},
    {'default_model': 'other/model/b', 'cartridge_models': {'one': 'missing'}},
    {'default_model': 'other/model/b', 'cartridge_models': {'unknown': 'other/model/b'}},
    {'cartridge_models': {'one': 'other/model/b'}}, {'model_map': {'': 'other/model/b'}},
])
def test_invalid_references_never_substitute(data):
    with pytest.raises(ValueError):
        select_models(DOC, data, MODELS)
