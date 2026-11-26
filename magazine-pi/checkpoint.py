"""Generic, durable project checkpoints authorised by accepted cartridge results."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import time

import confined_fs as fs

STEP_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
HASH = re.compile(r'[0-9a-f]{64}\Z')
MAX_REPORT = 1024 * 1024
MAX_RECORD = 16 * 1024 * 1024
RECORD = 'project-checkpoints.json'
RECORD_VERSION = 2


class CheckpointError(ValueError):
    """Refusal safe to show the model and owner."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()


def parse_declaration(value, cartridge_ids):
    if value is None:
        return None
    allowed = {'root', 'state_file', 'report', 'required', 'baseline_step', 'baseline_sha256', 'steps'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('Unknown project_checkpoint declaration fields')
    root = value.get('root')
    if (not isinstance(root, str) or not root.startswith('/') or root.startswith('//')
            or root == '/' or '..' in PurePosixPath(root).parts or '\0' in root or '\\' in root):
        raise ValueError('project_checkpoint.root must be an absolute container project path')
    state = fs.plain_name(value.get('state_file', 'state.json'))
    fs.plain_name(state + '.before-' + '0' * 32)
    report = fs.plain_name(value.get('report', '{step}.json'))
    if (state.startswith('.') or report.startswith('.') or report.count('{step}') != 1
            or '{' in report.replace('{step}', '') or '}' in report.replace('{step}', '')):
        raise ValueError('Checkpoint files must be plain names and report must contain {step} exactly once')
    steps = value.get('steps', list(cartridge_ids))
    if (not isinstance(steps, list) or not steps or any(not isinstance(s, str) or not STEP_RE.fullmatch(s)
            or s not in cartridge_ids for s in steps) or len(set(steps)) != len(steps)):
        raise ValueError('project_checkpoint.steps must list cartridges of this workflow')
    baseline = value.get('baseline_step')
    if baseline is not None and (not isinstance(baseline, str) or not STEP_RE.fullmatch(baseline) or baseline in steps):
        raise ValueError('project_checkpoint.baseline_step must name the preceding step')
    baseline_hash = value.get('baseline_sha256')
    if baseline_hash is not None and (not baseline or not isinstance(baseline_hash, str) or not HASH.fullmatch(baseline_hash)):
        raise ValueError('project_checkpoint.baseline_sha256 requires baseline_step and its state file SHA-256')
    required = value.get('required', True)
    if type(required) is not bool:
        raise ValueError('project_checkpoint.required must be a boolean')
    for step in [*steps, *([baseline] if baseline else [])]:
        if fs.plain_name(report.format(step=step)) in (state, 'backup') or state == 'backup':
            raise ValueError('Checkpoint reports, state and backup directory need distinct names')
    result = {'root': str(PurePosixPath(root)), 'state_file': state, 'report': report,
              'required': required, 'baseline_step': baseline, 'steps': [s for s in cartridge_ids if s in steps]}
    if baseline_hash is not None:
        result['baseline_sha256'] = baseline_hash
    return result


def _json(raw, name):
    def pairs(values):
        record = dict(values)
        if len(record) != len(values):
            raise ValueError('duplicate JSON key')
        return record

    def constant(_):
        raise ValueError('non-finite JSON number')

    try:
        body = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError):
        raise CheckpointError(f'{name} is not valid JSON') from None
    if not isinstance(body, dict):
        raise CheckpointError(f'{name} must be a JSON object')
    return body


def _read_optional(fd, name, limit):
    try:
        return fs.read_plain(fd, name, limit)
    except FileNotFoundError:
        return None


def _digest(raw):
    return sha256_bytes(raw) if raw is not None else None


class Checkpoints:
    def __init__(self, decl, run_base, *, workflow_identity, allowed_roots=(), protected_roots=(), clock=time.time):
        self.decl = decl
        self.root = Path(decl['root'])
        self.run_base = Path(run_base)
        self.allowed = allowed_roots
        self.protected = [*protected_roots, self.run_base]
        self.binding = {**workflow_identity, 'declaration_sha256': sha256_bytes(encoded(decl))}
        self.state_name = decl['state_file']
        self.clock = clock

    @contextmanager
    def _open(self, exclusive=False):
        try:
            fs.permitted(self.root, self.allowed, self.protected)
            if os.name != 'posix':
                raise CheckpointError('Checkpoint access requires the Linux Pi service')
            import fcntl

            with fs.directory(self.root) as project, fs.directory(self.run_base) as trusted:
                mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(project, mode | fcntl.LOCK_NB)
                self._check_dirs(project, trusted)
                yield project, trusted
                self._check_dirs(project, trusted)
        except CheckpointError:
            raise
        except (OSError, ValueError) as error:
            detail = error.strerror if isinstance(error, OSError) else str(error)
            raise CheckpointError(f'Checkpoint storage refused: {detail}') from None

    def _check_dirs(self, project, trusted):
        fs.same_directory(self.root, project)
        fs.same_directory(self.run_base, trusted)
        try:
            with fs.child_directory(project, 'backup') as folder:
                fs.same_directory(self.root / 'backup', folder)
        except FileNotFoundError:
            pass

    def _record(self, trusted):
        raw = _read_optional(trusted, RECORD, MAX_RECORD)
        if raw is None:
            return {'version': RECORD_VERSION, 'binding': self.binding, 'history': [], 'pending': None}
        record = _json(raw, 'Trusted checkpoint journal')
        if 'version' not in record:
            raise CheckpointError('Legacy checkpoint history lacks transaction evidence. Preserve the run and resume '
                                  'with a reviewed continuation and an explicitly pinned baseline.')
        if (record.get('version') != RECORD_VERSION or record.get('binding') != self.binding
                or set(record) != {'version', 'binding', 'history', 'pending'}
                or not isinstance(record['history'], list)
                or (record['pending'] is not None and not isinstance(record['pending'], dict))):
            raise CheckpointError('Trusted checkpoint journal is damaged or belongs to another workflow')
        for index, entry in enumerate(record['history']):
            if index >= len(self.decl['steps']):
                raise CheckpointError('Trusted checkpoint history has unexpected entries')
            self._check_entry(entry, index)
            if entry['phase'] != 'committed':
                raise CheckpointError('Trusted checkpoint history includes an incomplete transaction')
            if index and entry['previous_sha256'] != record['history'][index - 1]['state_sha256']:
                raise CheckpointError('Trusted checkpoint history has a broken state chain')
        if record['pending'] is not None:
            pending = record['pending']
            self._check_entry(pending, len(record['history']))
            if (pending['phase'] != 'applying' or (record['history']
                    and pending['previous_sha256'] != record['history'][-1]['state_sha256'])):
                raise CheckpointError('Trusted pending checkpoint does not follow the committed state')
        return record

    def _check_entry(self, entry, index):
        fields = {'step', 'attempt_id', 'previous_sha256', 'report_sha256', 'state_sha256',
                  'result_sha256', 'backup', 'phase'}
        if (not isinstance(entry, dict) or set(entry) != fields or index >= len(self.decl['steps'])
                or entry['step'] != self.decl['steps'][index]
                or not isinstance(entry['attempt_id'], str) or not re.fullmatch(r'[0-9a-f]{32}', entry['attempt_id'])
                or any(not isinstance(entry[k], str) or not HASH.fullmatch(entry[k])
                       for k in ('report_sha256', 'state_sha256', 'result_sha256'))
                or (entry['previous_sha256'] is not None and (not isinstance(entry['previous_sha256'], str)
                    or not HASH.fullmatch(entry['previous_sha256'])))
                or (entry['backup'] != self._backup_name(entry['attempt_id'], entry['previous_sha256']))
                or entry['phase'] not in ('applying', 'committed')):
            raise CheckpointError('Trusted checkpoint journal contains an invalid transaction')

    def _save(self, trusted, record, guard):
        body = encoded(record)
        if len(body) > MAX_RECORD:
            raise CheckpointError('Trusted checkpoint journal exceeds its size limit')
        fs.replace_plain(trusted, RECORD, body, guard)

    def _report(self, project, step):
        name = fs.plain_name(self.decl['report'].format(step=step))
        raw = fs.read_plain(project, name, MAX_REPORT)
        result = _json(raw, name)
        if result.get('step_id') != step or result.get('status') != 'DONE':
            raise CheckpointError(f'{name} must identify {step} and have status DONE')
        return name, sha256_bytes(raw)

    def _state(self, project):
        raw = _read_optional(project, self.state_name, MAX_REPORT)
        return (None, None) if raw is None else (_json(raw, self.state_name), raw)

    def _validate_base(self, project, history, state, raw):
        last = history[-1] if history else None
        if last:
            if _digest(raw) != last['state_sha256']:
                raise CheckpointError('Project checkpoint state changed outside the checkpoint tool')
            for entry in history:
                if self._report(project, entry['step'])[1] != entry['report_sha256']:
                    raise CheckpointError(f"The recorded checksum of {entry['step']} does not match its report on disk")
            return
        baseline = self.decl['baseline_step']
        if baseline is None and raw is None:
            return
        if baseline and state and state.get('last_committed_step') == baseline:
            if self._report(project, baseline)[1] != state.get('last_report_hash_sha256'):
                raise CheckpointError(f'The recorded checksum of {baseline} does not match its report on disk')
            if _digest(raw) == self.decl.get('baseline_sha256'):
                return
        raise CheckpointError('Checkpoint history is missing or the initial state is unverifiable. Use a reviewed '
                              'continuation with a pinned baseline state SHA-256, or an empty checkpoint directory.')

    def admission(self, expected):
        """Check the last accepted checkpoint before the first worker is dispatched."""
        with self._open() as (project, trusted):
            record = self._record(trusted)
            if record['pending'] is not None:
                raise CheckpointError('A checkpoint transaction is pending and needs explicit run recovery')
            if [entry['step'] for entry in record['history']] != expected:
                raise CheckpointError('Trusted checkpoint history differs from the accepted run history')
            state, raw = self._state(project)
            self._validate_base(project, record['history'], state, raw)

    def _stage(self, project, history, step, attempt_id, at):
        expected = self.decl['steps'][len(history):len(history) + 1]
        if expected != [step]:
            raise CheckpointError('Checkpoint must follow its previously committed workflow step')
        state, raw = self._state(project)
        self._validate_base(project, history, state, raw)
        name, report_hash = self._report(project, step)
        old_steps = (state or {}).get('completed_steps', [])
        if (not isinstance(old_steps, list) or any(not isinstance(s, str) or not STEP_RE.fullmatch(s)
                for s in old_steps) or len(set(old_steps)) != len(old_steps) or step in old_steps):
            raise CheckpointError('Checkpoint state has invalid step history')
        new = dict(state or {}, last_committed_step=step, last_report=name,
                   last_report_hash_sha256=report_hash, completed_steps=old_steps + [step],
                   last_updated=at, committed_by='magazine_checkpoint')
        new.pop('halt_note', None)
        body = encoded(new)
        if len(body) > MAX_REPORT:
            raise CheckpointError('Checkpoint state exceeds its size limit')
        return {'step': step, 'attempt_id': attempt_id, 'at': at, 'previous_sha256': _digest(raw),
                'report_sha256': report_hash, 'state_sha256': _digest(body)}, raw, body

    def stage(self, step, attempt_id):
        """Validate a worker's proposed checkpoint. This stage performs no writes."""
        at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.clock()))
        with self._open() as (project, trusted):
            record = self._record(trusted)
            if record['pending'] is not None:
                raise CheckpointError('A previous checkpoint transaction is pending')
            receipt, _, _ = self._stage(project, record['history'], step, attempt_id, at)
            return receipt

    def _backup_name(self, attempt_id, previous):
        return f'{self.state_name}.before-{attempt_id}' if previous is not None else None

    def _backup(self, project, name, before, previous, guard):
        with fs.child_directory(project, 'backup', create=before is not None) as folder:
            def check():
                guard()
                fs.same_directory(self.root / 'backup', folder)
            check()
            current = _read_optional(folder, name, MAX_REPORT)
            if current is None and before is not None:
                def confirm_new_backup():
                    check()
                    if _read_optional(folder, name, MAX_REPORT) is not None:
                        raise CheckpointError('Checkpoint backup appeared during publication')
                fs.replace_plain(folder, name, before, confirm_new_backup)
                current = fs.read_plain(folder, name, MAX_REPORT)
            if _digest(current) != previous:
                raise CheckpointError('Checkpoint backup is missing or changed')
            check()

    def commit(self, receipt, result_sha256):
        """After handover validation, apply only the exact proven transaction."""
        step, attempt_id = receipt['step'], receipt['attempt_id']
        entry = {k: v for k, v in receipt.items() if k != 'at'}
        entry.update(result_sha256=result_sha256, phase='applying',
                     backup=self._backup_name(attempt_id, receipt['previous_sha256']))
        with self._open(exclusive=True) as (project, trusted):
            def guard():
                self._check_dirs(project, trusted)
            record = self._record(trusted)
            history = record['history']
            committed = dict(entry, phase='committed')
            if history and history[-1] == committed and record['pending'] is None:
                state, raw = self._state(project)
                self._validate_base(project, history, state, raw)
                if entry['backup']:
                    self._backup(project, entry['backup'], None, entry['previous_sha256'], guard)
                return dict(committed, committed=step, already_committed=True)
            self._check_entry(entry, len(history))
            pending = record['pending']
            if pending is not None and pending != entry:
                raise CheckpointError('A different checkpoint transaction is pending; review the run')
            _, current = self._state(project)
            if _digest(current) == entry['state_sha256'] and pending == entry:
                if self._report(project, step)[1] != entry['report_sha256']:
                    raise CheckpointError('The pending checkpoint report changed')
                for earlier in history:
                    if self._report(project, earlier['step'])[1] != earlier['report_sha256']:
                        raise CheckpointError('A previously committed checkpoint report changed')
                if entry['backup']:
                    self._backup(project, entry['backup'], None, entry['previous_sha256'], guard)
                os.fsync(project)
            else:
                actual, before, body = self._stage(project, history, step, attempt_id, receipt['at'])
                if actual != receipt:
                    raise CheckpointError('Project state or report changed after checkpoint validation')
                if pending is None:
                    record['pending'] = entry
                    self._save(trusted, record, guard)
                if entry['backup']:
                    self._backup(project, entry['backup'], before, entry['previous_sha256'], guard)
                def confirm_previous():
                    guard()
                    if (_digest(_read_optional(project, self.state_name, MAX_REPORT)) != entry['previous_sha256']
                            or self._report(project, step)[1] != entry['report_sha256']):
                        raise CheckpointError('Project state or report changed before checkpoint publication')
                fs.replace_plain(project, self.state_name, body, confirm_previous)
            record['history'] = [*history, committed]
            record['pending'] = None
            state, raw = self._state(project)
            self._validate_base(project, record['history'], state, raw)
            self._save(trusted, record, guard)
            return dict(committed, committed=step, already_committed=pending is not None)

    def audit(self):
        """Inspect the journal and project with the same read boundary as commit."""
        out = {'state_present': False, 'last_committed_step': None, 'problems': []}
        try:
            with self._open() as (project, trusted):
                record = self._record(trusted)
                state, raw = self._state(project)
                out.update(state_present=raw is not None,
                           last_committed_step=record['history'][-1]['step'] if record['history'] else None)
                if record['pending'] is not None:
                    raise CheckpointError('A checkpoint transaction is pending')
                self._validate_base(project, record['history'], state, raw)
                for entry in record['history']:
                    if entry['backup']:
                        self._backup(project, entry['backup'], None, entry['previous_sha256'],
                                     lambda: self._check_dirs(project, trusted))
        except CheckpointError as error:
            out['problems'].append(str(error))
        return out


def tool_description(decl) -> str:
    return ('Validate this step’s project checkpoint after writing its report '
            f'({decl["root"]}/{decl["report"]} with "step_id" and "status": "DONE"). Magazine updates '
            f'{decl["state_file"]} after it accepts the completed cartridge and its published result. '
            'Leave the report unchanged and finish normally. If validation fails, report the reason and end with HALT.')
