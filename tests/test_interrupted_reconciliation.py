"""Lost-owner regression: real persistence/locks, synthetic state, no LLMs."""
import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import beta5_helpers as h
import interrupted
import scheduler as sch
from magazine import execution_meta as em, recovery_action as ra, run_control as rc
from magazine import runtime_identity as ri
from magazine.run_lock import acquire_run_lock, release_run_lock
from service import ApiError
from test_pi8_features import service, call, make_run, add_attempt  # noqa: F401
from test_pi6_integration import env, prepare, attempts  # noqa: F401


def orphan(svc, *, stop=False):
    rid, base, ids = make_run(svc, status='READY')
    state = {'status': 'RUNNING', 'current': ids[0], 'todo': ids[1:], 'done': [], 'revision': 1}
    (base/'run/state.json').write_text(json.dumps(state))
    claim = em.ExecutionMetadata(attempt=1, runtime_id='a'*32, pid=1073741824,
        start_ticks=1, created_at=em.utc_now_iso(), halted_by=None, halted_at=None, halt_reason=None)
    em.save_execution_metadata(str(base/'run/execution.json'), claim)
    add_attempt(base, rid, ids[0], status='running')
    (base/'active-worker.json').write_text(json.dumps({'session_id': 'saved-session', 'phase': 'streaming'}))
    svc.scheduler.admit(rid, 'local', 0)
    svc.scheduler.freeze()
    svc.scheduler = sch.Scheduler(svc.root, svc._launch)
    if stop:
        identity = ri.RuntimeIdentity(claim.runtime_id, claim.pid, claim.start_ticks)
        rc.persist_request_stop(str(base/'run/state.json'), h._lock_path('local', rid), 'local', 1, identity)
    return rid, base, claim


@pytest.mark.parametrize('action', ['reconcile', 'stop', 'start'])
def test_running_without_stop_can_be_reconciled_without_dispatch(service, monkeypatch, action):
    rid, base, original = orphan(service)
    other, other_base, _ = make_run(service, name='Unrelated work')
    before = {p.relative_to(other_base): p.read_bytes() for p in other_base.rglob('*') if p.is_file()}
    async def forbidden(*args, **kwargs):
        raise AssertionError('recovery started inference')
    service._launch = forbidden
    service.scheduler.launcher = forbidden
    assert call(service, 'status', run_id=rid)['reconciliation']['eligible']
    if action == 'start':
        with pytest.raises(ApiError, match='HALTED'):
            call(service, action, run_id=rid)
    else:
        assert call(service, action, run_id=rid)['status'] == 'reconciled'
    status = call(service, 'status', run_id=rid)
    assert status['status'] == 'HALTED' and status['done'] == []
    assert status['current_cartridge'] == 'c001' and status['control'] is None
    assert status['execution']['halt_reason'] == 'lost-owner'
    assert status['queue']['queued'] == (action == 'start') and not status['active']
    claim = em.load_execution_metadata(str(base/'run/execution.json'))
    assert (claim.attempt, claim.runtime_id, claim.pid, claim.start_ticks) == (original.attempt, original.runtime_id, original.pid, original.start_ticks)
    assert json.loads((base/'active-worker.json').read_text())['phase'] == 'interrupted'
    first = {p.name: p.read_bytes() for p in (base/'run').iterdir() if p.is_file()}
    assert call(service, 'reconcile', run_id=rid)['status'] == 'reconciled'
    assert first == {p.name: p.read_bytes() for p in (base/'run').iterdir() if p.is_file()}
    assert sch.Scheduler(service.root, forbidden).view()['queue'] == []
    assert before == {p.relative_to(other_base): p.read_bytes() for p in other_base.rglob('*') if p.is_file()}
    assert call(service, 'delete_preview', run_id=rid)['recoverable']


@pytest.mark.parametrize('kind', ['same_runtime', 'live_other', 'ambiguous', 'no_claim', 'worker', 'proc_unreadable'])
def test_uncertain_ownership_and_surviving_workers_refuse_without_mutation(service, monkeypatch, kind):
    rid, base, claim = orphan(service)
    if kind in ('same_runtime', 'live_other'):
        current = ri.get_runtime_identity()
        claim = replace(claim, pid=current.pid, start_ticks=current.start_ticks,
                        runtime_id=current.runtime_id if kind == 'same_runtime' else 'b'*32)
        em.save_execution_metadata(str(base/'run/execution.json'), claim)
    elif kind == 'ambiguous':
        monkeypatch.setattr(ri, '_linux_proc_is_reliable', lambda: False)
    elif kind == 'no_claim':
        (base/'run/execution.json').unlink()
    elif kind == 'worker':
        monkeypatch.setattr(interrupted, 'worker_evidence', lambda: {'status': 'present', 'pids': [123]})
    else:
        monkeypatch.setattr(interrupted, 'PROC_ROOT', base/'missing-proc')
    before = {p.relative_to(base): p.read_bytes() for p in base.rglob('*') if p.is_file()}
    with pytest.raises((ApiError, ValueError)):
        call(service, 'reconcile', run_id=rid)
    assert before == {p.relative_to(base): p.read_bytes() for p in base.rglob('*') if p.is_file()}


def test_pid_reuse_is_extinct_and_stale_lock_uses_normal_takeover(service):
    rid, base, claim = orphan(service)
    current = ri.get_runtime_identity()
    reused = replace(claim, pid=current.pid, start_ticks=current.start_ticks+1)
    em.save_execution_metadata(str(base/'run/execution.json'), reused)
    # A stale lock is retired exclusively by acquire_run_lock's existing
    # inode/flock/PID-incarnation checks, never by the reconciliation adapter.
    acquire_run_lock(h._lock_path('local', rid), 'local', reused.runtime_id, reused.pid, reused.start_ticks)
    assert call(service, 'reconcile', run_id=rid)['status'] == 'reconciled'
    assert not Path(h._lock_path('local', rid)).exists()


def test_live_lock_refuses_reconciliation(service):
    rid, base, claim = orphan(service)
    ident = ri.get_runtime_identity()
    lock = h._lock_path('local', rid)
    acquire_run_lock(lock, 'local', ident.runtime_id, ident.pid, ident.start_ticks)
    try:
        with pytest.raises(ApiError, match='locked'):
            call(service, 'reconcile', run_id=rid)
        assert json.loads((base/'run/state.json').read_text())['status'] == 'RUNNING'
    finally:
        release_run_lock(lock, 'local', ident.runtime_id, ident.pid, ident.start_ticks)


@pytest.mark.parametrize('cancel', ['NOT_ATTEMPTED', 'FAILED', 'CONFIRMED'])
def test_interrupted_stop_preserves_actual_cancellation_evidence(service, cancel):
    rid, base, claim = orphan(service, stop=True)
    if cancel != 'NOT_ATTEMPTED':
        rc.persist_cancellation_result(str(base/'run/state.json'), h._lock_path('local', rid), 'local', 1,
            ri.RuntimeIdentity(claim.runtime_id, claim.pid, claim.start_ticks),
            confirmed=cancel == 'CONFIRMED', detail='fixture provider evidence')
    call(service, 'reconcile', run_id=rid)
    status = call(service, 'status', run_id=rid)
    assert status['display_status'] == 'HALTED'
    assert status['control']['cancellation_status'] == cancel
    assert status['control']['termination_status'] == 'NOT_APPLIED'
    assert status['control']['termination_method'] == 'lost-owner'
    assert call(service, 'delete_preview', run_id=rid)['recoverable']
    with pytest.raises(ApiError):
        call(service, 'recover', run_id=rid)  # STOP remains permanent


@pytest.mark.parametrize('stage', ['claim', 'stop', 'ledger', 'scheduler', 'receipt'])
def test_retry_finishes_partial_durable_reconciliation(service, monkeypatch, stage):
    rid, base, claim = orphan(service, stop=True)
    def fail(*args, **kwargs):
        raise OSError('simulated interrupted write')
    with monkeypatch.context() as patch:
        if stage == 'claim':
            patch.setattr(ra, 'save_execution_metadata', fail)
        elif stage == 'stop':
            patch.setattr(rc, '_settle_not_applied_locked', fail)
        elif stage == 'ledger':
            patch.setattr(interrupted.ex, 'reconcile', fail)
        elif stage == 'scheduler':
            patch.setattr(service.scheduler, 'reconcile_interrupted', fail)
        else:
            original = interrupted.atomic_write
            patch.setattr(interrupted, 'atomic_write', lambda path, data: fail() if path.endswith('reconciliation.json') else original(path, data))
        with pytest.raises(OSError):
            call(service, 'reconcile', run_id=rid)
    assert json.loads((base/'run/state.json').read_text())['status'] == 'HALTED'
    assert call(service, 'reconcile', run_id=rid)['status'] == 'reconciled'
    assert call(service, 'delete_preview', run_id=rid)['recoverable']


def test_delete_and_restore_reconciled_run_keeps_project_and_unrelated_work(service):
    rid, base, claim = orphan(service)
    unrelated, other, _ = make_run(service, name='Other project')
    call(service, 'reconcile', run_id=rid)
    deleted = call(service, 'delete_run', run_id=rid, confirmation=rid)
    assert deleted['deleted'] and not base.exists() and other.exists()
    call(service, 'restore', trash_id=deleted['trash_id'])
    assert base.exists() and other.exists()
    assert call(service, 'status', run_id=rid)['status'] == 'HALTED'
    assert call(service, 'delete_preview', run_id=rid)['recoverable']
    assert (Path(service.config['cwd'])/'must-keep.txt').read_text() == 'owner project is not run storage'


@pytest.mark.skipif(not os.environ.get('MAGAZINE_PI_SDK'), reason='requires real SDK with scripted loopback provider')
def test_sigkill_then_explicit_reconcile_never_dispatches_or_retries(env):
    import time
    rpc = env.rpc()
    rid = prepare(rpc, 'SCRIPT hold-once\ninterrupted cartridge', 'SCRIPT ok\nmust not start')
    rpc.call('start', {'run_id': rid})
    assert env.provider.holding.wait(30)
    # Active ownership must refuse reconciliation; normal Stop is unchanged.
    assert rpc.call('reconcile', {'run_id': rid}, ok=False)['code'] == 409
    rpc = env.restart(kill=True)
    env.provider.hold.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = rpc.call('status', {'run_id': rid})
        if status['reconciliation']['eligible']:
            break
        time.sleep(.05)
    assert status['reconciliation']['eligible'], status['reconciliation']
    before = len(env.provider.requests)
    assert rpc.call('reconcile', {'run_id': rid})['status'] == 'reconciled'
    assert rpc.call('reconcile', {'run_id': rid})['status'] == 'reconciled'
    assert rpc.call('start', {'run_id': rid}, ok=False)['code'] == 409
    rpc = env.restart()
    assert rpc.call('status', {'run_id': rid})['status'] == 'HALTED'
    assert rpc.call('scheduler')['queue'] == []
    assert attempts(rpc, rid, 'c1')[0]['status'] == 'interrupted'
    assert attempts(rpc, rid, 'c2') == []
    assert len(env.provider.requests) == before


def test_actual_surviving_sdk_process_blocks_reconciliation(service):
    rid, base, claim = orphan(service)
    worker = Path(__file__).resolve().parents[1]/'magazine-pi/worker.mjs'
    child = subprocess.Popen(['node', str(worker)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
    try:
        assert child.poll() is None
        with pytest.raises(ApiError, match='SDK worker presence'):
            call(service, 'reconcile', run_id=rid)
        assert json.loads((base/'run/state.json').read_text())['status'] == 'RUNNING'
    finally:
        child.communicate(timeout=10)


def test_scheduler_failure_retains_slot_and_cannot_admit_duplicate(service, monkeypatch):
    rid, base, claim = orphan(service)
    def fail(*args, **kwargs):
        raise OSError('simulated scheduler EIO')
    with monkeypatch.context() as patch:
        patch.setattr(sch, 'atomic_write', fail)
        with pytest.raises(sch.SchedulerError):
            call(service, 'reconcile', run_id=rid)
    assert service.scheduler.describe(rid)['recovery_required']
    with pytest.raises(ApiError):
        call(service, 'start', run_id=rid)
    # A genuine journal failure remains fail-closed until service restart.
    service.scheduler = sch.Scheduler(service.root, service._launch)
    assert call(service, 'reconcile', run_id=rid)['status'] == 'reconciled'
