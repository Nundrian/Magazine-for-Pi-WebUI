"""Read-only chat discovery, exact model plans and recoverable managed deletion.

The host authenticates the single-operator instance. Legacy owner namespaces
are storage labels, never client-supplied identities. No execution lock is
acquired by chat readers; every path is derived from retained run records.
"""
import contextlib
import hashlib
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path, PurePosixPath

import beta5_helpers as h
import handover as ho
from run_export import redact

PLAN_BYTES = 128 * 1024
JSON_BYTES = 16 * 1024 * 1024
PAGE_BYTES = 512 * 1024
LINE_BYTES = 4 * 1024 * 1024
CHAT_ID = re.compile(r'[a-f0-9]{64}')


class SupportError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def check(ok, message, code=400):
    if not ok:
        raise SupportError(message, code)


def validate_plan(plan, workflow_name, ordered_ids, available_model_ids):
    """Validate the whole plan before returning any applicable mapping."""
    try:
        raw = plan if isinstance(plan, str) else json.dumps(plan, allow_nan=False)
        check(len(raw.encode('utf-8')) <= PLAN_BYTES, 'Model plan exceeds 128 KiB')
        value = ho.strict_json(raw)
    except (TypeError, ValueError) as exc:
        raise SupportError('Invalid model plan: ' + str(exc)) from None
    check(isinstance(value, dict), 'Model plan must be an object')
    check(set(value) <= {'schema_version', 'workflow_name', 'cartridge_ids', 'cartridge_models', 'default_model', 'rationale'}, 'Unknown model-plan field')
    check(value.get('schema_version') == '1.0', 'Model plan schema_version must be 1.0')
    check(value.get('workflow_name') == workflow_name, 'Model plan belongs to a different workflow')
    check(value.get('cartridge_ids') == ordered_ids and len(set(ordered_ids)) == len(ordered_ids), 'Model plan cartridge IDs must match the exact ordered workflow')
    mapping = value.get('cartridge_models', {})
    check(isinstance(mapping, dict) and set(mapping) <= set(ordered_ids), 'Unknown cartridge in model plan')
    default = value.get('default_model')
    available = set(available_model_ids)
    if default is not None:
        check(isinstance(default, str) and default in available, 'Default model is unavailable in Pi catalogue')
    resolved = {}
    for cid in ordered_ids:
        model = mapping.get(cid, default)
        check(isinstance(model, str) and model in available, f'Choose an exact available Pi model for {cid}: {model}')
        resolved[cid] = model
    rationale = value.get('rationale', {})
    check(isinstance(rationale, dict) and set(rationale) <= set(ordered_ids)
          and all(isinstance(v, str) and len(v) <= 2000 for v in rationale.values()), 'Invalid model-plan rationale')
    return {'schema_version': '1.0', 'workflow_name': workflow_name, 'cartridge_ids': list(ordered_ids),
            'cartridge_models': resolved, 'rationale': rationale}


@contextlib.contextmanager
def directory(path):
    """Anchor an absolute configured path, rejecting links in every ancestor."""
    p = Path(path)
    check(p.is_absolute() and '..' not in p.parts, 'Unsafe managed directory', 409)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in p.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def plain_file(base, rel):
    parts = PurePosixPath(rel).parts
    check(parts and not PurePosixPath(rel).is_absolute() and all(p not in ('.', '..') and '\\' not in p and ':' not in p for p in parts), 'Unsafe retained file', 409)
    with directory(base) as root:
        fd = os.dup(root)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        finally:
            os.close(fd)
        try:
            info = os.fstat(file_fd)
            check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Retained record must be a regular file without hard links', 409)
            with os.fdopen(file_fd, 'rb', closefd=False) as stream:
                yield stream
        finally:
            os.close(file_fd)


def read_json(base, rel, limit=JSON_BYTES):
    with plain_file(base, rel) as stream:
        raw = stream.read(limit + 1)
    check(len(raw) <= limit, 'Retained JSON record is oversized', 413)
    return ho.strict_json(raw.decode('utf-8'))


def sidecar(service, project_id, name, ids, available):
    try:
        value = read_json(service.root / 'library' / project_id, 'model-assignments.json', PLAN_BYTES)
        return {'state': 'valid', 'plan': validate_plan(value, name, ids, available)}
    except FileNotFoundError:
        return {'state': 'absent'}
    except (OSError, ValueError) as exc:
        return {'state': 'invalid', 'error': 'Assignments were not applied: ' + str(exc)}


def visible_message(record, model, secrets):
    """Only host-visible content; no thinking blocks or private session fields."""
    if record.get('type') != 'message' or not isinstance(record.get('message'), dict):
        return None
    message = record['message']
    role = message.get('role')
    if role not in ('user', 'assistant', 'toolResult'):
        return None
    content = message.get('content', [])
    if isinstance(content, str):
        content = [{'type': 'text', 'text': content}]
    blocks = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        if part.get('type') == 'text':
            text = str(part.get('text', ''))
            if role == 'assistant':
                text = re.sub(r'<think\b[^>]*>[\s\S]*?(?:</think\s*>|$)', '', text, flags=re.I)
            if text:
                blocks.append({'type': 'text', 'text': text})
        elif role == 'assistant' and part.get('type') == 'toolCall':
            blocks.append({'type': 'tool_call', 'name': str(part.get('name', 'Tool')),
                           'id': str(part.get('id', '')), 'arguments': part.get('arguments', {})})
        elif part.get('type') == 'image':
            blocks.append({'type': 'text', 'text': '[Image retained in original session; binary data is not displayed here.]'})
    if not blocks:
        return None
    return redact({'id': record.get('id'), 'timestamp': record.get('timestamp'),
                   'role': role, 'model': model if role == 'assistant' else None,
                   'tool_name': message.get('toolName') if role == 'toolResult' else None,
                   'is_error': bool(message.get('isError')) if role == 'toolResult' else False,
                   'blocks': blocks}, secrets)


class Support:
    def __init__(self, service):
        self.service = service
        self.cache = {}

    def runs(self):
        """Metadata-only enumeration without status reconciliation or writes."""
        s = self.service
        result = []
        for owner in s.run_owners():
            parent = s.root/'runs'/owner
            if not parent.exists():
                continue
            with directory(parent) as fd:
                names = [name for name in os.listdir(fd) if re.fullmatch(r'[a-f0-9]{12}', name)
                         and stat.S_ISDIR(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode)]
            for rid in names:
                base = parent/rid
                meta = read_json(base, 'meta.json')
                if meta.get('owner_id') != owner:
                    continue
                state = read_json(base, 'run/state.json')
                check(not any(r['run_id'] == rid for r in result), 'Ambiguous legacy run ID', 409)
                result.append({'run_id': rid, '_owner': owner, 'name': meta.get('name'), 'project_id': meta.get('project_id'),
                               'created_at': meta.get('created_at'), 'model': next(iter((meta.get('cartridge_models') or {}).values()), None),
                               'display_status': state.get('status'), 'active': s.has_task(owner, rid)})
        return result

    def records(self):
        """All accessible retained attempts, including sessions not yet archived."""
        s = self.service
        rows = []
        for run in self.runs():
            rid = run['run_id']
            owner = run['_owner']
            base = s.root / 'runs' / owner / rid
            try:
                with plain_file(base, 'ledger.json') as stream:
                    st = os.fstat(stream.fileno())
                    stamp = (st.st_ino, st.st_size, st.st_mtime_ns)
                    cached = self.cache.get(rid)
                    if cached and cached[0] == stamp:
                        ledger = cached[1]
                    else:
                        raw = stream.read(JSON_BYTES + 1)
                        check(len(raw) <= JSON_BYTES, 'Attempt ledger is oversized', 413)
                        ledger = ho.strict_json(raw.decode())
                        self.cache[rid] = (stamp, ledger)
            except FileNotFoundError:
                ledger = None
            check(ledger is None or isinstance(ledger, dict), 'Invalid retained ledger', 409)
            used = set()
            for cid in (ledger or {}).get('order', []):
                for number, attempt in enumerate((ledger['cartridges'].get(cid) or {}).get('attempts', []), 1):
                    rel = attempt.get('session_file')
                    # Only original managed sessions, never a caller path or an
                    # unvalidated handover/archive path, are selectable here.
                    if rel:
                        check(len(PurePosixPath(rel).parts) == 2 and PurePosixPath(rel).parts[0] == 'sessions', 'Session outside managed session store', 409)
                        used.add(rel)
                    identity = '\0'.join((rid, cid, str(attempt.get('attempt_id', number)), rel or ''))
                    row = {'chat_id': hashlib.sha256(identity.encode()).hexdigest(), 'run_id': rid,
                           'run_name': run.get('name') or 'Workflow', 'cartridge_id': cid,
                           'attempt_id': attempt.get('attempt_id'), 'attempt_number': attempt.get('attempt_number', number),
                           'session_id': attempt.get('session_id'), 'model': attempt.get('model'),
                           'status': attempt.get('status', 'unknown'), 'created_at': attempt.get('started') or run.get('created_at'),
                           'live': bool(run.get('active') and attempt.get('status') == 'running'),
                           'state': 'available' if rel else 'not_created', '_owner': owner, '_rel': rel}
                    rows.append(row)
            # Pre-ledger pi.5 sessions retain individual selection, labelled as
            # legacy because a trustworthy cartridge association is unavailable.
            try:
                with directory(base / 'sessions') as fd:
                    names = os.listdir(fd)
                for name in names:
                    if name.endswith('.jsonl') and '/' not in name and '\\' not in name and 'sessions/'+name not in used:
                        rel = 'sessions/' + name
                        rows.append({'chat_id': hashlib.sha256((rid+'\0legacy\0'+name).encode()).hexdigest(),
                                     'run_id': rid, 'run_name': run.get('name') or 'Workflow', 'cartridge_id': 'legacy',
                                     'attempt_id': None, 'attempt_number': None, 'session_id': None, 'model': run.get('model'),
                                     'status': run['display_status'], 'created_at': run.get('created_at'),
                                     'live': bool(run.get('active')), 'state': 'legacy_unassociated' if ledger else 'legacy', '_owner': owner, '_rel': rel})
            except FileNotFoundError:
                pass
        return sorted(rows, key=lambda r: (r.get('created_at') or '', r['chat_id']), reverse=True)

    def chats(self, data):
        check(not ({'owner', 'path', 'session_file'} & set(data)), 'Client session paths/owners are not accepted')
        offset, limit = data.get('offset', 0), data.get('limit', 100)
        check(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 200, 'Invalid history page')
        rows = self.records()
        page = rows[offset:offset+limit]
        return {'chats': [{k: v for k, v in r.items() if not k.startswith('_')} for r in page],
                'total': len(rows), 'next_offset': offset+len(page) if offset+len(page) < len(rows) else None,
                'scope': 'Magazine chats — all retained worker attempts in this single-operator instance'}

    def chat(self, data):
        check(not ({'owner', 'path', 'session_file'} & set(data)), 'Client session paths/owners are not accepted')
        cid = data.get('chat_id')
        check(isinstance(cid, str) and CHAT_ID.fullmatch(cid), 'Chat not found', 404)
        row = next((r for r in self.records() if r['chat_id'] == cid), None)
        check(row is not None, 'Chat not found', 404)
        offset = data.get('offset', 0)
        check(type(offset) is int and offset >= 0, 'Invalid chat cursor')
        answer = {'messages': [], 'next_offset': 0, 'generation': None, 'reset': False, 'more': False,
                  'live': row['live'], 'state': row['state'], 'updated_at': time.time()}
        if not row['_rel']:
            return answer
        base = self.service.root / 'runs' / row['_owner'] / row['run_id']
        try:
            with plain_file(base, row['_rel']) as stream:
                st = os.fstat(stream.fileno())
                identity = f'{st.st_dev:x}-{st.st_ino:x}'
                check(st.st_size <= ho.MAX_SESSION_BYTES, 'Session exceeds the retained read limit', 413)
                # Identity is checked at the beginning even for subsequent pages.
                header = stream.readline(LINE_BYTES + 1)
                if not header.endswith(b'\n'):
                    answer['state'] = 'waiting_for_complete_record'
                    return answer
                head = ho.strict_json(header.decode())
                check(head.get('type') == 'session' and (not row['session_id'] or head.get('id') == row['session_id']), 'Session identity mismatch', 409)
                def cursor(at):
                    # A bounded prefix/boundary fingerprint also detects an
                    # in-place truncation and regrowth with the same inode.
                    stream.seek(0)
                    prefix = stream.read(min(at, 1024))
                    stream.seek(max(0, at-256))
                    edge = stream.read(min(at, 256))
                    return identity + ':' + hashlib.sha256(prefix + edge).hexdigest()[:24]
                reset = offset > st.st_size or (data.get('generation') is not None and data['generation'] != cursor(offset))
                if reset:
                    offset = 0
                if offset:
                    stream.seek(offset-1)
                    check(stream.read(1) == b'\n', 'Chat cursor must be a record boundary')
                stream.seek(offset)
                start, messages, count = offset, [], 0
                while stream.tell() - start < PAGE_BYTES and count < 100:
                    begin = stream.tell()
                    raw = stream.readline(LINE_BYTES + 1)
                    if not raw:
                        break
                    check(len(raw) <= LINE_BYTES, 'A session record is too large to display', 413)
                    if not raw.endswith(b'\n'):
                        stream.seek(begin)  # writer may still be appending
                        break
                    try:
                        record = ho.strict_json(raw.decode())
                    except (UnicodeDecodeError, ValueError):
                        answer['state'] = 'invalid_complete_record'
                        raise SupportError('A complete retained session record is invalid', 409) from None
                    msg = visible_message(record, row['model'], self.service.export_secrets())
                    if msg:
                        messages.append(msg)
                    count += 1
                next_offset = stream.tell()
                more = next_offset < st.st_size and raw.endswith(b'\n') if raw else False
                answer.update(messages=messages, next_offset=next_offset, generation=cursor(next_offset), reset=reset, more=more)
                return answer
        except FileNotFoundError:
            answer['state'] = 'missing'
            return answer
        except OSError:
            raise SupportError('Session unavailable or unsafe; no file was read outside its managed store', 409) from None

    def deletion_preview(self, rid):
        s = self.service
        owner = s.run_owner(rid)
        payload = h._status_payload(owner, rid)
        check(not s.has_task(owner, rid) and s.scheduler.entry(rid) is None and not s.scheduler.persistence_error,
              'Active, queued or uncertain scheduler work cannot be deleted', 409)
        status = s.display_status(payload, rid)
        check(status in ('READY', 'COMPLETED', 'HALTED', 'STOPPED'), 'Run must be inactive before deletion', 409)
        execution = payload.get('execution') or {}
        control = payload.get('control') or {}
        check((not control or control.get('termination_status') == 'CONFIRMED')
              and not (status != 'COMPLETED' and execution and not execution.get('halted') and execution.get('owner_alive')),
              'Worker termination is not confirmed', 409)
        try:
            ledger = read_json(s.root/'runs'/owner/rid, 'ledger.json')
            check(not any(a.get('status') == 'running' for c in ledger.get('cartridges', {}).values() for a in c.get('attempts', [])), 'An attempt still appears active', 409)
        except FileNotFoundError:
            pass
        return {'run_id': rid, 'status': status, 'recoverable': True,
                'removed': ['Saved run snapshot', 'ledger and attempts', 'managed worker sessions', 'committed results', 'Magazine-owned magazine-runs workspace for this run'],
                'kept': ['Actual user project files (including /projects/3D kit)', 'Imported workflow template', 'Input ZIPs', 'Finished Work exports'],
                'message': 'Moves only Magazine-managed saved work to recoverable Magazine trash. It does not delete your actual project files.'}

    def _trash(self, kind, identifier, owner=None):
        s = self.service
        trash = s.root/'trash'
        trash.mkdir(mode=0o700, exist_ok=True)
        token = uuid.uuid4().hex
        moves = []
        # Stage under a private transaction; rollback reverses completed renames.
        with directory(trash) as tf:
            os.mkdir(token, mode=0o700, dir_fd=tf)
        try:
            with directory(trash/token) as transaction:
                targets = [('record', s.root/'library' if kind == 'template' else s.root/'runs'/owner, identifier)]
                if kind == 'run':
                    workspace = Path(s.config['cwd'])/'magazine-runs'
                    if workspace.exists():
                        targets.append(('workspace', workspace, identifier))
                def write_record(name, record):
                    fd = os.open(name, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600, dir_fd=transaction)
                    with os.fdopen(fd, 'w') as stream:
                        json.dump(record, stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.fsync(transaction)
                record = {'kind': kind, 'id': identifier, 'owner': owner, 'created_at': time.time(),
                          'parts': [label for label, _, _ in targets], 'state': 'interrupted'}
                # The durable intent precedes the first rename. A process death
                # mid-transaction remains discoverable and restorable on restart.
                write_record('intent.json', record)
                for label, parent, name in targets:
                    with directory(parent) as pf:
                        try:
                            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pf)
                        except FileNotFoundError:
                            if label == 'workspace':
                                continue
                            raise
                        try:
                            before = os.fstat(child)
                            now = os.stat(name, dir_fd=pf, follow_symlinks=False)
                            check((before.st_dev, before.st_ino) == (now.st_dev, now.st_ino), 'Managed directory changed during deletion', 409)
                            os.rename(name, label, src_dir_fd=pf, dst_dir_fd=transaction)
                            moves.append((label, parent, name))
                            os.fsync(pf)
                        finally:
                            os.close(child)
                record.update(parts=[label for label, _, _ in moves], state='complete')
                write_record('trash.json', record)
                os.unlink('intent.json', dir_fd=transaction)
                os.fsync(transaction)
        except Exception:
            with directory(trash/token) as transaction:
                for label, parent, name in reversed(moves):
                    with directory(parent) as pf:
                        os.rename(label, name, src_dir_fd=transaction, dst_dir_fd=pf)
                        os.fsync(pf)
                for name in ('trash.json', 'intent.json'):
                    try:
                        os.unlink(name, dir_fd=transaction)
                    except FileNotFoundError:
                        pass
            raise
        return {'deleted': True, 'recoverable': True, 'trash_id': token,
                'message': 'Removed from saved lists; retained in Magazine trash. Actual project files and exports were kept.'}

    def delete_run(self, data):
        check(not ({'owner', 'path'} & set(data)), 'Client paths/owners are not accepted')
        rid = data.get('run_id')
        check(data.get('confirmation') == rid and isinstance(rid, str), 'Type the exact run ID to confirm deletion')
        self.deletion_preview(rid)  # no await between final recheck and renames
        return self._trash('run', rid, self.service.run_owner(rid))

    def delete_template(self, data):
        check(not ({'owner', 'path'} & set(data)), 'Client paths/owners are not accepted')
        pid = data.get('project_id')
        check(isinstance(pid, str) and h._LIBRARY_ID.fullmatch(pid) and data.get('confirmation') == pid, 'Type the exact workflow template ID to confirm deletion')
        for run in self.runs():
            if run.get('project_id') == pid:
                self.deletion_preview(run['run_id'])
        with h._library_root_fd(str(self.service.root/'library')) as fd:
            target = os.open(pid, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=fd)
            os.close(target)
        return self._trash('template', pid)

    def trash_list(self):
        folder = self.service.root/'trash'
        if not folder.exists():
            return {'items': []}
        with directory(folder) as fd:
            names = os.listdir(fd)
        items = []
        for name in names:
            if re.fullmatch(r'[a-f0-9]{32}', name):
                try:
                    item = self._trash_record(folder/name)
                    items.append({k: item.get(k) for k in ('kind', 'id', 'created_at', 'state')} | {'trash_id': name})
                except (FileNotFoundError, OSError, ValueError):
                    continue
        return {'items': sorted(items, key=lambda r: r['created_at'], reverse=True)}

    @staticmethod
    def _trash_record(base):
        try:
            return read_json(base, 'trash.json', 4096)
        except (FileNotFoundError, ValueError):
            return read_json(base, 'intent.json', 4096)

    def restore(self, data):
        token = data.get('trash_id')
        check(isinstance(token, str) and re.fullmatch(r'[a-f0-9]{32}', token), 'Invalid trash item')
        check(not ({'owner', 'path'} & set(data)), 'Client paths/owners are not accepted')
        s = self.service
        base = s.root/'trash'/token
        record = self._trash_record(base)
        kind, ident, owner = record.get('kind'), record.get('id'), record.get('owner')
        check(kind in ('run', 'template') and isinstance(ident, str), 'Invalid trash record', 409)
        check(bool((re.fullmatch(r'[a-f0-9]{12}', ident) if kind == 'run' else h._LIBRARY_ID.fullmatch(ident))), 'Invalid retained identity', 409)
        if kind == 'run':
            check(owner in s.run_owners() or owner == 'local', 'Inaccessible retained namespace', 404)
            check(s.scheduler.entry(ident) is None, 'Run ID is already scheduled', 409)
        targets = [('record', s.root/'library' if kind == 'template' else s.root/'runs'/owner, ident)]
        if 'workspace' in record.get('parts', []):
            check(kind == 'run', 'Invalid workspace trash record', 409)
            targets.append(('workspace', Path(s.config['cwd'])/'magazine-runs', ident))
        restored = []
        with directory(base) as tf:
            try:
                for label, parent, name in targets:
                    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with directory(parent) as pf:
                        try:
                            retained = os.stat(label, dir_fd=tf, follow_symlinks=False)
                        except FileNotFoundError:
                            # An interrupted deletion may not have moved this
                            # part yet. It must still be safely at its origin.
                            original = os.stat(name, dir_fd=pf, follow_symlinks=False)
                            check(record.get('state') == 'interrupted' and stat.S_ISDIR(original.st_mode), 'Missing retained trash data', 409)
                            continue
                        check(stat.S_ISDIR(retained.st_mode), 'Unsafe retained trash directory', 409)
                        try:
                            os.stat(name, dir_fd=pf, follow_symlinks=False)
                        except FileNotFoundError:
                            pass
                        else:
                            raise SupportError('Restore would overwrite existing saved work', 409)
                        os.rename(label, name, src_dir_fd=tf, dst_dir_fd=pf)
                        restored.append((label, parent, name))
                        os.fsync(pf)
                for name in ('trash.json', 'intent.json'):
                    try:
                        os.unlink(name, dir_fd=tf)
                    except FileNotFoundError:
                        pass
                os.fsync(tf)
            except Exception:
                for label, parent, name in reversed(restored):
                    with directory(parent) as pf:
                        os.rename(name, label, src_dir_fd=pf, dst_dir_fd=tf)
                raise
        return {'restored': True, 'id': ident, 'message': 'Saved work restored; nothing was started.'}
