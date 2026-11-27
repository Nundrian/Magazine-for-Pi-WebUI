"""Pi host composition around the unchanged Magazine core and beta.5 helpers.

Private JSON-lines service, launched only by the native plugin. It binds no port.
"""
import asyncio
import base64
import importlib
import io
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

import beta5_helpers as h
from magazine.durability import atomic_write
from magazine.schema import MagazineSchemaError
from pi_dispatch import PiDispatch
from model_selection import select_models, select_thinking
from run_export import export_run, ExportError, redact
import executor as ex
import handover as ho
import host_tools
import finished_export as fx
import checkpoint as cpt
import scheduler as sch
import ledger as lg
import protocol as proto
import tokens
import ui_support as ui
import interrupted
import integrity
import pi_dispatch as PiDispatchModule
import restore_points as rp
import sandbox

VERSION = '0.1.0-pi.10.5'
CORE = {'ok': True}
for key, module in {'ar':'async_runner','state':'state','ss':'state_store',
    'doc':'document','rp':'run_preparation','ra':'run_action','rl':'run_lock',
    'rs':'result_store','ri':'runtime_identity','racton':'recovery_action',
    'em':'execution_meta','rc':'run_control'}.items():
    CORE[key] = importlib.import_module('magazine.' + module)
# Library previews/imports accept the Magazine Simple manifest superset; every
# other document source is delegated to the unchanged core loader.
CORE['doc'] = proto.LibraryDocuments
h._mag = lambda: CORE
RUN_ID = re.compile(r'[a-f0-9]{12}')


class ApiError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise ApiError(code, message)


def legacy_protocol(document):
    """Runs prepared before pi.6 keep their recorded pi.5 execution semantics."""
    value = proto.default_protocol(document, proto.LEGACY_PI5)
    value['loop_police'] = dict(value['loop_police'], enabled=False)
    value['legacy'] = True
    return value


class Service:
    def __init__(self, root, config):
        self.root = Path(root).absolute()
        self.config = config
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(sys.platform == 'linux', 503, 'Magazine for Pi currently requires Linux')
        for name in ('runs', 'temp', 'preferences'):
            (self.root / name).mkdir(mode=0o700, exist_ok=True)
        tempfile.tempdir = str(self.root / 'temp')
        h._runs_root = lambda: str(self.root / 'runs')
        h._LIBRARY_ROOT_DEFAULT = str(self.root / 'library')
        h._STORAGE_LIBRARY_ROOT_GETTER = lambda: h._LIBRARY_ROOT_DEFAULT
        h._save_meta = self.save_meta
        h._initialize_library_root(h._LIBRARY_ROOT_DEFAULT)
        config['dataDir'] = str(self.root)
        config.setdefault('extensionPaths', [])
        config.setdefault('allowedTools',['read','bash','edit','write','grep','find','ls'])
        self.catalogue_lock = asyncio.Lock()
        self.scheduler = sch.Scheduler(self.root, self._launch)
        self.ui = ui.Support(self)
        self.restore_points = rp.RestorePoints(self)
        self.integrity_events = self.root / 'integrity-events.jsonl'
        # pi.10: trusted broker for workflows that declare host applications (credentials stay in this process).
        self.host = host_tools.HostBroker(self.root, config.get('agentDir'))
        self.preflights = {}
        self.heal(full=True)
        # Scratch folders hold copies of model configuration; remove those left
        # by a crashed service, but only when no worker process can still use them.
        if interrupted.worker_evidence()['status'] == 'absent':
            for stale in (self.root / 'temp').glob('worker-*'):
                if stale.is_dir() and not stale.is_symlink():
                    shutil.rmtree(stale, ignore_errors=True)

    def heal(self, full=False):
        """Put back owner access a confined worker may have removed (chmod is not
        governed by Landlock). Each correction is logged for the operator."""
        try:
            healed = integrity.heal_full(self.root) if full else integrity.heal_tree(self.root)
        except OSError:
            return []
        if healed:
            try:
                with open(self.integrity_events, 'a', encoding='utf-8') as stream:
                    stream.write(json.dumps({'at': h._now(), 'event': 'permissions_restored', 'paths': healed}) + '\n')
            except OSError:
                pass
        return healed

    def save_meta(self, owner, rid, value):
        atomic_write(h._meta_path(owner, rid), (json.dumps(value) + '\n').encode())

    def run_owners(self):
        """Single-operator workspace; retain legacy partitions in place.

        These are storage labels, not credentials. Never accept a namespace or
        user identity from a browser.
        """
        return ['local'] + sorted(p.name for p in (self.root / 'runs').iterdir()
            if re.fullmatch(r'[a-f0-9]{64}', p.name) and p.is_dir() and not p.is_symlink())

    def run_owner(self, rid):
        matches = [owner for owner in self.run_owners()
            if (self.root / 'runs' / owner / rid).is_dir()
            and not (self.root / 'runs' / owner / rid).is_symlink()
            and h._status_payload(owner, rid) is not None]
        require(len(matches) <= 1, 409, 'Ambiguous legacy run ID; original records retained')
        require(bool(matches), 404, 'Run not found')
        return matches[0]

    async def catalogue(self):
        async with self.catalogue_lock:
            value = await PiDispatch(self.config, self.root / 'catalogue').catalogue()
            value['tools'] = [tool for tool in value['tools'] if tool['id'] in self.config['allowedTools']]
            return value

    def parse_document(self, value):
        require(isinstance(value,str) and len(value)<=4*1024*1024,400,'Magazine JSON required')
        try:
            return h._load_package_from_upload('', '', 'json', value)
        except (ValueError,TypeError,MagazineSchemaError) as error:
            raise ApiError(400,str(error)) from error

    # ------------------------------------------------------------ protocol
    def library_protocol(self, project_id):
        """Extended declarations of a library workflow, read from a bounded copy."""
        require(isinstance(project_id, str) and h._LIBRARY_ID.fullmatch(project_id), 400, 'Invalid workflow identifier')
        with h._library_root_fd(h._LIBRARY_ROOT_DEFAULT) as root_fd:
            source = os.open(project_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                with tempfile.TemporaryDirectory(prefix='magazine-protocol-') as scratch:
                    h._library_copy_tree(source, Path(scratch), {'entries': 0, 'files': 0, 'bytes': 0})
                    try:
                        document, value, name = proto.parse_manifest(scratch)
                    except (MagazineSchemaError, OSError, ValueError) as error:
                        raise ApiError(400, h._library_validation_error(error)) from error
                    data = proto.core_package._parse_yaml((Path(scratch) / 'magazine.yaml').read_text(encoding='utf-8'))
                    value['declared_mode'] = 'handover_mode' in data
                    return document, value
            finally:
                os.close(source)

    def protocol_path(self, owner, rid):
        return Path(h._run_base(owner, rid)) / 'protocol.json'

    def load_protocol(self, owner, rid):
        path = self.protocol_path(owner, rid)
        if not path.is_file():
            loaded = CORE['doc'].load_document(h._mag_path(owner, rid))
            return legacy_protocol(loaded.document)
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            require(isinstance(value, dict) and type(value.get('version')) is int
                    and value['version'] in proto.KNOWN_PROTOCOL_VERSIONS, 409,
                    'Saved Magazine protocol is invalid or unsupported on this version')
            if value.get('requires') or value.get('project_checkpoint') is not None:
                doc = CORE['doc'].load_document(h._mag_path(owner, rid)).document
                return proto.validate_saved_extensions(value, [item['id'] for item in doc['cartridges']])
            return value
        except (OSError, ValueError, RecursionError) as exc:
            raise ApiError(409, f'Saved Magazine protocol could not be validated: {exc}') from None

    def save_protocol(self, owner, rid, value):
        atomic_write(str(self.protocol_path(owner, rid)), (json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8'))

    def preference_path(self, project_id):
        require(isinstance(project_id, str) and h._LIBRARY_ID.fullmatch(project_id), 400, 'Invalid workflow identifier')
        return self.root / 'preferences' / (project_id + '.antiloop.json')

    def antiloop_preference(self, project_id):
        if not project_id:
            return None
        path = self.preference_path(project_id)
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
        require(type(value.get('enabled')) is bool, 500, 'Saved Antiloop setting is invalid')
        return value['enabled']

    # ------------------------------------------------------------ dispatch
    async def handle(self, request):
        require(isinstance(request, dict), 400, 'Request must be an object')
        self.heal()
        PiDispatchModule.reap_orphans()  # descendants of finished workers never linger
        action = request.get('action')
        data = request.get('data', {})
        require(isinstance(data, dict), 400, 'Action data must be an object')
        if action == 'catalogue':
            value = await self.catalogue()
            value['magazine_version'] = VERSION
            value['compatibility'] = self.config.get('compatibility')
            return value
        if action == 'library':
            projects = h._library_list(h._LIBRARY_ROOT_DEFAULT)
            if projects['available'] and not projects['workflows'] and not projects['truncated']:
                projects['message'] = 'No Magazine projects yet. Select a project ZIP and click Import ZIP, or load a JSON workflow.'
            return {'projects': projects}
        if action == 'preview':
            value = h._library_preview(h._LIBRARY_ROOT_DEFAULT, data.get('project_id'))
            if value.get('valid'):
                _, protocol = self.library_protocol(data['project_id'])
                value['protocol'] = self.protocol_view(protocol)
                value['path_check'] = self.path_check(protocol.get('required_paths', []), value['document'])
                pref = self.antiloop_preference(data['project_id'])
                value['antiloop'] = protocol['loop_police']['enabled'] if pref is None else pref
                cat = await self.catalogue()
                value['model_plan'] = ui.sidecar(self, data['project_id'], value['name'],
                    [c['id'] for c in value['document']['cartridges']], {m['id']: m for m in cat['models']})
            return value
        if action in ('model_plan', 'planning_brief'):
            if data.get('project_id'):
                context = h._library_preview(h._LIBRARY_ROOT_DEFAULT, data['project_id'])
                require(context.get('valid'), 400, 'Load a valid workflow first')
                document, name = context['document'], context['name']
            else:
                document, _, name = self.parse_document(data.get('magazine', ''))
            cat = await self.catalogue()
            ids = [c['id'] for c in document['cartridges']]
            if action == 'model_plan':
                return {'plan': ui.validate_plan(data.get('plan'), name, ids, {m['id']: m for m in cat['models']})}
            brief = {'workflow_name': name, 'cartridge_ids': ids,
                     'available_models': cat['models'], 'available_tools': cat['tools'],
                     'cartridges': [{'order': i+1, **c} for i, c in enumerate(document['cartridges'])],
                     'instructions': 'Suggest exact Pi catalogue IDs using schema_version 1.1, workflow_name, ordered cartridge_ids, cartridge_models and optional rationale/default_model. Optional default_thinking_level and cartridge_thinking_levels use inherit or the model-specific thinking_levels from available_models. An omitted cartridge level uses the workflow default. Explicit inherit uses Pi settings directly. Omit overrides if capabilities are uncertain. Use task requirements and measured local reliability where provided; otherwise mark reliability unknown. Qwen3-Coder-30B-A3B is non-thinking, not a reasoning model. Do not assume suitability for complex security/planning; prior local tool-call failures are a risk. Model-card claims and the prior 75/40/16/14 task allocation are not comparative local benchmarks. Context/memory/quantisation compatibility must be verified for the actual installation. No remote call is made by Magazine.'}
            return {'brief': json.dumps(brief, ensure_ascii=False, indent=2),
                    'warning': 'This brief contains workflow prompts. Review and remove sensitive content before copying it to another service.'}
        if action == 'restore_points':
            return self.restore_points.list()
        if action == 'delete_restore_points':
            try:
                return self.restore_points.delete(data)
            except rp.RestorePointError as error:
                raise ApiError(error.code, str(error)) from error
        if action == 'worker_isolation':
            return self.isolation_view()
        if action == 'export_settings':
            return self.export_settings_view()
        if action == 'set_export_settings':
            require(set(data) <= {'mode', 'folder'}, 400, 'Only mode and folder are accepted')
            allowed, protected = self.export_roots()
            try:
                fx.set_preferences(self.root, data.get('mode'), data.get('folder'), allowed, protected)
            except ValueError as error:
                raise ApiError(400, str(error)) from error
            return self.export_settings_view()
        if action == 'host_connections':
            return {'services': self.host.summary()}
        if action == 'host_connect':
            require(set(data) <= {'service', 'key'}, 400, 'Only service and key are accepted')
            try:
                return {'services': self.host.configure(data.get('service'), data.get('key'))}
            except host_tools.HostToolError as error:
                raise ApiError(400, str(error)) from error
        if action == 'host_forget':
            return {'services': self.host.forget(data.get('service'))}
        if action == 'host_test':
            service = data.get('service')
            require(service in host_tools.SERVICES, 400, 'Unknown service')
            # host_preflight takes a protocol record; the requirement goes under 'requires'. (RC passed the bare
            # requirement, so the test made no call at all and reported success.)
            result = await self.host_preflight({'requires': host_tools.parse_requires({'services': {service: {}}})},
                                               raise_on_failure=False)
            if not result.get('checks'):
                result = {**result, 'ok': False, 'problems': ['No check was performed']}
            return {'service': service, **result}
        if action == 'chats':
            return self.ui.chats(data)
        if action == 'chat':
            return redact(self.ui.chat(data), self.export_secrets())
        if action == 'delete_template':
            return self.ui.delete_template(data)
        if action == 'trash':
            return self.ui.trash_list()
        if action == 'restore':
            return self.ui.restore(data)
        if action == 'import':
            require(isinstance(data.get('file_name'), str) and isinstance(data.get('file_data_b64'), str), 400, 'ZIP upload required')
            return h._library_import(h._LIBRARY_ROOT_DEFAULT, data['file_name'], data['file_data_b64'], data.get('project_id', ''))
        if action == 'validate':
            require(isinstance(data.get('magazine'), str) and len(data['magazine']) <= 4*1024*1024, 400, 'Magazine JSON required')
            document, _, name = self.parse_document(data.get('magazine'))
            return {'document':document,'name':name,'cartridges':h._normalize_cartridges(document),
                    'protocol': self.protocol_view(proto.default_protocol(document))}
        if action == 'prepare':
            return await self.prepare('local', data)
        if action == 'set_antiloop':
            return self.set_antiloop(data)
        if action == 'view_state':
            return self.view_state(data)
        if action == 'runs':
            return {'runs': self.list_runs(), 'scheduler': self.scheduler.view()}
        if action == 'scheduler':
            return self.scheduler.view()
        if action == 'set_scheduler':
            try:
                self.scheduler.set_mode(data.get('mode'))
            except sch.SchedulerError as error:
                raise ApiError(error.code, str(error)) from error
            return self.scheduler.view()
        rid = data.get('run_id')
        require(isinstance(rid, str) and RUN_ID.fullmatch(rid), 404, 'Run not found')
        owner = self.run_owner(rid)
        payload = h._status_payload(owner, rid)
        require(payload is not None, 404, 'Run not found')
        if action == 'status':
            return self.status(owner, rid)
        if action == 'preflight':
            # Recheck a run's declared dependencies; read-only, never starts anything.
            result = await self.host_preflight(self.load_protocol(owner, rid), raise_on_failure=False)
            self.preflights[rid] = result
            return result
        if action == 'delete_preview':
            require(not ({'owner', 'path'} & set(data)), 400, 'Client paths/owners are not accepted')
            return self.ui.deletion_preview(rid)
        if action == 'delete_run':
            return self.ui.delete_run(data)
        if action == 'results':
            value = h._results_payload(owner, rid)
            value['handovers'] = self.handover_payloads(owner, rid)
            return value
        if action == 'ledger':
            return self.ledger_view(owner, rid)
        if action == 'start':
            return await self.start(owner, rid, payload)
        if action == 'reconcile':
            return self.reconcile_interrupted(owner, rid)
        if action == 'repair_preview':
            try:
                return integrity.plan_repair(self, owner, rid)
            except integrity.IntegrityError as error:
                raise ApiError(409, str(error)) from error
        if action == 'repair_state':
            require(data.get('confirm') is True, 400, 'Confirm the state repair first')
            try:
                return integrity.repair_state(self, owner, rid)
            except integrity.IntegrityError as error:
                raise ApiError(409, str(error)) from error
        if action == 'stop':
            if payload.get('status') == 'error':
                # The controller state cannot be read. Stopping must not depend
                # on it: cancel execution first, then report recovery-required.
                return await self.emergency_stop(owner, rid, payload.get('error'))
            if payload.get('status') == 'RUNNING' and not (payload.get('execution') or {}).get('owner_alive') \
                    and not self.has_task(owner, rid):
                return self.reconcile_interrupted(owner, rid)
            queue = self.scheduler.describe(rid)
            if queue['queued'] and (not queue['entered_core'] or queue['recovery_required']) \
                    and payload.get('status') in ('READY', 'HALTED'):
                # Never entered the core in this generation: leave the run as it is.
                try:
                    self.scheduler.dequeue(rid)
                except sch.SchedulerError as error:
                    raise ApiError(error.code, str(error)) from error
                return {'run_id': rid, 'status': 'removed_from_queue',
                        'message': f"Removed from the queue; the run stays {payload.get('status')}."}
            try:
                code, result = await h._stop_run_decision(owner, rid)
            except Exception as exc:  # unreadable ownership or control evidence
                if self.has_task(owner, rid):
                    return await self.emergency_stop(owner, rid, repr(exc), damaged=False)
                raise ApiError(409, f'Stop could not read the run records: {exc}') from exc
            detail = str(result.get('detail', ''))
            if self.has_task(owner, rid) and (code >= 500 or 'unreadable' in detail.lower()):
                # Work is still active and the durable Stop path failed: cancel
                # execution anyway and report exactly that.
                return await self.emergency_stop(owner, rid, detail, damaged=False)
            require(code < 400, code, result.get('detail', 'Stop refused'))
            if result.get('status') == 'stopped':
                self.scheduler.forget(rid)
            return result
        if action == 'recover':
            require(payload.get('status') != 'error', 409,
                    'The run state is damaged. Use Repair state first; it halts the run without starting anything.')
            protocol = self.load_protocol(owner, rid)
            problems = proto.check_required_paths(protocol.get('required_paths'))
            require(not problems, 409, 'Required project path missing: ' + '; '.join(problems))
            self.preflights[rid] = await self.host_preflight(protocol)
            evidence = interrupted.diagnostics(self, owner, rid)
            require(not self.has_task(owner, rid), 409, 'A local task is still finishing; recovery is not yet safe')
            execution = payload.get('execution') or {}
            require(not execution or execution.get('owner_alive') or evidence['owner_extinct'],
                    409, 'Recorded execution owner is live or uncertain; recovery refused')
            if evidence['eligible']:
                self.reconcile_interrupted(owner, rid, retire_schedule=False)
            await asyncio.to_thread(self.checkpoint_admission, owner, rid, payload, protocol)
            code, result = h._recover_run_decision(owner, rid)
            require(code < 400, code, result.get('detail', 'Recovery refused'))
            return result
        if action == 'export':
            require(payload.get('status') in ('HALTED','COMPLETED'), 409, 'Only HALTED or COMPLETED runs can export')
            await self.settle_terminal(owner, rid)
            await asyncio.to_thread(self.check_export_size, owner, rid, payload)
            mode = data.get('delivery_mode')
            require(mode is None or mode == 'download', 400, 'Use the configured delivery mode or choose download')
            record = await asyncio.to_thread(h._attempt_export, owner, rid)
            out = {'export': record}
            if record.get('status') == 'EXPORTED':
                out.update(await asyncio.to_thread(self.deliver_export, owner, rid, record, mode))
            return out
        if action in ('download', 'download_jsonl'):
            await self.settle_terminal(owner, rid)
        if action == 'download':
            await asyncio.to_thread(self.check_export_size, owner, rid, payload)
            files, manifest, _ = h._export_files(owner, rid)
            files, manifest = fx.redact_delivery(files, manifest, self.export_secrets())
            return {'file_name':f'Magazine-{rid}-results.zip',
                    'file_data_b64':base64.b64encode(self.results_zip(owner, rid, files['results.zip'])).decode(),
                    'manifest':manifest}
        if action == 'download_jsonl':
            try:
                raw = await asyncio.to_thread(export_run, h._run_base(owner, rid), rid,
                    self.export_secrets())
            except ExportError as error:
                raise ApiError(409, str(error)) from error
            return {'file_name':f'magazine-run-{rid}.jsonl',
                    'file_data_b64':base64.b64encode(raw).decode()}
        raise ApiError(400, 'Unknown Magazine action')

    def export_secrets(self):
        # Retired import credentials remain on disk for rollback, never used for
        # access. Continue redacting their exact bytes from historical exports.
        legacy = self.root / 'import-key'
        return (legacy.read_text().strip() if legacy.is_file() and not legacy.is_symlink() else None,
                os.environ.get('PI_WEB_TOKEN'), *self.host.secrets())

    @staticmethod
    def protocol_view(protocol):
        return {'handover_mode': protocol['handover_mode'], 'result_profile': protocol['result_profile'],
                'recovery': protocol['recovery'], 'loop_police': protocol['loop_police'],
                'declared_mode': protocol.get('declared_mode', False),
                'ignored_fields': protocol.get('ignored_fields', []),
                'required_paths': protocol.get('required_paths', []),
                'cartridges': {cid: {k: v for k, v in d.items() if k != 'dir'} for cid, d in protocol['cartridges'].items()}}

    async def settle_terminal(self, owner, rid):
        """A COMPLETED/HALTED run's task may still be writing its automatic
        Finished Work record; let that bookkeeping finish (bounded) so a
        download is not refused as a changing snapshot. Never touches execution."""
        handle = h._RUN_HANDLES.get((owner, rid))
        payload = h._status_payload(owner, rid) or {}
        if handle is None or handle['task'].done() or payload.get('status') not in ('COMPLETED', 'HALTED'):
            return
        try:
            await asyncio.wait_for(asyncio.shield(handle['task']), 10)
        except (asyncio.TimeoutError, Exception):
            pass

    # ------------------------------------------------------ emergency STOP
    async def emergency_stop(self, owner, rid, reason, damaged=True):
        """Stop execution without depending on readable or writable run state.

        Cancellation comes first and is reported exactly as observed. The run
        is never presented as successfully halted: its state needs an explicit
        repair. Evidence is written to a separate file beside the state.
        """
        cancellation = {'active_task': False, 'worker': 'none', 'task_ended': None}
        try:
            self.scheduler.forget(rid)  # nothing further may be dispatched for this run
        except Exception:
            pass
        handle = h._RUN_HANDLES.get((owner, rid))
        if handle is not None and not handle['task'].done():
            cancellation['active_task'] = True
            dispatch = handle['dispatch']
            dispatch.operator_stop = True
            try:
                result = await dispatch.cancel_active()
                cancellation['worker'] = 'abort_acknowledged' if result.confirmed else \
                    ('no_active_worker' if not result.active else 'abort_unconfirmed')
            except Exception as exc:
                cancellation['worker'] = f'abort_failed: {type(exc).__name__}'
            if cancellation['worker'] in ('abort_unconfirmed',) or cancellation['worker'].startswith('abort_failed'):
                terminated = await dispatch.dispatch.terminate()
                cancellation['worker'] = 'terminated' if terminated else 'termination_unconfirmed'
            handle['task'].cancel()
            try:
                await asyncio.wait_for(asyncio.shield(handle['task']), 15)
            except BaseException:
                pass
            cancellation['task_ended'] = handle['task'].done()
        workers = interrupted.worker_evidence()
        record = {'requested_at': h._now(), 'reason': str(reason or '')[:600], **cancellation,
                  'worker_processes_after': workers['status'], 'state': 'recovery_required'}
        try:
            atomic_write(str(Path(h._state_dir(owner, rid)) / 'emergency-stop.json'),
                         (json.dumps(record, indent=2) + '\n').encode(), prefix='.magazine_emergency_')
            record['recorded'] = True
        except OSError as exc:
            record['recorded'] = False
            record['record_error'] = str(exc)
        stopped = cancellation['task_ended'] is not False and cancellation['worker'] not in ('termination_unconfirmed',)
        if not stopped:
            message = 'Stop was attempted but the worker could not be confirmed stopped; inspect the run.'
        elif damaged:
            message = ('Execution stopped. The run state is damaged, so the run was not marked HALTED; '
                       'use Repair state to rebuild it from verified records. Nothing will resume.')
        else:
            message = ('Execution stopped, but the normal Stop record could not be completed ('
                       + str(reason or 'unknown')[:200] + '). Check progress: the run is halted, or offers '
                       'Reconcile or Repair state. Nothing will resume.')
        return {'run_id': rid, 'status': 'emergency_stopped' if stopped else 'stop_unconfirmed', 'emergency': record,
                'message': message}

    # ------------------------------------------------------------ pi.10.2: where Finished Work goes
    def export_roots(self):
        allowed = [self.config['cwd']] + list(self.config.get('workerWritePaths') or []) \
            + list(self.config.get('exportRoots') or [])
        return allowed, PiDispatchModule.protected_roots(self.config)

    def export_settings_view(self):
        allowed, _ = self.export_roots()
        prefs = fx.preferences(self.root)
        mounts = fx.windows_mounts()
        roots = [{'path': a, 'windows': fx.windows_path(a, mounts)} for a in allowed]
        suggestion = os.path.join(self.config['cwd'], 'Magazine exports')
        return {**prefs, 'folder_windows': fx.windows_path(prefs['folder'], mounts) if prefs['folder'] else None,
                'roots': roots, 'suggestion': suggestion, 'suggestion_windows': fx.windows_path(suggestion, mounts)}

    @staticmethod
    def check_export_size(owner, rid, payload):
        try:
            size = sum(Path(CORE['rs'].result_path(h._state_dir(owner, rid), cid)).lstat().st_size
                       for cid in payload.get('done', []))
        except OSError:
            raise ApiError(409, 'A canonical result is unavailable; Finished Work delivery refused') from None
        require(size <= fx.MAX_EXPORT_BYTES // 2, 413,
                'Canonical results exceed the size limit for a single download. Individual results remain available')

    def deliver_export(self, owner, rid, record, mode=None):
        """After the canonical export: hand it to the browser (default) or write it to the chosen folder."""
        files, manifest, _ = h._export_files(owner, rid)
        require((manifest['revision'], manifest['status']) == (record['revision'], record['run_status']), 409,
                'Run changed during export. Retry after it has stopped')
        secrets = self.export_secrets()
        files, manifest = fx.redact_delivery(files, manifest, secrets)
        run_base = h._run_base(owner, rid)
        ledger = lg.load(run_base)
        if ledger is not None and h._load_meta(owner, rid).get('execution_settings'):
            files['magazine-ledger.json'] = (json.dumps(redact(ledger, secrets), ensure_ascii=False,
                                                       indent=2) + '\n').encode('utf-8')
        base = f"Magazine-{fx.slug(manifest.get('project_name'))}-{rid}"
        prefs = fx.preferences(self.root)
        if (mode or prefs['mode']) == 'folder':
            allowed, protected = self.export_roots()
            try:
                saved = fx.deliver_to_folder(prefs['folder'], base, files, allowed=allowed, protected=protected)
            except (ValueError, OSError) as error:
                return {'delivery': {'mode': 'folder', 'ok': False, 'error': str(error)}}
            return {'delivery': {'mode': 'folder', 'ok': True, **saved,
                                 'windows_path': fx.windows_path(saved['container_path'])}}
        try:
            data = fx.zip_bytes(files)
        except ValueError as error:
            return {'delivery': {'mode': 'download', 'ok': False, 'error': str(error)}}
        return {'delivery': {'mode': 'download', 'ok': True, 'file_name': base + '.zip',
                             'file_data_b64': base64.b64encode(data).decode()}}

    async def host_preflight(self, protocol, raise_on_failure=True):
        """pi.10 dependency preflight: authenticated checks of declared host applications. Ordinary workflows
        (no ``requires``) pass without any network call."""
        requires = (protocol or {}).get('requires') or {}
        if not requires.get('services'):
            return {'ok': True, 'checks': [], 'problems': [], 'declared': False}
        result = await asyncio.to_thread(self.host.preflight, requires)
        result['declared'] = True
        if raise_on_failure and not result['ok']:
            raise ApiError(409, 'Dependency check failed; nothing was started: ' + '; '.join(result['problems'])
                           + ' Fix this, then use Recheck dependencies or Start again.')
        return result

    def path_check(self, required, document=None):
        problems = proto.check_required_paths(required)
        hints = proto.windows_path_hints(document) if document else []
        return {'required': required, 'missing': problems,
                'windows_paths': hints,
                'note': ('This workflow names Windows paths. Workers can use only folders mounted into the Pi '
                         'container; add the project folder as a bind mount and list its container path in '
                         'required_paths so a missing mount stops the run before it starts.') if hints else None}

    def isolation_view(self):
        abi = sandbox.abi_version()
        mode = self.config.get('workerSandbox', 'required')
        return {'mode': mode, 'landlock_abi': abi, 'enforced': mode == 'required' and abi >= sandbox.MIN_ABI,
                'workspace': self.config.get('cwd'), 'extra_write_paths': self.config.get('workerWritePaths') or [],
                'extra_read_paths': self.config.get('workerReadPaths') or [],
                'limits': ['Network access is not restricted (Landlock network rules need kernel ABI 4).',
                           'Before kernel ABI 6, workers can still signal (for example stop or kill) other '
                           'processes of the same user, including Magazine and Pi.',
                           'Workers cannot open Magazine files but can still change their permissions or times; '
                           'Magazine detects and restores permissions.',
                           'Any Pi host API reachable without a token remains reachable; keep PI_WEB_TOKEN set.']}

    # -------------------------------------------------------------- views
    def has_task(self, owner, rid):
        handle = h._RUN_HANDLES.get((owner, rid))
        return handle is not None and not handle['task'].done()

    def reconcile_interrupted(self, owner, rid, *, retire_schedule=True):
        try:
            return interrupted.reconcile(self, owner, rid, retire_schedule=retire_schedule)
        except ValueError as exc:
            raise ApiError(409, str(exc)) from exc

    def display_status(self, payload, rid=None):
        status = payload.get('status')
        if rid is not None:
            queue = self.scheduler.describe(rid)
            if queue['queued'] and not queue['recovery_required'] and status in ('READY', 'RUNNING') \
                    and not (isinstance(payload.get('control'), dict) and payload['control'].get('request') == 'STOP'):
                return 'QUEUED'
        control = payload.get('control') if isinstance(payload.get('control'), dict) else None
        if control and control.get('request') == 'STOP':
            if status == 'HALTED' and control.get('termination_status') == 'CONFIRMED':
                return 'STOPPED'
            if status in ('RUNNING', 'READY') or control.get('termination_status') == 'REQUESTED':
                return 'STOP_REQUESTED'
        return status

    def legacy_tokens(self, owner, rid):
        """Pre-pi.6 runs: usage from retained sessions; association not recorded."""
        folder = Path(h._run_base(owner, rid)) / 'sessions'
        snaps = []
        if folder.is_dir() and not folder.is_symlink():
            for path in sorted(folder.glob('*.jsonl')):
                if path.is_file() and not path.is_symlink():
                    snaps.append(tokens.from_session(path, finished_normally=True))
        value = tokens.total(snaps)
        value['source'] = 'retained_sessions_without_ledger'
        return value

    def damaged_status(self, owner, rid, payload):
        """A run whose controller state cannot be read: a complete, honest view."""
        meta = h._load_meta(owner, rid)
        base = h._run_base(owner, rid)
        try:
            ledger = lg.load(base)
        except ValueError:
            ledger = None
        try:
            order = [c['id'] for c in CORE['doc'].load_document(h._mag_path(owner, rid)).document['cartridges']]
        except Exception:
            order = list((ledger or {}).get('order') or [])
        emergency = None
        try:
            emergency = json.loads((Path(h._state_dir(owner, rid)) / 'emergency-stop.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        try:
            protocol = self.load_protocol(owner, rid)
        except Exception:
            protocol = {'handover_mode': None, 'recovery': {}, 'loop_police': {'enabled': False}}
        payload.update(status='STATE_INVALID', display_status='STATE_INVALID', state_error=payload.get('error'),
                       done=[], todo=[], current_cartridge=None, order=order, name=meta.get('name'),
                       project_id=meta.get('project_id'), cartridge_models=meta.get('cartridge_models', {}),
                       created_at=meta.get('created_at'), model_map=meta.get('model_map'), tool_ids=meta.get('tool_ids'),
                       export=None, queue=self.scheduler.describe(rid), ledger=lg.summary(ledger),
                       tokens=(ledger or {}).get('tokens') or tokens.total([]), active=self.has_task(owner, rid),
                       reconciliation=None, execution=None, control=None, emergency_stop=emergency,
                       protocol={'handover_mode': protocol.get('handover_mode'), 'legacy': protocol.get('legacy', False),
                                 'recovery': protocol.get('recovery'), 'antiloop': protocol['loop_police'].get('enabled'),
                                 'antiloop_frozen': protocol.get('antiloop_frozen', False)},
                       repair={'available': not self.has_task(owner, rid),
                               'detail': 'Magazine cannot read this run\'s controller state. Stop is still available; '
                                         'Repair state keeps the damaged file as evidence and rebuilds a HALTED state '
                                         'from Magazine\'s verified records without starting anything.'})
        return payload

    def status(self, owner, rid):
        payload = h._status_payload(owner, rid)
        if not payload:
            return payload
        if payload.get('status') == 'error':
            return self.damaged_status(owner, rid, payload)
        meta = h._load_meta(owner, rid)
        base = h._run_base(owner, rid)
        try:
            evidence = interrupted.diagnostics(self, owner, rid)
            value = ex.reconcile(base, self.has_task(owner, rid) or not evidence['owner_extinct'])
            value = value or lg.load(base)
        except ValueError:
            value = None
        protocol = self.load_protocol(owner, rid)
        payload['name'] = meta.get('name')
        payload['project_id'] = meta.get('project_id')
        payload['cartridge_models'] = meta.get('cartridge_models',{})
        payload['execution_settings'] = meta.get('execution_settings')
        payload['export'] = h._export_record(owner,rid)
        payload['display_status'] = self.display_status(payload, rid)
        payload['queue'] = self.scheduler.describe(rid)
        payload['protocol'] = {'handover_mode': protocol['handover_mode'], 'legacy': protocol.get('legacy', False),
                               'recovery': protocol['recovery'], 'antiloop': protocol['loop_police']['enabled'],
                               'antiloop_frozen': protocol.get('antiloop_frozen', False)}
        payload['ledger'] = lg.summary(value)
        payload['tokens'] = (value or {}).get('tokens') or (self.legacy_tokens(owner, rid) if protocol.get('legacy') else tokens.total([]))
        payload['active'] = self.has_task(owner, rid)
        payload['reconciliation'] = interrupted.diagnostics(self, owner, rid)
        payload['order'] = [c['id'] for c in CORE['doc'].load_document(h._mag_path(owner, rid)).document['cartridges']]
        payload['required_paths'] = self.path_check(protocol.get('required_paths', []))
        if protocol.get('requires'):
            payload['dependencies'] = {'declared': protocol['requires'], 'last_check': self.preflights.get(rid)}
        if protocol.get('project_checkpoint'):
            payload['project_checkpoint'] = {'root': protocol['project_checkpoint']['root'],
                                             'required': protocol['project_checkpoint']['required']}
        payload['isolation'] = {'mode': self.config.get('workerSandbox', 'required'), 'landlock_abi': sandbox.abi_version()}
        return payload

    def list_runs(self):
        runs = []
        for owner in self.run_owners():
            directory = self.root / 'runs' / owner
            if not directory.is_dir() or directory.is_symlink():
                continue
            for run in directory.iterdir():
                if RUN_ID.fullmatch(run.name) and run.is_dir() and not run.is_symlink():
                    require(not any(r['run_id'] == run.name for r in runs), 409,
                            'Ambiguous legacy run ID; original records retained')
                    payload = h._status_payload(owner, run.name)
                    if not payload:
                        continue
                    meta = h._load_meta(owner, run.name)
                    try:
                        value = lg.load(h._run_base(owner, run.name))
                    except ValueError:
                        value = None
                    self.reconcile_queue(run.name, payload)
                    done, todo = payload.get('done', []), payload.get('todo', [])
                    current = payload.get('current_cartridge') or (todo[0] if todo else None)
                    models = meta.get('cartridge_models') or {}
                    record = ((value or {}).get('cartridges') or {}).get(current) or {}
                    tokens_value = (value or {}).get('tokens')
                    invalid = payload['status'] == 'error'
                    runs.append({'run_id': run.name, 'status': 'STATE_INVALID' if invalid else payload['status'],
                                 'display_status': 'STATE_INVALID' if invalid else self.display_status(payload, run.name),
                                 'name': meta.get('name'), 'project_id': meta.get('project_id'),
                                 'created_at': meta.get('created_at'), 'done': done, 'todo': todo,
                                 'current_cartridge': payload.get('current_cartridge'),
                                 'completed': len(done), 'total': len(done) + len(todo) + (1 if payload.get('current_cartridge') else 0),
                                 'next_cartridge': current, 'attempt': len(record.get('attempts') or []) or None,
                                 'model': models.get(current) or (list(models.values())[-1] if models else None),
                                 'tokens': tokens_value, 'active': self.has_task(owner, run.name),
                                 'queue': self.scheduler.describe(run.name)})
        return sorted(runs, key=lambda r: r.get('created_at') or '', reverse=True)

    def reconcile_queue(self, rid, payload):
        """A retained entry whose run is finished or STOPPED needs no scheduling."""
        entry = self.scheduler.entry(rid)
        if entry is None or not entry.get('needs_start'):
            return
        control = payload.get('control') if isinstance(payload.get('control'), dict) else {}
        if payload.get('status') == 'COMPLETED' or control.get('request') == 'STOP':
            try:
                self.scheduler.forget(rid)
            except sch.SchedulerError:
                pass

    def handover_payloads(self, owner, rid):
        """Verified committed handover payloads of a result_file_v1 run (read-only; empty otherwise)."""
        protocol = self.load_protocol(owner, rid)
        if protocol.get('legacy') or protocol.get('handover_mode') != 'result_file_v1':
            return {}
        base = h._run_base(owner, rid)
        value = lg.load(base)
        if not value:
            return {}
        layout = ho.Layout(base, self.config['cwd'], rid, protocol)
        out = {}
        for cid in value.get('order', []):
            if (value['cartridges'].get(cid) or {}).get('status') != 'done':
                continue
            try:
                out[cid] = {'payload': ho.committed_payload(layout, value, cid)}
            except ho.ResultFailure as exc:
                out[cid] = {'error': f'{exc.code}: {exc}'}
            except (OSError, KeyError, ValueError) as exc:
                out[cid] = {'error': f'{type(exc).__name__}: {exc}'}
        return out

    def ledger_view(self, owner, rid):
        base = h._run_base(owner, rid)
        value = ex.reconcile(base, self.has_task(owner, rid)) or lg.load(base)
        meta = h._load_meta(owner, rid)
        protocol = self.load_protocol(owner, rid)
        if value is None:
            return {'run_id': rid, 'ledger': None, 'markdown': None, 'protocol': protocol['handover_mode'],
                    'tokens': self.legacy_tokens(owner, rid) if protocol.get('legacy') else tokens.total([])}
        return {'run_id': rid, 'ledger': value, 'protocol': protocol['handover_mode'],
                'markdown': lg.render(value, {'name': meta.get('name'), 'tool_ids': meta.get('tool_ids'),
                                              'loop_police': protocol['loop_police']})}

    def view_state(self, data):
        """Server-side last selection, independent of browser storage."""
        path = self.root / 'view-state.json'
        try:
            current = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(current, dict):
                current = {}
        except (FileNotFoundError, ValueError):
            current = {}
        update = data.get('set')
        if update is not None:
            require(isinstance(update, dict) and set(update) <= {'run_id', 'project_id'}, 400, 'Invalid view state')
            for key, value in update.items():
                require(value is None or (isinstance(value, str) and (RUN_ID.fullmatch(value) if key == 'run_id'
                        else h._LIBRARY_ID.fullmatch(value))), 400, 'Invalid view state')
                current[key] = value
            atomic_write(str(path), (json.dumps(current) + '\n').encode('utf-8'))
        result = dict(current)
        rid = result.get('run_id')
        if rid:
            try:
                self.run_owner(rid)
                result['run_exists'] = True
            except ApiError:
                result['run_exists'] = False
        return result

    def results_zip(self, owner, rid, raw):
        """Canonical results ZIP plus the attempt ledger (helper ZIP unchanged)."""
        base = Path(h._run_base(owner, rid))
        value = lg.load(base)
        if value is None:
            return raw
        protocol = self.load_protocol(owner, rid)
        meta = h._load_meta(owner, rid)
        value = redact(value, self.export_secrets())  # same credential redaction as the JSONL export
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(out, 'w', zipfile.ZIP_STORED) as target:
            for info in source.infolist():
                target.writestr(info, source.read(info))
            extra = {'magazine-ledger/LEDGER.md': lg.render(value, {'name': meta.get('name'), 'tool_ids': meta.get('tool_ids'),
                                                                     'loop_police': protocol['loop_police']}).encode('utf-8'),
                     'magazine-ledger/ledger.json': (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')}
            for path in sorted((base / 'handovers').rglob('*')) if (base / 'handovers').is_dir() else []:
                if path.is_file() and not path.is_symlink() and path.name in ('result.json', 'commit.json'):
                    extra['magazine-ledger/' + path.relative_to(base).as_posix()] = path.read_bytes()
            for name, data in extra.items():
                entry = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                entry.external_attr = 0o100600 << 16
                target.writestr(entry, data)
        return out.getvalue()

    # ------------------------------------------------------------ actions
    def set_antiloop(self, data):
        enabled = data.get('enabled')
        require(type(enabled) is bool, 400, 'Antiloop enabled must be boolean')
        if data.get('run_id') is not None:
            rid = data['run_id']
            require(isinstance(rid, str) and RUN_ID.fullmatch(rid), 404, 'Run not found')
            owner = self.run_owner(rid)
            payload = h._status_payload(owner, rid)
            protocol = self.load_protocol(owner, rid)
            require(not protocol.get('legacy'), 409, 'Legacy pi.5 runs keep their recorded settings')
            require(payload['status'] == 'READY' and not protocol.get('antiloop_frozen'), 409,
                    'Antiloop is frozen once a run has started')
            protocol['loop_police'] = dict(protocol['loop_police'], enabled=enabled)
            self.save_protocol(owner, rid, protocol)
            return {'run_id': rid, 'antiloop': enabled}
        path = self.preference_path(data.get('project_id'))
        atomic_write(str(path), (json.dumps({'enabled': enabled}) + '\n').encode('utf-8'))
        return {'project_id': data['project_id'], 'antiloop': enabled}

    async def prepare(self, owner, data):
        require(isinstance(data.get('magazine'),str) and len(data['magazine']) <= 4*1024*1024, 400, 'Magazine JSON required')
        document, _, name = self.parse_document(data.get('magazine'))
        tools = data.get('tool_ids', [])
        require(isinstance(tools,list) and all(isinstance(t,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', t)
                for t in tools) and len(tools)==len(set(tools)), 400, 'Tool IDs must be unique plain tool names')
        require('magazine_publish_result' not in tools, 400, 'magazine_publish_result is supplied by Magazine itself')
        choice = data.get('handover_mode')
        require(choice is None or choice in proto.HANDOVER_MODES, 400, 'Unknown handover protocol')
        antiloop = data.get('antiloop')
        require(antiloop is None or type(antiloop) is bool, 400, 'Antiloop must be boolean')
        cat = await self.catalogue()
        original = document
        model_catalogue = {m['id']: m for m in cat['models']}
        try:
            document, models = select_models(document, data, model_catalogue)
        except ValueError as error:
            raise ApiError(400, str(error)) from error
        errors = h._validate_admission(document, models, tools, cat['models'], cat['tools'])
        require(not errors, 400, '; '.join(errors))
        project_id = data.get('project_id','')
        if project_id:
            project = h._library_preview(h._LIBRARY_ROOT_DEFAULT, project_id)
            def identity(value):
                return {**value,'cartridges':[{k:v for k,v in c.items() if k!='job_type'} for c in value['cartridges']]}
            require(project.get('valid') and identity(project['document']) == identity(document), 409, 'Selected project changed; reload its preview')
            name = project['name']
            _, protocol = self.library_protocol(project_id)
            needs_files = any(d['handover'] or d['inputs'] or d['outputs'] for d in protocol['cartridges'].values())
            if choice is None and not protocol.get('declared_mode') and not needs_files \
                    and not protocol.get('extended_manifest'):
                # A plain v1 manifest prepared by an API client that names no
                # protocol keeps pi.5 semantics; Magazine Simple manifests keep
                # Simple's own default (result_file_v1).
                choice = 'legacy_text'
            if choice is not None and choice != protocol['handover_mode']:
                require(not protocol.get('declared_mode'), 409, 'This workflow declares its handover protocol')
                require(choice != 'legacy_text' or not needs_files, 400, 'This workflow needs committed result handovers')
                protocol['handover_mode'] = choice
            preference = self.antiloop_preference(project_id)
            if preference is not None:
                protocol['loop_police'] = dict(protocol['loop_police'], enabled=preference)
        else:
            # The native view always names a protocol (default result_file_v1);
            # an API client that names none keeps the pi.5 final-text contract.
            protocol = proto.default_protocol(original, choice or 'legacy_text')
        if antiloop is not None:
            protocol['loop_police'] = dict(protocol['loop_police'], enabled=antiloop)
        applied_plan = None
        if data.get('model_plan') is not None:
            applied_plan = ui.validate_plan(data['model_plan'], name,
                [c['id'] for c in document['cartridges']], model_catalogue)
            applied_plan['manual_overrides'] = {c['id']: models[c['job_type']] for c in document['cartridges']
                if models[c['job_type']] != applied_plan['cartridge_models'][c['id']]}
        assignments = {c['id']: models[c['job_type']] for c in document['cartridges']}
        try:
            execution_settings = select_thinking(data, assignments, model_catalogue)
        except ValueError as error:
            raise ApiError(400, str(error)) from error
        protocol.update(version=proto.protocol_version(protocol), prepared_by=VERSION, antiloop_frozen=False)
        rid = uuid.uuid4().hex[:12]
        Path(h._state_dir(owner,rid)).mkdir(parents=True,mode=0o700)
        atomic_write(h._mag_path(owner,rid),(json.dumps(document)+'\n').encode())
        self.save_protocol(owner, rid, protocol)
        CORE['ss'].save_run_state(h._state_path(owner,rid),CORE['state'].new_ready_state([c['id'] for c in document['cartridges']]))
        self.save_meta(owner,rid,{'model_map':models,'tool_ids':tools,'owner_id':owner,
            'cartridge_models':assignments, 'execution_settings':execution_settings,
            'created_at':h._now(),'name':name,'project_id':project_id,'schema_version':document['schema_version'],
            'magazine_version':VERSION, **({'model_plan': applied_plan} if applied_plan else {})})
        return self.status(owner,rid)

    def attempts_used(self, owner, rid, payload):
        try:
            value = lg.load(h._run_base(owner, rid))
        except ValueError:
            raise ApiError(409, 'The attempt ledger is unreadable; inspect the run before starting it')
        if not value:
            return None, 0
        cid = payload.get('current_cartridge') or next(iter(payload.get('todo') or []), None)
        return cid, len(((value.get('cartridges') or {}).get(cid) or {}).get('attempts') or [])

    def checkpoint_admission(self, owner, rid, payload, protocol):
        decl = protocol.get('project_checkpoint')
        if not decl:
            return
        base = h._run_base(owner, rid)
        current = payload.get('current_cartridge') or next(iter(payload.get('todo') or []), None)
        ledger = lg.load(base)
        if ex.resumable_completion(ledger, current):
            return  # The executor verifies and replays this attempt before model dispatch.
        records = (ledger or {}).get('cartridges', {})
        done = payload.get('done') or []
        expected = [cid for cid in decl['steps'] if cid in done and (decl['required'] or any(
            attempt.get('checkpoint') for attempt in records.get(cid, {}).get('attempts', [])))]
        doc = CORE['doc'].load_document(h._mag_path(owner, rid)).document
        book = ex.checkpoint_book(self.config, protocol, doc, base, rid)
        try:
            book.admission(expected)
        except cpt.CheckpointError as exc:
            raise ApiError(409, f'Checkpoint preflight failed: {exc}') from None

    async def start(self, owner, rid, payload):
        if payload.get('status') == 'RUNNING' and not (payload.get('execution') or {}).get('owner_alive'):
            self.reconcile_interrupted(owner, rid, retire_schedule=False)
            raise ApiError(409, 'Interrupted run reconciled to HALTED; review it and Prepare recovery before Start')
        interception = h._lost_runtime_interception(payload,owner,rid)
        if interception is not None and interception != 'same_runtime':
            require(interception['status_code'] < 400, interception['status_code'],interception.get('detail','Start refused'))
            return interception
        ok, code, detail = h._start_precheck(payload,'pi-host-access')
        require(ok, code, detail)
        self.admission_checks(owner, rid, payload)
        # pi.10: declared host applications are checked before any model is started.
        protocol = self.load_protocol(owner, rid)
        await asyncio.to_thread(self.checkpoint_admission, owner, rid, payload, protocol)
        self.preflights[rid] = await self.host_preflight(protocol)
        loaded = CORE['doc'].load_document(h._mag_path(owner,rid))
        cat = await self.catalogue()
        errors = h._validate_admission(loaded.document,payload['model_map'],payload['tool_ids'],cat['models'],cat['tools'])
        require(not errors,400,'; '.join(errors))
        # A delete request may have completed while catalogue discovery awaited.
        # Recheck admission synchronously before creating a scheduler entry.
        require(self.run_owner(rid) == owner, 404, 'Run is no longer available')
        payload = h._status_payload(owner, rid)
        ok, code, detail = h._start_precheck(payload, 'pi-host-access')
        require(ok, code, detail)
        try:
            self.scheduler.admit(rid, owner, len(payload.get('done') or []))
        except sch.SchedulerError as error:
            raise ApiError(error.code, str(error)) from error
        entry = self.scheduler.entry(rid)
        entry['_validated'] = True  # in memory only: just checked against the live catalogue
        await self.scheduler.pump()
        queue = self.scheduler.describe(rid)
        handle = h._RUN_HANDLES.get((owner, rid))
        if queue['holder'] and handle is not None:
            return {'run_id':rid,'status':'accepted','attempt':handle['prepared'].attempt}
        if self.scheduler.entry(rid) is None:
            raise ApiError(409, self.scheduler.notes.get(rid) or 'The run could not start')
        return {'run_id':rid,'status':'queued','queue_position':queue['position'],
                'message':f"Queued at position {queue['position']}; it starts when the current cartridge work allows."}

    def admission_checks(self, owner, rid, payload):
        base = h._run_base(owner, rid)
        ex.reconcile(base, self.has_task(owner, rid))
        protocol = self.load_protocol(owner, rid)
        required = list(protocol.get('required_paths') or [])
        if protocol.get('project_checkpoint'):
            required.append(protocol['project_checkpoint']['root'])
        problems = proto.check_required_paths(required)
        require(not problems, 409, 'Required project path missing; nothing was started: ' + '; '.join(problems)
                + '. Mount the project folder into the Pi container, then Start again.')
        cid, used = self.attempts_used(owner, rid, payload)
        require(used < lg.HARD_CEILING or ex.resumable_completion(lg.load(base), cid), 409,
                f'{cid} has used all {lg.HARD_CEILING} attempts; Magazine will not start a sixth attempt')

    async def _launch(self, entry):
        """The scheduler grants the slot: enter the core and start the run's task."""
        owner, rid = entry['owner'], entry['run_id']
        payload = h._status_payload(owner, rid)
        ok, code, detail = h._start_precheck(payload,'pi-host-access')
        require(ok, code, detail)
        self.admission_checks(owner, rid, payload)
        base = h._run_base(owner, rid)
        protocol = self.load_protocol(owner, rid)
        await asyncio.to_thread(self.checkpoint_admission, owner, rid, payload, protocol)
        # A queued run may wait a long time: recheck its declared host applications when it is admitted.
        self.preflights[rid] = await self.host_preflight(protocol)
        # Recheck native model/tool availability before admission; READY remains
        # untouched on failure. Only server configuration supplies worker paths.
        loaded = CORE['doc'].load_document(h._mag_path(owner,rid))
        if not entry.pop('_validated', False):
            cat = await self.catalogue()
            errors = h._validate_admission(loaded.document,payload['model_map'],payload['tool_ids'],cat['models'],cat['tools'])
            require(not errors,400,'; '.join(errors))
        if not protocol.get('legacy') and (not protocol.get('antiloop_frozen')
                or protocol.get('version') != proto.protocol_version(protocol)):
            # Freeze the effective Antiloop choice for this run's execution.
            protocol.setdefault('frozen_at', h._now())
            protocol.update(antiloop_frozen=True, version=proto.protocol_version(protocol))
            self.save_protocol(owner, rid, protocol)
        try:
            prepared = CORE['rp'].prepare_run(h._mag_path(owner,rid),base,owner)
        except CORE['ra'].RunAdmissionError as error:
            raise ApiError(409,str(error)) from error
        meta = h._load_meta(owner, rid)
        meta['document'] = loaded.document
        config = {**self.config,'tools':payload['tool_ids']}
        dispatch = ex.CartridgeExecutor(core=CORE, config=config, run_base=base, run_id=rid, owner=owner,
            state_path=prepared.state_path, lock_path=prepared.lock_path, prepared=prepared,
            protocol=protocol, meta=meta, workspace=self.config['cwd'], scheduler=self.scheduler)
        task = asyncio.create_task(h._run_bg_dispatch(rid,h._mag_path(owner,rid),base,owner,payload['model_map'],prepared,dispatch))
        h._TASKS.add(task)
        key = (owner,rid)
        handle = {'task':task,'dispatch':dispatch,'prepared':prepared}
        h._RUN_HANDLES[key] = handle
        def forget(t):
            h._TASKS.discard(t)
            if h._RUN_HANDLES.get(key) is handle:
                h._RUN_HANDLES.pop(key,None)
            self.scheduler.finished(rid)
        task.add_done_callback(forget)

    async def close(self):
        # Host reload/shutdown aborts children, then retires active claims through
        # the unchanged core cancellation path. It never creates operator STOP.
        self.scheduler.freeze()  # keep the queue; nothing is dispatched during shutdown
        tasks = list(h._TASKS)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)


async def main():
    config = json.loads(sys.argv[2])
    # This dedicated process runs nothing but workers, so orphans re-parented
    # to it can only be worker descendants: adopt and reap them.
    PiDispatchModule.become_subreaper()
    service = Service(sys.argv[1],config)
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=12*1024*1024)
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda:protocol,sys.stdin)
    pending = set()
    def emit(value):
        print(json.dumps(value,ensure_ascii=False),flush=True)
    async def answer(value):
        try:
            result = await service.handle(value)
            emit({'id':value.get('id'),'ok':True,'result':result})
        except (ApiError,h._LibraryError,ui.SupportError) as error:
            emit({'id':value.get('id'),'ok':False,'code':getattr(error,'code',getattr(error,'status_code',400)),
                  'error':str(error).replace('administrator review', 'manual review')})
        except Exception as error:
            emit({'id':value.get('id'),'ok':False,'code':500,'error':str(error)})
    stop = asyncio.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):
        loop.add_signal_handler(sig,stop.set)
    emit({'event':'ready'})
    try:
        while not stop.is_set():
            read_task = asyncio.create_task(reader.readline())
            stop_task = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait((read_task,stop_task),return_when=asyncio.FIRST_COMPLETED)
            if stop_task in done:
                read_task.cancel()
                break
            stop_task.cancel()
            raw = read_task.result()
            if not raw:
                break
            try:
                value = json.loads(raw)
                require(isinstance(value,dict) and isinstance(value.get('id'),str),400,'Invalid protocol request')
            except (ValueError,ApiError):
                emit({'id':None,'ok':False,'code':400,'error':'Malformed request'})
                continue
            task = asyncio.create_task(answer(value))
            pending.add(task)
            task.add_done_callback(pending.discard)
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
        await service.close()


if __name__ == '__main__':
    asyncio.run(main())
