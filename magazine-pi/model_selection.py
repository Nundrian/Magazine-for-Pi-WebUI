"""Normalize operator-selected models without changing the strict core schema."""
from copy import deepcopy


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
