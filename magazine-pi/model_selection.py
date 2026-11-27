"""Normalize operator-selected models without changing the strict core schema."""
from copy import deepcopy

THINKING_LEVELS = frozenset(('inherit', 'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'))


def select_thinking(data, assignments, catalogue):
    """Resolve absent, explicit inherit and explicit levels independently of model choice."""
    default = data.get('default_thinking_level', 'inherit')
    overrides = data.get('cartridge_thinking_levels', {})
    if 'default_thinking_level' in data and (not isinstance(default, str) or default not in THINKING_LEVELS):
        raise ValueError('Unknown workflow reasoning level')
    if not isinstance(overrides, dict) or set(overrides) - assignments.keys():
        raise ValueError('Reasoning override references an unknown cartridge')
    result = {}
    errors = []
    for cid, model_id in assignments.items():
        model = catalogue.get(model_id, {})
        requested = overrides.get(cid, default)
        if not isinstance(requested, str) or requested not in THINKING_LEVELS:
            errors.append(f'{cid} ({model_id}): unknown reasoning level {requested!r}')
            continue
        effective = model.get('inherited_thinking_level', 'off') if requested == 'inherit' else requested
        levels = model.get('thinking_levels', ['off'])
        if effective not in levels:
            errors.append(f'{cid} ({model_id}): reasoning level {requested} is unavailable')
            continue
        if 'sdk_version' in model:
            source = 'cartridge' if cid in overrides else 'workflow' if 'default_thinking_level' in data else 'inherit'
            result[cid] = {
                'model': model_id, 'requested_thinking_level': requested, 'effective_thinking_level': effective,
                'reasoning_scope': source,
                'reasoning_source': model['inheritance_source'] if requested == 'inherit' else source,
                'provider_mapping': model['thinking_level_map'].get(effective),
                'compaction': dict(model['compaction']), 'context_window': model['context_window'],
                'max_output': model['max_output'], 'sdk_version': model['sdk_version'],
            }
    if errors:
        raise ValueError('; '.join(errors))
    return result


def select_models(document, data, available):
    """Explicit cartridge > supplied alias mapping > selected default.

    Old clients supplying only model_map keep their exact document/map contract.
    Concrete identifiers are opaque and never stripped, guessed or substituted.
    """
    aliases = data.get('model_map', {})
    overrides = data.get('cartridge_models', {})
    default = data.get('default_model')
    for value in (aliases, overrides):
        if not isinstance(value, dict) or not all(
                isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
                for k, v in value.items()):
            raise ValueError('Model mappings must contain non-empty string keys and values')
    if default is not None and (not isinstance(default, str) or not default.strip()):
        raise ValueError('Default model must be a non-empty identifier')
    ids = {c['id'] for c in document['cartridges']}
    if set(overrides) - ids:
        raise ValueError('Model override references an unknown cartridge')
    # Validate supplied mappings even when unused; an invalid explicit setting
    # must not silently disappear behind a default or a cartridge override.
    for model in [*aliases.values(), *([default] if default is not None else [])]:
        if model not in available:
            raise ValueError(f'Assigned model unavailable: {model}')
    if default is None and not overrides:
        return document, aliases
    normalized, resolved = deepcopy(document), {}
    for index, c in enumerate(normalized['cartridges']):
        if c['id'] in overrides:
            value = overrides[c['id']]
            model = value if value in available else aliases.get(value, value)
        else:
            model = aliases.get(c['job_type'], c['job_type'] if c['job_type'] in available else default)
        if model not in available:
            raise ValueError(f'Choose an available model for cartridge {c["id"]}: {model}')
        c['job_type'] = f'selected_{index}'
        resolved[c['job_type']] = model
    return normalized, resolved
