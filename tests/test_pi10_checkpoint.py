"""Checkpoint transaction, application boundary and restart regressions."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path

import pytest

import checkpoint as cpt
import confined_fs as fs
import protocol

STEPS = ['c001', 'c002', 'c003']
RESULT_SHA = '01' * 32
POSIX = pytest.mark.skipif(os.name != 'posix', reason='requires Linux descriptor-relative filesystem operations')


@pytest.fixture
def transaction_storage(monkeypatch):
    """Use real descriptors on Linux and a file-backed storage double on Windows.

    The Windows run validates recovery decisions and ordering. Path confinement
    tests explicitly require the real Linux runtime.
    """
    if os.name == 'posix':
        return

    @contextmanager
    def open_roots(book, exclusive=False):
        fs.permitted(book.root, book.allowed, book.protected)
        yield book.root, book.run_base

    @contextmanager
    def child(parent, name, create=False):
        path = parent / name
        if create:
            path.mkdir(exist_ok=True)
        if not path.is_dir():
            raise FileNotFoundError(path)
        yield path

    def read(parent, name, limit):
        raw = (parent / name).read_bytes()
        if len(raw) > limit:
            raise ValueError('Too large')
        return raw

    def replace(parent, name, data, guard=None):
        if guard:
            guard()
        (parent / name).write_bytes(data)

    monkeypatch.setattr(cpt.Checkpoints, '_open', open_roots)
    monkeypatch.setattr(cpt.Checkpoints, '_check_dirs', lambda *_: None)
    monkeypatch.setattr(fs, 'same_directory', lambda *_: None)
    monkeypatch.setattr(fs, 'child_directory', child)
    monkeypatch.setattr(fs, 'read_plain', read)
    monkeypatch.setattr(fs, 'replace_plain', replace)
    monkeypatch.setattr(cpt.os, 'fsync', lambda *_: None)


def settings(root, **extras):
    decl = cpt.parse_declaration({'root': '/project/.checkpoint', **extras}, STEPS)
    return dict(decl, root=str(root))


def checkpoint(root, run, **extras):
    return cpt.Checkpoints(settings(root, **extras), run, workflow_identity={'run_id': 'sample',
                           'workflow_sha256': '12' * 32, 'protocol_version': 3},
                           allowed_roots=[root.parent], clock=lambda: 1800000000)


def report(root, step, **extra):
    path = root / f'{step}.json'
    path.write_bytes(cpt.encoded({'step_id': step, 'status': 'DONE', **extra}))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage(cp, step, attempt='a'):
    return cp.stage(step, attempt * 32)


def commit(cp, step, attempt='a'):
    return cp.commit(stage(cp, step, attempt), RESULT_SHA)


@pytest.fixture
def roots(tmp_path):
    root, run = tmp_path / 'project' / 'checkpoints', tmp_path / 'trusted' / 'run'
    root.mkdir(parents=True)
    run.mkdir(parents=True)
    return root, run


@pytest.mark.parametrize('bad', [
    {'root': 'relative'}, {'root': '//unc/share'}, {'root': '/root/../outside'},
    {'root': '/p', 'report': '{step}{danger}.json'}, {'root': '/p', 'report': '../{step}.json'},
    {'root': '/p', 'steps': ['absent']}, {'root': '/p', 'required': 'false'},
    {'root': '/p', 'baseline_step': 'c001'}, {'root': '/p', 'baseline_sha256': RESULT_SHA},
    {'root': '/p', 'state_file': 'c001.json'}, {'root': '/p', 'state_file': 'backup'},
])
def test_invalid_checkpoint_declarations(bad):
    with pytest.raises(ValueError):
        cpt.parse_declaration(bad, STEPS)


def test_saved_checkpoint_fails_with_wrong_mode():
    with pytest.raises(protocol.ProtocolError, match='result_file_v1'):
        protocol.validate_saved_extensions({'version': 2, 'handover_mode': 'legacy_text',
                                            'project_checkpoint': {'root': '/p'}}, STEPS)


def test_stage_and_commit_are_distinct(roots, transaction_storage):
    root, run = roots
    cp = checkpoint(root, run)
    digest = report(root, 'c001')
    proof = stage(cp, 'c001')
    assert proof['report_sha256'] == digest
    assert not (root / 'state.json').exists() and not (run / cpt.RECORD).exists()
    assert cp.commit(proof, RESULT_SHA)['committed'] == 'c001'
    assert cp.commit(proof, RESULT_SHA)['already_committed'] is True
    cp.admission(['c001'])
    with pytest.raises(cpt.CheckpointError):
        cp.commit(proof, '23' * 32)


def test_state_change_refuses_progress(roots, transaction_storage):
    root, run = roots
    cp = checkpoint(root, run)
    report(root, 'c001')
    commit(cp, 'c001')
    report(root, 'c002')
    proof = stage(cp, 'c002', 'b')
    (root / 'state.json').write_bytes(b'{"extra": true}')
    before = (run / cpt.RECORD).read_bytes()
    with pytest.raises(cpt.CheckpointError, match='changed outside the checkpoint tool'):
        cp.commit(proof, RESULT_SHA)
    assert (run / cpt.RECORD).read_bytes() == before


def test_project_state_cannot_authorise_an_initial_commit(roots, transaction_storage):
    root, run = roots
    cp = checkpoint(root, run)
    (root / 'state.json').write_bytes(b'{}')
    with pytest.raises(cpt.CheckpointError, match='unverifiable'):
        cp.admission([])
    assert not (run / cpt.RECORD).exists()


@pytest.mark.parametrize('transition', ['pending', 'backup', 'state', 'committed'])
def test_restart_recovers_the_same_validated_attempt(roots, transaction_storage, monkeypatch, transition):
    root, run = roots
    cp = checkpoint(root, run)
    report(root, 'c001')
    commit(cp, 'c001')
    report(root, 'c002')
    proof = stage(cp, 'c002', 'b')
    real = fs.replace_plain

    def interrupt(parent, name, data, *args):
        real(parent, name, data, *args)
        record_phase = ('pending' if json.loads(data)['pending'] else 'committed') if name == cpt.RECORD else None
        phase = record_phase or ('state' if name == 'state.json' else 'backup')
        if transition == phase:
            raise OSError('simulated power loss')

    with monkeypatch.context() as fixture:
        fixture.setattr(fs, 'replace_plain', interrupt)
        with pytest.raises((OSError, cpt.CheckpointError)):
            cp.commit(proof, RESULT_SHA)
    restarted = checkpoint(root, run)
    with pytest.raises(cpt.CheckpointError):
        restarted.commit({**proof, 'attempt_id': 'c' * 32}, RESULT_SHA)
    assert restarted.commit(proof, RESULT_SHA)['committed'] == 'c002'
    assert restarted.commit(proof, RESULT_SHA)['already_committed']
    restarted.admission(['c001', 'c002'])
    assert len(list((root / 'backup').glob('state.json.before-*'))) == 1


def test_modified_older_report_is_detected(roots, transaction_storage):
    root, run = roots
    cp = checkpoint(root, run)
    report(root, 'c001')
    commit(cp, 'c001')
    report(root, 'c002')
    commit(cp, 'c002', 'b')
    report(root, 'c001', edited=True)
    with pytest.raises(cpt.CheckpointError, match='c001.*does not match'):
        cp.admission(['c001', 'c002'])


def test_skip_and_non_done_are_refused(roots, transaction_storage):
    root, run = roots
    cp = checkpoint(root, run)
    report(root, 'c002')
    with pytest.raises(cpt.CheckpointError, match='previously committed workflow step'):
        stage(cp, 'c002')
    report(root, 'c001', status='HALT')
    with pytest.raises(cpt.CheckpointError, match='status DONE'):
        stage(cp, 'c001')


@pytest.mark.parametrize('record', [b'{', b'{"last_step":"c001"}'])
def test_bad_or_legacy_journal_is_preserved(roots, transaction_storage, record):
    root, run = roots
    (run / cpt.RECORD).write_bytes(record)
    with pytest.raises(cpt.CheckpointError, match='journal|history'):
        checkpoint(root, run).admission([])
    assert (run / cpt.RECORD).read_bytes() == record


def test_reviewed_baseline_must_match_exact_bytes(roots, transaction_storage):
    root, run = roots
    report_sha = report(root, 'seed')
    initial = cpt.encoded({'last_committed_step': 'seed', 'last_report_hash_sha256': report_sha,
                           'completed_steps': ['seed']})
    (root / 'state.json').write_bytes(initial)
    with pytest.raises(cpt.CheckpointError, match='unverifiable'):
        checkpoint(root, run, baseline_step='seed').admission([])
    cp = checkpoint(root, run, baseline_step='seed', baseline_sha256=cpt.sha256_bytes(initial))
    cp.admission([])
    report(root, 'c001')
    commit(cp, 'c001')
    assert json.loads((root / 'state.json').read_bytes())['completed_steps'] == ['seed', 'c001']


@POSIX
@pytest.mark.parametrize('name', ['state.json', 'c001.json', 'backup'])
def test_symlink_entries_are_rejected(roots, name):
    root, run = roots
    target = run if name == 'backup' else run / name
    if name != 'backup':
        target.write_bytes(b'{}')
    (root / name).symlink_to(target)
    if name != 'c001.json':
        report(root, 'c001')
    with pytest.raises(cpt.CheckpointError):
        stage(checkpoint(root, run), 'c001')
    assert not (run / cpt.RECORD).exists()


@POSIX
def test_hard_links_special_files_and_size_are_rejected(roots):
    root, run = roots
    target = run / 'example'
    target.write_bytes(b'{}')
    path = root / 'c001.json'
    variants = (lambda: os.link(target, path), lambda: os.mkfifo(path),
                lambda: path.write_bytes(b' ' * (cpt.MAX_REPORT + 1)))
    for prepare in variants:
        prepare()
        with pytest.raises(cpt.CheckpointError):
            stage(checkpoint(root, run), 'c001')
        path.unlink()


@POSIX
def test_project_path_replacement_refuses_checkpoint(roots, monkeypatch):
    root, run = roots
    report(root, 'c001')
    cp = checkpoint(root, run)
    proof = stage(cp, 'c001')
    old = root.with_name('original')
    real = cp._save

    def move_project(*args):
        real(*args)
        root.rename(old)
        root.mkdir()

    monkeypatch.setattr(cp, '_save', move_project)
    with pytest.raises(cpt.CheckpointError, match='changed during the operation'):
        cp.commit(proof, RESULT_SHA)
    assert not (root / 'state.json').exists() and not (old / 'state.json').exists()


def test_grant_is_required_and_protected_paths_are_refused(roots, transaction_storage):
    root, run = roots
    for allowed, protected, reason in (([], [], 'outside'), ([root.parent], [root], 'inside Magazine')):
        cp = cpt.Checkpoints(settings(root), run, workflow_identity={}, allowed_roots=allowed,
                             protected_roots=protected)
        with pytest.raises(ValueError, match=reason):
            cp.admission([])
