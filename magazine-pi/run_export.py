"""Read-only whole-run JSONL from retained Pi transcripts and canonical files.

No engine hooks, new journal, inferred lifecycle events or reconstructed chats.
"""
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

from magazine.result_store import result_path


class ExportError(ValueError):
    pass


@contextmanager
def directory(path):
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).absolute().parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_file(root, relative):
    """No symlink traversal, hard links, devices, or caller-supplied paths."""
    path = Path(relative)
    with directory(Path(root) / path.parent) as fd:
        handle = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(handle, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ExportError('Export source must be a regular file without hard links')
            return stream.read()


_SECRET_KEYS = {'authorization', 'proxyauthorization', 'apikey', 'token', 'accesstoken',
    'refreshtoken', 'idtoken', 'password', 'secret', 'clientsecret', 'ownerkey',
    'importkey', 'piwebtoken', 'telegrambottoken', 'privatekey', 'credentials'}
_ASSIGNMENT = re.compile(r'''(?i)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|owner[_-]?key|import[_-]?key|pi_web_token|telegram_bot_token|password|client[_-]?secret|secret|token)["']?\s*[:=]\s*)("[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''')


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {k: '[REDACTED]' if re.sub(r'[-_]', '', k).lower() in _SECRET_KEYS
                else redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [REDACTED]', value)
        value = re.sub(r'(?i)(\b(?:Proxy-)?Authorization\s*:\s*Basic\s+)\S+', r'\1[REDACTED]', value)
        value = re.sub(r'(https?://)[^/@\s]+:[^/@\s]+@', r'\1[REDACTED]@', value)
        return _ASSIGNMENT.sub(lambda m: m[1] + '"[REDACTED]"', value)
    return value


def export_run(root, rid, secrets=()):
    """Return a consistent prefix of each retained transcript, plus snapshots.

    Pi session line order is authoritative. Sequential sessions are ordered by
    their recorded start timestamp. Mutable run files are explicitly snapshots,
    not a fabricated history. All attempts in sessions/ are included.
    """
    root = Path(root)
    stable = {}
    def saved(name, optional=False):
        try:
            raw = read_file(root, name)
        except FileNotFoundError:
            if optional:
                stable[name] = None
                return None
            raise ExportError(f'Required export source missing: {name}') from None
        stable[name] = raw
        return json.loads(raw)

    try:
        doc, meta, state = saved('magazine.json'), saved('meta.json'), saved('run/state.json')
        snapshots = {name: saved(name, True) for name in
            ('run/execution.json', 'run/control.json', 'active-worker.json', 'export.json',
             'protocol.json', 'ledger.json')}
        # Attempt ledger (0.1.0-pi.6+): exact recorded session -> cartridge association.
        recorded = {}
        for cid, item in ((snapshots.get('ledger.json') or {}).get('cartridges') or {}).items():
            for attempt in item.get('attempts') or []:
                if attempt.get('session_id'):
                    recorded[attempt['session_id']] = cid
        cartridges = doc['cartridges']
        def assigned(c):
            return meta.get('cartridge_models', {}).get(c['id'], meta.get('model_map', {}).get(c['job_type']))
        sessions, session_bytes = [], {}
        try:
            with directory(root / 'sessions') as fd:
                names = sorted(n for n in os.listdir(fd) if n.endswith('.jsonl'))
        except FileNotFoundError:
            names = []
        for name in names:
            source = 'sessions/' + name
            raw = read_file(root, source)
            session_bytes[source] = raw
            # An active SDK writer may not yet have committed its last line.
            lines = raw.splitlines(keepends=True)
            pending = len(lines.pop()) if lines and not lines[-1].endswith(b'\n') else 0
            records = [json.loads(line) for line in lines]
            if not all(isinstance(r, dict) for r in records):
                raise ExportError('Session JSONL records must be objects')
            users = [r['message'] for r in records if r.get('type') == 'message' and r.get('message', {}).get('role') == 'user']
            prompt = ''.join(p.get('text', '') for p in users[0].get('content', []) if p.get('type') == 'text') if users else None
            change = next((r for r in records if r.get('type') == 'model_change'), {})
            model = f'{change["provider"]}/{change["modelId"]}' if 'provider' in change and 'modelId' in change else None
            sid = records[0].get('id') if records else None
            if sid in recorded:
                candidates = [recorded[sid]]
            else:
                candidates = [c['id'] for c in cartridges if c['prompt'] == prompt and (model is None or assigned(c) == model)]
            sessions.append((records[0].get('timestamp', '') if records else '', source, records, candidates, model, pending))
        rows = []
        def add(kind, source, record, cid=None, timestamp=None, **extra):
            rows.append({'schema_version': '1.0', 'run_id': rid, 'sequence': len(rows) + 1,
                'kind': kind, 'cartridge_id': cid, 'timestamp': timestamp,
                'source': source, 'record': record, **extra})
        add('export_info', None, {
            'scope': 'All retained session lines, cartridge definitions, canonical results and run snapshots; credentials redacted.',
            'ordering': 'Session source line order; sequential sessions by recorded start timestamp. Snapshots are not historical events.',
            'not_captured': 'No complete run-level transition journal or previous execution/state snapshots are retained. Missing prior history is not reconstructed.',
            'cartridge_association': 'Recorded attempt ledger session identity when present; otherwise exact persisted prompt and model match. Ambiguous legacy sessions list candidate IDs, never an invented assignment.',
        })
        add('preparation_snapshot', 'meta.json', meta, timestamp=meta.get('created_at'))
        for c in cartridges:
            add('cartridge_definition', 'magazine.json', c, c['id'], model_id=assigned(c))
        for _, source, records, candidates, model, pending in sorted(sessions, key=lambda s: (s[0], s[1])):
            sid = records[0].get('id') if records else None
            for line, record in enumerate(records, 1):
                add('session_record', source, record, candidates[0] if len(candidates) == 1 else None,
                    record.get('timestamp'), source_line=line, session_id=sid, model_id=model,
                    cartridge_candidates=candidates)
            if pending:
                add('retention_notice', source, {'uncommitted_tail_bytes': pending})
        for c in cartridges:
            relative = Path(result_path(str(root / 'run'), c['id'])).relative_to(root).as_posix()
            try:
                raw = read_file(root, relative)
            except FileNotFoundError:
                if c['id'] in state['done']:
                    raise ExportError('Completed cartridge result is missing') from None
                continue
            stable[relative] = raw
            add('result_snapshot', relative, {'text': raw.decode('utf-8')}, c['id'], model_id=assigned(c))
        for source, value in snapshots.items():
            if value is not None:
                add('run_snapshot', source, value, timestamp=value.get('halted_at') or value.get('created_at'))
        try:
            with directory(root / 'handovers') as fd:
                pass
            handovers = sorted(p.relative_to(root).as_posix() for p in (root / 'handovers').rglob('*')
                               if p.name in ('result.json', 'commit.json') and p.is_file() and not p.is_symlink())
        except FileNotFoundError:
            handovers = []
        for source in handovers:
            raw = read_file(root, source)
            stable[source] = raw
            add('handover_snapshot', source, json.loads(raw))
        add('state_snapshot', 'run/state.json', state)
        # Refuse mixed state/recovery snapshots; don't lock or mutate execution.
        for source, expected in stable.items():
            try:
                actual = read_file(root, source)
            except FileNotFoundError:
                actual = None
            if actual != expected:
                raise ExportError(f'Run changed during export ({source}); retry the download')
        for source, raw in session_bytes.items():
            if not read_file(root, source).startswith(raw):
                raise ExportError('Session changed during export; retry the download')
        try:
            with directory(root / 'sessions') as fd:
                after_names = sorted(n for n in os.listdir(fd) if n.endswith('.jsonl'))
        except FileNotFoundError:
            after_names = []
        if after_names != names:
            raise ExportError('Run changed during export; retry the download')
    except (OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, ExportError):
            raise
        raise ExportError('Retained run data is unsafe or unreadable; export refused') from error
    return ''.join(json.dumps(redact(row, secrets), ensure_ascii=False, allow_nan=False) + '\n' for row in rows).encode('utf-8')
