"""Trusted host-tool broker: lets sandboxed Magazine workers call Unreal Connector and 3D Resource Kit
without ever seeing their credentials (0.1.0-pi.10).

Why a broker
------------
Workers are Landlock-confined and cannot read Pi's agent folder or Magazine's data folder, which is where
credentials live. A worker therefore asks *this* process, over its private stdin/stdout channel, to perform one
allowlisted operation. The broker:

* knows only two services, each reached at a fixed allowlisted host (never a model-supplied URL or redirect);
* allows only named operations with a fixed method and path (no arbitrary proxying, no host commands);
* refuses operations a workflow did not declare, and project IDs outside the workflow's declared projects;
* attaches the bearer credential itself and masks it from every result and error;
* records each call (operation, status, job/approval/plan identifiers, argument digest) in the attempt's
  evidence, so a recovery can query an existing external job instead of submitting it again.

Workflows that declare no ``requires.services`` get no host tool at all, so ordinary Magazine workflows are
unchanged.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from run_export import redact

CONFIG_NAME = 'host-services.json'
ALLOWED_HOSTS = ('host.docker.internal', '127.0.0.1', 'localhost')
MAX_ARGS = 64 * 1024
MAX_BODY = 256 * 1024
TIMEOUT = 30.0

# Fixed operation tables: op -> (method, path), from the services' own contracts:
# Unreal Connector OpenAPI (operationId == tool name) and the Resource Kit model-tool table.
UC_OPS = {
    'health': ('GET', '/health'), 'capabilities': ('GET', '/capabilities'), 'discover_projects': ('GET', '/projects'),
    'project_status': ('POST', '/project_status'), 'list_assets': ('POST', '/list_assets'),
    'asset_references': ('POST', '/asset_references'), 'inspect_asset': ('POST', '/inspect_asset'),
    'inspect_material': ('POST', '/inspect_material'), 'run_validation': ('POST', '/run_validation'),
    'submit_change': ('POST', '/submit_change'), 'list_actors': ('POST', '/list_actors'),
    'inspect_blueprint': ('POST', '/inspect_blueprint'), 'fab_status': ('GET', '/fab_status'),
    'fab_connection_status': ('GET', '/fab_connection_status'), 'fab_search_owned': ('POST', '/fab_search_owned'),
    'fab_list_owned': ('POST', '/fab_list_owned'), 'fab_asset_details': ('POST', '/fab_asset_details'),
    'fab_local_assets': ('POST', '/fab_local_assets'), 'get_job': ('POST', '/get_job'),
    'cancel_job': ('POST', '/cancel_job'), 'list_evidence': ('POST', '/list_evidence'),
    'get_evidence': ('POST', '/get_evidence'),
}
RK_OPS = {
    'whoami': ('GET', '/tools/whoami'), 'get_library_status': ('GET', '/tools/library/status'),
    'list_local_collections': ('GET', '/tools/library/collections'),
    'search_local_assets': ('POST', '/tools/library/search'), 'inspect_local_asset': ('POST', '/tools/library/inspect'),
    'update_library': ('POST', '/tools/library/update'),
    'search_catalogue': ('POST', '/tools/catalogue/search'),
    'resolve_asset_reference': ('POST', '/tools/catalogue/resolve'),
    'export_catalogue': ('POST', '/tools/catalogue/export'), 'prepare_fab_handoff': ('POST', '/tools/catalogue/handoff'),
    'list_projects': ('POST', '/tools/projects/list'), 'inspect_project': ('POST', '/tools/projects/inspect'),
    'register_project': ('POST', '/tools/projects/register'), 'create_project': ('POST', '/tools/projects/create'),
    'list_project_files': ('POST', '/tools/projects/files'), 'read_project_file': ('POST', '/tools/projects/read_file'),
    'write_project_file': ('POST', '/tools/projects/write_file'),
    'stage_asset_for_project': ('POST', '/tools/projects/stage_asset'),
    'export_project_file': ('POST', '/tools/projects/export_file'),
    'get_operation_status': ('POST', '/tools/operations/status'), 'get_unreal_status': ('GET', '/tools/unreal/status'),
}
# Never offered to a worker: starting an Unreal editor needs the owner, and only one Unreal may run at a time.
RK_DENIED = {'open_project_in_unreal'}

SERVICES = {
    'unreal_connector': {'label': 'Unreal Connector', 'port': 8765, 'ops': UC_OPS, 'probe': 'health',
                         'token_re': re.compile(r'^[A-Za-z0-9_\-]{20,200}$')},
    'resource_kit': {'label': '3D Resource Kit', 'port': 8766, 'ops': RK_OPS, 'probe': 'whoami',
                     'token_re': re.compile(r'^rk_[A-Za-z0-9_\-]{40,}$')},
}
PI_MCP_NAMES = ('3d-resource-kit', 'resource-kit', '3d_resource_kit')
_REF_KEYS = ('job_id', 'approval_id', 'plan_hash', 'operation_id', 'status', 'approval_status')
_REF = re.compile(r'[A-Za-z0-9_.:\-]{1,80}\Z')
_VERSION = re.compile(r'(\d+(?:\.\d+){0,2})([.-][A-Za-z][A-Za-z0-9.-]*)?(?:\+[A-Za-z0-9.-]+)?\Z')
PROJECT_OPS = {
    'project_status', 'list_assets', 'asset_references', 'inspect_asset', 'inspect_material',
    'run_validation', 'submit_change', 'list_actors', 'inspect_blueprint', 'inspect_project',
    'list_project_files', 'read_project_file', 'write_project_file', 'stage_asset_for_project',
    'export_project_file', 'prepare_fab_handoff', 'resolve_asset_reference',
}
INDIRECT = {'get_job': 'job_id', 'cancel_job': 'job_id', 'list_evidence': 'job_id',
            'get_evidence': 'job_id', 'get_operation_status': 'operation_id'}
UNSCOPED_CREATION = {'register_project', 'create_project'}
MUTATIONS = {'submit_change', 'cancel_job', 'update_library', 'export_catalogue', 'prepare_fab_handoff',
             'write_project_file', 'stage_asset_for_project', 'export_project_file'}
ENGINE_JOBS = {'inspect_asset', 'inspect_material', 'inspect_blueprint', 'list_actors', 'run_validation'}
RK_SCOPES = {
    **dict.fromkeys(('get_library_status', 'list_local_collections', 'search_local_assets', 'inspect_local_asset',
                     'list_projects', 'inspect_project', 'list_project_files', 'read_project_file',
                     'get_operation_status', 'search_catalogue', 'resolve_asset_reference', 'export_catalogue'), 'library'),
    **dict.fromkeys(('write_project_file', 'stage_asset_for_project', 'export_project_file'), 'projects_write'),
    'whoami': 'discover', 'update_library': 'library_update', 'get_unreal_status': 'unreal',
    'prepare_fab_handoff': 'downloads',
}
_FAB_FILTERS = 'query offset limit category engine_version local engine_listed_only'
_CATALOGUE_FILTERS = 'query source kind scope ids compact'
_OPTIONAL_PROJECT = {'resolve_asset_reference', 'search_catalogue', 'export_catalogue'}
_ARGUMENTS = {
    'capabilities': 'topic',
    'list_assets': 'source folder search class_name offset limit',
    'asset_references': 'asset_path direction offset limit',
    'inspect_asset': 'asset_path wait_seconds',
    'inspect_material': 'asset_path search offset limit wait_seconds',
    'run_validation': 'check wait_seconds',
    'submit_change': 'change_type params mode confirm wait_seconds',
    'list_actors': 'level_path search offset limit wait_seconds',
    'inspect_blueprint': 'asset_path graph node_offset node_limit detail wait_seconds search node_id snapshot_job_id',
    'fab_search_owned': _FAB_FILTERS, 'fab_list_owned': _FAB_FILTERS,
    'fab_asset_details': 'asset_id engine_version', 'fab_local_assets': 'query offset limit',
    'get_job': 'job_id wait_seconds', 'cancel_job': 'job_id', 'list_evidence': 'job_id',
    'get_evidence': 'job_id name contains offset max_bytes',
    'search_local_assets': 'query kind format collection target_app include_missing duplicates_only offset limit',
    'inspect_local_asset': 'asset_id',
    'search_catalogue': _CATALOGUE_FILTERS + ' offset limit',
    'resolve_asset_reference': 'reference',
    'export_catalogue': _CATALOGUE_FILTERS + ' format selection',
    'prepare_fab_handoff': 'reference',
    'list_projects': 'type query',
    'list_project_files': 'path limit',
    'read_project_file': 'path max_bytes',
    'write_project_file': 'path text overwrite',
    'stage_asset_for_project': 'asset_id subfolder',
    'export_project_file': 'path',
    'get_operation_status': 'operation_id',
}
_TYPED_ARGS = {
    **dict.fromkeys(('offset', 'limit', 'node_offset', 'node_limit', 'max_bytes'), int),
    **dict.fromkeys(('overwrite', 'include_missing', 'duplicates_only', 'engine_listed_only'), bool),
    **dict.fromkeys(('params', 'selection'), dict), 'compact': bool, 'ids': list,
}
_REQUIRED_ARGUMENTS = {
    **dict.fromkeys(('asset_references', 'inspect_asset', 'inspect_material', 'inspect_blueprint'), ('asset_path',)),
    **dict.fromkeys(('read_project_file', 'export_project_file'), ('path',)),
    **dict.fromkeys(('resolve_asset_reference', 'prepare_fab_handoff'), ('reference',)),
    **dict.fromkeys(('fab_asset_details', 'inspect_local_asset', 'stage_asset_for_project'), ('asset_id',)),
    'submit_change': ('change_type',), 'list_actors': ('level_path',), 'write_project_file': ('path', 'text'),
    'get_evidence': ('name',),
}


class HostToolError(ValueError):
    """A refusal or failure whose message is safe to show to the model and the owner."""


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def project_identifier(row):
    if isinstance(row, dict):
        value = row.get('project_id') or row.get('id')
        return value if isinstance(value, str) else None
    return None


def restrict_projects(data, allowed):
    projects = data.get('projects')
    if isinstance(projects, list):
        data['projects'] = [row for row in projects if project_identifier(row) in allowed]
        if 'count' in data:
            data['count'] = len(data['projects'])


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.host-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ------------------------------------------------------------------------------------------------ policy
def parse_requires(value):
    """Optional workflow ``requires`` declaration. Returns a normalised dict ({} when absent).

    {"services": {"unreal_connector": {"operations": [...], "projects": [...], "min_api": "0.2.0",
                                       "capabilities": [...]},
                  "resource_kit": {"operations": [...], "scopes": [...]}},
     "exclusive_unreal": true, "approval_note": "Level changes need approval in Unreal Connector"}
    """
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {'services', 'exclusive_unreal', 'approval_note'}:
        raise ValueError('requires accepts services, exclusive_unreal and approval_note')
    exclusive = value.get('exclusive_unreal', False)
    if type(exclusive) is not bool:
        raise ValueError('requires.exclusive_unreal must be a boolean')
    out = {'services': {}, 'exclusive_unreal': exclusive}
    note = value.get('approval_note')
    if note is not None:
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError('requires.approval_note must be text of at most 500 characters')
        out['approval_note'] = note
    services = value.get('services', {})
    if not isinstance(services, dict) or set(services) - set(SERVICES):
        raise ValueError(f"requires.services may name only: {', '.join(SERVICES)}")
    for name, spec in services.items():
        spec = {} if spec is True else spec
        if not isinstance(spec, dict) or set(spec) - {'operations', 'projects', 'min_api', 'capabilities', 'scopes'}:
            raise ValueError(f'requires.services.{name} accepts operations, projects, min_api, capabilities, scopes')
        table = SERVICES[name]['ops']
        ops = spec.get('operations')
        if ops is None:
            ops = [SERVICES[name]['probe']]
        if not isinstance(ops, list) or not ops or any(not isinstance(o, str) or o not in table for o in ops):
            raise ValueError(f'requires.services.{name}.operations must list known operations: {", ".join(table)}')
        clean = {'operations': sorted(set(ops))}
        for key in ('projects', 'capabilities', 'scopes'):
            v = spec.get(key)
            if v is not None:
                if not isinstance(v, list) or len(v) > 64 or not all(isinstance(x, str) and 0 < len(x) <= 200 for x in v):
                    raise ValueError(f'requires.services.{name}.{key} must be a list of names')
                clean[key] = sorted(set(v))
        if spec.get('min_api') is not None:
            if not isinstance(spec['min_api'], str) or not re.fullmatch(r'\d+(\.\d+){0,2}', spec['min_api']):
                raise ValueError(f'requires.services.{name}.min_api must look like 0.2.0')
            clean['min_api'] = spec['min_api']
        out['services'][name] = clean
    if exclusive and not out['services']:
        raise ValueError('requires.exclusive_unreal needs a declared host service')
    return out


def parse_saved_requires(value, legacy=False):
    parsed = parse_requires(value)
    if legacy:
        for name, spec in parsed.get('services', {}).items():
            if set(spec['operations']) == set(SERVICES[name]['ops']):
                spec['operations'] = [SERVICES[name]['probe']]
    return parsed


def _version(text):
    match = _VERSION.fullmatch(text) if isinstance(text, str) else None
    if not match:
        return ()
    numbers = [int(x) for x in match[1].split('.')]
    return (*((numbers + [0, 0])[:3]), match[2] is None)


def tool_description(policy) -> str:
    """Compact, task-specific contract for the worker tool (instead of broad API discovery in every prompt)."""
    lines = ['Call an allowlisted operation of a host application. Credentials are attached by Magazine; never '
             'look for keys or URLs. Arguments are a JSON object exactly as the operation expects; never guess IDs, '
             'paths or change types: use values returned by earlier calls.']
    for name, spec in (policy or {}).get('services', {}).items():
        lines.append(f"{name}: {', '.join(spec['operations'])}" +
                     (f" (projects: {', '.join(spec['projects'])})" if spec.get('projects') else ''))
    uc_ops = (policy or {}).get('services', {}).get('unreal_connector', {}).get('operations', [])
    if 'submit_change' in uc_ops:
        lines.append('Unreal Connector writes: submit_change mode=preview first, then mode=apply with confirm=<plan_hash>. '
                     'approval_required means the owner must approve on the PC. Report it and stop.')
    if 'get_job' in uc_ops:
        lines.append('Poll a recorded Connector job with get_job(job_id, wait_seconds=20).')
    return '\n'.join(lines)


# ------------------------------------------------------------------------------------------------ broker
class HostBroker:
    def __init__(self, data_dir, agent_dir=None, connect=None):
        self.data_dir = Path(data_dir)
        self.agent_dir = Path(agent_dir) if agent_dir else None
        self._connect = connect or (lambda host, port: http.client.HTTPConnection(host, port, timeout=TIMEOUT))
        self._call_lock = threading.Lock()

    # ---- credentials (owner-managed; never returned)
    @property
    def config_file(self) -> Path:
        return self.data_dir / CONFIG_NAME

    def _load(self) -> dict:
        try:
            d = json.loads(self.config_file.read_text(encoding='utf-8'))
            return d if isinstance(d, dict) and isinstance(d.get('services'), dict) else {'services': {}}
        except (OSError, ValueError):
            return {'services': {}}

    def _pi_mcp_entry(self, name):
        """Reuse the Resource Kit key that the Kit's own pairing already wrote into Pi's mcp.json."""
        if name != 'resource_kit' or self.agent_dir is None:
            return None
        try:
            servers = json.loads((self.agent_dir / 'mcp.json').read_text(encoding='utf-8')).get('mcpServers') or {}
        except (OSError, ValueError, AttributeError):
            return None
        for key in PI_MCP_NAMES:
            entry = servers.get(key)
            if not isinstance(entry, dict):
                continue
            auth = str((entry.get('headers') or {}).get('Authorization') or '')
            m = re.fullmatch(r'Bearer\s+(\S+)', auth)
            url = re.fullmatch(r'http://([A-Za-z0-9.\-]+):(\d{2,5})/mcp/?', str(entry.get('url') or ''))
            if m and url and url.group(1) in ALLOWED_HOSTS:
                return {'host': url.group(1), 'port': int(url.group(2)), 'token': m.group(1), 'source': 'pi-mcp'}
        return None

    def _entry(self, name):
        e = self._load()['services'].get(name)
        if isinstance(e, dict) and e.get('token'):
            return {**e, 'source': 'magazine'}
        return self._pi_mcp_entry(name)

    def secrets(self):
        return tuple(e['token'] for name in SERVICES if (e := self._entry(name)) and isinstance(e.get('token'), str))

    def configure(self, name, token, host='host.docker.internal', port=None) -> dict:
        if name not in SERVICES:
            raise HostToolError('Unknown service')
        if not isinstance(token, str) or not SERVICES[name]['token_re'].fullmatch(token.strip()):
            raise HostToolError(f"That does not look like a {SERVICES[name]['label']} key")
        # Owner feedback: the Connector offers several keys through the clipboard; name the wrong one.
        wrong = {'ulk_': 'the Unreal Connector link key for 3D Resource Kit', 'fsk_': 'the Unreal Connector browser-sync key',
                 'op_': 'the Unreal Connector operator key', 'rk_': 'a 3D Resource Kit key'}
        if name == 'unreal_connector':
            for prefix, what in wrong.items():
                if token.strip().startswith(prefix):
                    raise HostToolError(f'That is {what}, not the API key. In Unreal Connector use Settings > Copy API key.')
        port = SERVICES[name]['port'] if port is None else port
        if host not in ALLOWED_HOSTS or type(port) is not int or not 1024 <= port <= 65535:
            raise HostToolError(f"Host must be one of {', '.join(ALLOWED_HOSTS)} with a port 1024-65535")
        data = self._load()
        data['services'][name] = {'host': host, 'port': port, 'token': token.strip(),
                                  'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        data['version'] = 1
        _atomic_write(self.config_file, json.dumps(data, indent=1).encode())
        return self.summary()

    def forget(self, name) -> dict:
        data = self._load()
        data['services'].pop(name, None)
        _atomic_write(self.config_file, json.dumps(data, indent=1).encode())
        return self.summary()

    def summary(self) -> dict:
        out = {}
        for name, svc in SERVICES.items():
            e = self._entry(name)
            out[name] = {'label': svc['label'], 'configured': bool(e),
                         'source': e.get('source') if e else None,
                         'address': f"http://{e['host']}:{e['port']}" if e else None,
                         'key_fingerprint': hashlib.sha256(e['token'].encode()).hexdigest()[:12] if e else None}
        return out

    # ---- calls
    def raw_call(self, name, op, args=None) -> dict:
        svc = SERVICES.get(name)
        if svc is None or op not in svc['ops']:
            raise HostToolError(f'Unknown operation {name}.{op}')
        if name == 'resource_kit' and op in RK_DENIED:
            raise HostToolError(f'{op} is not available to Magazine workers')
        e = self._entry(name)
        if not e:
            raise HostToolError(f"{svc['label']} is not connected in Magazine (Manage > Host connections).")
        if (e.get('host') not in ALLOWED_HOSTS or type(e.get('port')) is not int
                or not 1024 <= e['port'] <= 65535 or not isinstance(e.get('token'), str)
                or not svc['token_re'].fullmatch(e['token'])):
            raise HostToolError('Stored host connection is invalid. Reconnect it in Manage.')
        method, path = svc['ops'][op]
        args = {} if args is None else args
        if not isinstance(args, dict):
            raise HostToolError('Arguments must be a JSON object')
        try:
            body = json.dumps(args, separators=(',', ':'), allow_nan=False).encode()
        except (ValueError, TypeError, RecursionError):
            raise HostToolError('Arguments must contain valid JSON') from None
        if len(body) > MAX_ARGS:
            raise HostToolError('Arguments exceed 64 KiB')
        query = ''
        if method == 'GET' and args:
            from urllib.parse import urlencode
            query = '?' + urlencode({str(k): (json.dumps(v) if isinstance(v, (dict, list)) else v)
                                     for k, v in args.items()})
        token = e['token']
        conn = None
        try:
            conn = self._connect(e['host'], e['port'])
            headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                       'User-Agent': 'magazine-pi-host-broker'}
            if method == 'POST':
                headers['Content-Type'] = 'application/json'
            conn.request(method, path + query, body=body if method == 'POST' else None, headers=headers)
            resp = conn.getresponse()
            raw = resp.read(MAX_BODY + 1)
            status = resp.status
        except (OSError, http.client.HTTPException) as exc:
            raise HostToolError(f"{svc['label']} is not reachable at http://{e['host']}:{e['port']} "
                                f"({exc.__class__.__name__}). Is it switched on?") from None
        finally:
            if conn is not None:
                conn.close()
        truncated = len(raw) > MAX_BODY
        secrets = (*self.secrets(), token)
        try:
            data = redact(json.loads(raw), secrets) if not truncated else None
        except (ValueError, RecursionError):
            data = None
        refs = {k: data[k] for k in _REF_KEYS if isinstance(data, dict)
                and isinstance(data.get(k), str) and _REF.fullmatch(data[k])}
        return {'service': name, 'operation': op, 'status': status,
                'ok': 200 <= status < 300 and isinstance(data, dict),
                'truncated': truncated, 'data': data, 'refs': refs, 'source': e.get('source')}

    @staticmethod
    def _receipts(evidence):
        if evidence is None:
            return []
        try:
            with Path(evidence).open('rb') as stream:
                raw = stream.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024 or (raw and not raw.endswith(b'\n')):
                raise ValueError('incomplete or oversized')
            rows = [json.loads(line) for line in raw.splitlines()]
            if any(not isinstance(row, dict) for row in rows):
                raise ValueError('invalid receipt')
            return rows
        except FileNotFoundError:
            return []
        except (OSError, ValueError, RecursionError):
            raise HostToolError('Host operation receipts are unreadable; review before continuing') from None

    @staticmethod
    def _receipt(evidence, row):
        if evidence is None:
            return
        path = Path(evidence)
        try:
            try:
                path.parent.mkdir()
            except FileExistsError:
                pass
            else:
                HostBroker._sync_directory(path.parent.parent)
            with path.open('a', encoding='utf-8', newline='\n') as stream:
                stream.write(json.dumps(row) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            HostBroker._sync_directory(path.parent)
        except OSError:
            raise HostToolError('Host operation receipt could not be saved; check the run before continuing') from None

    @staticmethod
    def _sync_directory(path):
        if os.name == 'posix':
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    @staticmethod
    def _authorise(op, args, allowed, history, name):
        if not isinstance(args, dict):
            raise HostToolError('Arguments must be a JSON object')
        if op in UNSCOPED_CREATION:
            raise HostToolError('Create or register the project with the owner before declaring it in Magazine')
        fields = set(_ARGUMENTS.get(op, '').split())
        if op in PROJECT_OPS or op in _OPTIONAL_PROJECT:
            fields.add('project_id')
        if set(args) - fields:
            raise HostToolError('This operation received an unknown argument')
        if any(not isinstance(args.get(key), str) for key in _REQUIRED_ARGUMENTS.get(op, ())):
            raise HostToolError('A required text argument is missing or invalid')
        if any(key in _TYPED_ARGS and type(value) is not _TYPED_ARGS[key] for key, value in args.items()):
            raise HostToolError('An operation argument has the wrong type')
        projects = allowed.get('projects') or []
        project = args.get('project_id')
        needs_project = op in PROJECT_OPS - _OPTIONAL_PROJECT or args.get('scope') == 'project'
        if (needs_project or 'project_id' in args) and (not isinstance(project, str) or project not in projects):
            raise HostToolError('project_id must name one of this workflow’s declared projects')
        if 'wait_seconds' in args and (type(args['wait_seconds']) not in (int, float)
                or not 0 <= args['wait_seconds'] <= 25):
            raise HostToolError('wait_seconds must be a number from 0 to 25')
        ref_key = INDIRECT.get(op)
        ref_arg = ref_key
        if op == 'inspect_blueprint' and args.get('snapshot_job_id') is not None:
            ref_key, ref_arg = 'job_id', 'snapshot_job_id'
        if ref_key is not None:
            ref = args.get(ref_arg)
            match = next((row for row in reversed(history) if row.get('service') == name and row.get('ok')
                          and (row.get('refs') or {}).get(ref_key) == ref), None) if isinstance(ref, str) else None
            if match is None or (match.get('project_id') is not None and match['project_id'] not in projects):
                raise HostToolError('Use an operation identifier recorded for this cartridge and its declared projects')
            if project is not None and match.get('project_id') != project:
                raise HostToolError('The job belongs to a different project')
            project = match.get('project_id')
        if op == 'submit_change' and args.get('mode', 'preview') not in ('preview', 'apply'):
            raise HostToolError('Choose preview or apply for a Connector change')
        if op == 'submit_change' and args.get('mode') == 'apply':
            confirm = args.get('confirm')
            if not isinstance(confirm, str) or not any(row.get('operation') == 'submit_change'
                    and row.get('project_id') == project and row.get('preview') and row.get('ok')
                    and (row.get('refs') or {}).get('plan_hash') == confirm for row in history):
                raise HostToolError('Use a confirmed preview recorded for the same project and cartridge')
        return project

    def call(self, name, op, args, policy, evidence=None) -> dict:
        """A worker's call: checked against the workflow's declared policy, then performed and recorded."""
        with self._call_lock:
            return self._call(name, op, args, policy, evidence)

    def _call(self, name, op, args, policy, evidence):
        allowed = (policy or {}).get('services', {}).get(name)
        if allowed is None:
            raise HostToolError(f'This workflow does not declare {name} in requires.services')
        if op not in allowed['operations']:
            raise HostToolError(f'{op} is not among the operations this workflow declared for {name}')
        history = self._receipts(evidence)
        project = self._authorise(op, args, allowed, history, name)
        preview = op == 'submit_change' and args.get('mode', 'preview') == 'preview'
        starts_work = op in MUTATIONS or op in ENGINE_JOBS
        try:
            fingerprint = _digest({k: v for k, v in args.items() if k != 'wait_seconds'})
        except (ValueError, TypeError, RecursionError):
            raise HostToolError('Arguments must contain valid JSON') from None
        row = redact({'service': name, 'operation': op, 'project_id': project, 'args_sha256': fingerprint,
                      'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'preview': preview}, self.secrets())
        if starts_work:
            if evidence is None:
                raise HostToolError('This operation requires durable run receipts')
            prior = next((x for x in reversed(history) if all(x.get(k) == row[k]
                         for k in ('service', 'operation', 'args_sha256'))), None)
            if prior and prior.get('phase') in ('pending', 'ambiguous'):
                raise HostToolError('A previous request may have started work. Check its status with the owner '
                                    'before submitting it again.')
            if prior and prior.get('ok') and not preview:
                return {'ok': True, 'status': prior['status'], 'service': name, 'operation': op,
                        'refs': prior['refs'], 'replayed': True,
                        'data': {'detail': 'This request already succeeded. Use its recorded receipt.'}}
            self._receipt(evidence, dict(row, phase='pending'))
        try:
            result = self.raw_call(name, op, args)
        except HostToolError as exc:
            error = redact(str(exc), self.secrets())
            self._receipt(evidence, dict(row, phase='ambiguous' if starts_work else 'finished', error=error))
            raise HostToolError(error) from None
        status = result['status']
        ambiguous = starts_work and not result['ok'] and status not in (400, 401, 403, 404, 405, 422)
        self._receipt(evidence, dict(row, phase='ambiguous' if ambiguous else 'finished',
                                    ok=result['ok'], status=status, refs=result['refs']))
        if op in ('discover_projects', 'list_projects') and allowed.get('projects') is not None and result['ok']:
            restrict_projects(result['data'], allowed['projects'])
        if result['status'] == 401:
            result['hint'] = (f"{SERVICES[name]['label']} rejected Magazine's stored key. The owner must reconnect it "
                              "(Magazine > Manage > Host connections). Do not retry.")
        return result

    # ---- dependency preflight
    def preflight(self, requires) -> dict:
        """Checks run before any model starts. Each check calls the protected API, not the public schema."""
        checks, problems = [], []

        def add(name, ok, detail):
            checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
            if not ok:
                problems.append(detail)
        services = (requires or {}).get('services') or {}
        for name, spec in services.items():
            label, probe = SERVICES[name]['label'], SERVICES[name]['probe']
            try:
                r = self.raw_call(name, probe, {})
            except HostToolError as exc:
                add(f'{name}.reachable', False, str(exc))
                continue
            if r['status'] == 401:
                add(f'{name}.auth', False, f'{label} rejected the stored key (HTTP 401). Reconnect it in Manage.')
                continue
            if not r['ok']:
                add(f'{name}.reachable', False, f'{label} returned an invalid or unsuccessful {probe} response '
                    f'(HTTP {r["status"]}).')
                continue
            data = r['data']
            marker = 'uehl' if name == 'unreal_connector' else 'resource_kit'
            if data.get('service') != marker or not _version(data.get('version')):
                add(f'{name}.identity', False, f'{label} did not return its expected service identity and API version.')
                continue
            add(f'{name}.auth', True, f'{label} accepted the stored key ({r.get("source")}).')
            if set(spec['operations']) & UNSCOPED_CREATION:
                add(f'{name}.projects', False, 'Create or register projects with the owner before declaring them.')
                continue
            if set(spec['operations']) & (PROJECT_OPS - _OPTIONAL_PROJECT) and not spec.get('projects'):
                add(f'{name}.projects', False, 'This workflow requests project operations without declared projects.')
            try:
                if name == 'unreal_connector':
                    self._uc_checks(spec, data, requires, add)
                else:
                    self._rk_checks(spec, data, requires, add)
            except HostToolError as exc:
                add(f'{name}.checks', False, str(exc))
        return {'ok': not problems, 'checks': checks, 'problems': problems,
                'approval_note': (requires or {}).get('approval_note'),
                'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}

    def _uc_checks(self, spec, health, requires, add):
        if spec.get('capabilities') or set(spec['operations']) - {'health'}:
            caps = self.raw_call('unreal_connector', 'capabilities', {})
            ops = caps['data'].get('operations') if caps['ok'] else None
            wanted = set(spec['operations']) | set(spec.get('capabilities') or [])
            missing = sorted(wanted - set(ops)) if isinstance(ops, dict) else sorted(wanted)
            add('unreal_connector.capabilities', not missing,
                'Declared Connector operations are available.' if not missing else
                f'Connector capabilities could not verify: {", ".join(missing)}')
        if spec.get('min_api'):
            api = health['version']
            add('unreal_connector.api', _version(api) >= _version(spec['min_api']),
                f'Unreal Connector API {api} (required {spec["min_api"]}).')
        self._projects('unreal_connector', 'discover_projects', spec, add,
                       writable='submit_change' in spec['operations'])
        if requires.get('exclusive_unreal'):
            running = health.get('unreal_processes_running')
            if type(running) is not int or running < 0:
                add('unreal_connector.unreal_idle', False, 'Unreal Connector did not report running Unreal processes.')
                return
            add('unreal_connector.unreal_idle', not running,
                'No Unreal process is running.' if not running else
                'An Unreal process is already running; close it or wait before starting this workflow.')

    def _projects(self, name, operation, spec, add, *, writable=False):
        if spec.get('projects'):
            found = self.raw_call(name, operation, {})
            rows = found['data'].get('projects') if found['ok'] else None
            projects = ({project_identifier(row): row for row in rows if project_identifier(row)}
                        if isinstance(rows, list) else {})
            missing = sorted(set(spec['projects']) - projects.keys())
            add(f'{name}.projects', not missing, 'Declared projects are registered.' if not missing else
                f'The service did not verify these projects: {", ".join(missing)}')
            if writable and not missing:
                read_only = [p for p in spec['projects'] if projects[p].get('access') != 'write']
                add(f'{name}.project_write', not read_only, 'Project write access verified.' if not read_only else
                    f'The owner must enable project write access for: {", ".join(read_only)}')

    def _rk_checks(self, spec, who, requires, add):
        if spec.get('min_api'):
            add('resource_kit.api', _version(who['version']) >= _version(spec['min_api']),
                f'Resource Kit API {who["version"]} (required {spec["min_api"]}).')
        ops = set(spec['operations']) | set(spec.get('capabilities') or [])
        unsupported = sorted(ops - set(RK_SCOPES))
        if unsupported:
            add('resource_kit.capabilities', False, f'Unrecognised Resource Kit operations: {", ".join(unsupported)}')
        required = set(spec.get('scopes') or []) | {RK_SCOPES[o] for o in ops if o in RK_SCOPES}
        if spec.get('projects'):
            required.add('library')
        exclusive = requires.get('exclusive_unreal') and 'unreal_connector' not in requires['services']
        if exclusive:
            required.add('unreal')
        scopes = who.get('scopes')
        have = {s for s in scopes if isinstance(s, str)} if isinstance(scopes, list) else set()
        missing = sorted(required - have)
        add('resource_kit.scopes', not missing, 'Key scopes are sufficient.' if not missing else
            f'The Resource Kit key lacks scope(s): {", ".join(missing)}. Check AI connections in Resource Kit.')
        self._projects('resource_kit', 'list_projects', spec, add,
                       writable=bool(set(spec['operations']) & {'write_project_file', 'stage_asset_for_project',
                                                                'export_project_file'}))
        if exclusive and not missing:
            status = self.raw_call('resource_kit', 'get_unreal_status', {})
            editor = status['data'].get('editor') if status['ok'] else None
            idle = isinstance(editor, dict) and editor.get('running') is False
            add('resource_kit.unreal_idle', idle, 'No Unreal Editor is running.' if idle else
                'Resource Kit could not confirm that Unreal Editor is idle.')
