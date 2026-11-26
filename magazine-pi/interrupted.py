"""Explicit, non-executing reconciliation of extinct Pi execution owners."""
import json
import os
from pathlib import Path

import beta5_helpers as h
import executor as ex
from magazine.durability import atomic_write

PROC_ROOT = Path('/proc')


def worker_evidence():
    """Fail closed for surviving SDK children, including orphaned workers.

    Legacy active-worker.json has no PID. Any Magazine SDK worker in this
    PID namespace therefore blocks reconciliation, even if it might belong to
    another run. No signals, network calls, or provider requests are made.
    """
    workers = []
    try:
        if not (PROC_ROOT / str(os.getpid()) / 'cmdline').read_bytes():
            raise OSError('procfs self identity unavailable')
        for entry in PROC_ROOT.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                args = (entry / 'cmdline').read_bytes().split(b'\0')
            except FileNotFoundError:
                continue  # exited during inspection
            if any(Path(os.fsdecode(arg)).name in ('worker.mjs', 'sandbox.py') for arg in args if arg):
                workers.append(int(entry.name))
    except (OSError, ValueError) as exc:
        return {'status': 'uncertain', 'pids': workers, 'detail': str(exc)}
    return {'status': 'present' if workers else 'absent', 'pids': workers}


def diagnostics(service, owner, rid):
    m = h._mag()
    try:
        claim = m['em'].load_execution_metadata(str(Path(h._state_path(owner, rid)).with_name('execution.json')))
        state = m['ss'].load_run_state(h._state_path(owner, rid))
        identity = m['ri'].get_runtime_identity()
        same = claim is not None and claim.runtime_id == identity.runtime_id
        extinct = bool(claim and not same and m['ri'].process_is_extinct(claim.pid, claim.start_ticks))
        workers = worker_evidence() if extinct else {'status': 'not_checked', 'pids': []}
        eligible = bool(extinct and workers['status'] == 'absent' and not service.has_task(owner, rid)
                        and state['status'] in ('RUNNING', 'HALTED')
                        and claim.halt_reason in (None, 'lost-owner'))
        reason = ('Recorded execution process is positively extinct; no Magazine SDK worker was found.' if eligible else
                  'A local task is still active.' if service.has_task(owner, rid) else
                  'No execution claim exists; ownership cannot be established.' if claim is None else
                  'This service owns the execution; use Stop workflow.' if same else
                  'The recorded process is live or its identity is uncertain.' if not extinct else
                  'SDK worker presence is unresolved: ' + workers['status'] if workers['status'] != 'absent' else
                  'This state or claim is not eligible for interrupted-run reconciliation.')
        return {'eligible': eligible, 'owner_extinct': extinct, 'same_runtime': same,
                'pid': claim.pid if claim else None, 'start_ticks': claim.start_ticks if claim else None,
                'workers': workers, 'detail': reason,
                'inference_cancellation': 'unconfirmed', 'external_operations': 'unknown',
                'message': 'Reconciliation halts Magazine only. Provider cancellation and external tool completion are unconfirmed. No inference is started.'}
    except Exception as exc:
        return {'eligible': False, 'owner_extinct': False, 'detail': 'Ownership evidence is unreadable: ' + str(exc),
                'inference_cancellation': 'unconfirmed', 'external_operations': 'unknown'}


def reconcile(service, owner, rid, *, retire_schedule=True):
    """Caller serializes service actions; all run writes use the core fence.

    Each durable stage is retryable. Scheduler removal does not pump the queue.
    A crash between the core HALT and sidecar writes leaves HALTED, never READY.
    """
    evidence = diagnostics(service, owner, rid)
    if not evidence['eligible']:
        raise ValueError(evidence['detail'])
    m = h._mag()
    identity = m['ri'].get_runtime_identity()
    state_path, lock_path = h._state_path(owner, rid), h._lock_path(owner, rid)
    state = m['ss'].load_run_state(state_path)
    outcome = m['racton'].recover_lost_runtime(state_path, lock_path, owner,
        identity.runtime_id, identity.pid, identity.start_ticks, expected_status=state['status'])
    if outcome.outcome != 'halted':
        raise ValueError('Reconciliation refused after ownership revalidation: ' + outcome.outcome)
    m['rl'].acquire_run_lock(lock_path, owner, identity.runtime_id, identity.pid, identity.start_ticks)
    try:
        base = Path(h._run_base(owner, rid))
        claim = m['em'].load_execution_metadata(str(Path(state_path).with_name('execution.json')))
        if claim != outcome.claim or m['ss'].load_run_state(state_path)['status'] != 'HALTED':
            raise ValueError('Execution changed before reconciliation bookkeeping')
        # Repeat the process check while holding the run lock. Fails closed if
        # a worker appears or evidence becomes unreadable between snapshots.
        if not diagnostics(service, owner, rid)['eligible']:
            raise ValueError('Ownership or worker evidence changed during reconciliation')
        ex.reconcile(base, False)
        active = base / 'active-worker.json'
        if active.exists():
            value = json.loads(active.read_bytes())
            value['phase'] = 'interrupted'
            atomic_write(str(active), (json.dumps(value) + '\n').encode())
        # No schedule()/finished(): reconciling one run must never start another.
        service.scheduler.reconcile_interrupted(rid, retain=not retire_schedule)
        receipt = {'version': 1, 'execution_attempt': claim.attempt, 'runtime_id': claim.runtime_id,
                   'owner_extinct': True, 'inference_cancellation': 'unconfirmed',
                   'external_operations': 'unknown', 'status': 'HALTED'}
        receipt['scheduler_entry'] = 'removed' if retire_schedule else 'retained-needs-start'
        atomic_write(str(base / 'reconciliation.json'), (json.dumps(receipt) + '\n').encode())
    finally:
        m['rl'].release_run_lock(lock_path, owner, identity.runtime_id, identity.pid, identity.start_ticks)
    return {'run_id': rid, 'status': 'reconciled', 'state': 'HALTED',
            'message': 'Interrupted run reconciled to HALTED. Nothing was started. Inference cancellation and external operation completion remain unconfirmed.'}
