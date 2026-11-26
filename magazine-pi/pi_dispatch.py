"""Fresh Pi workers behind the unchanged injected dispatch boundary.

One worker process per attempt. The process is the cancellation unit: STOP and
Antiloop ask the SDK to abort and wait for its acknowledgement; a timeout or an
unacknowledged worker is terminated by process group and waited for.

Every worker is started through ``sandbox.py``: a kernel-enforced (Landlock)
filesystem boundary that leaves the configured project workspace, this
attempt's own session/publication folders and a private scratch folder
writable, and Magazine's state, Pi's data directory and configuration
unreadable and unwritable. The worker receives copies of the model
configuration it needs, and no Pi host credentials in its environment.
"""
import asyncio
import json
import os
import re
import shutil
import signal
import stat
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from magazine.durability import atomic_write
import sandbox

SANDBOX_FAILED = b'MAGAZINE_SANDBOX_FAILED '
# Workers receive an allow-listed environment: never Pi's own configuration or
# the host access token (PI_WEB_TOKEN). Operators may pass more names through
# with config "workerEnv".
_ENV_ALLOW = re.compile(r'^(PATH|LANG|LANGUAGE|LC_[A-Z_]+|TZ|TERM|NODE_ENV|NODE_EXTRA_CA_CERTS|SSL_CERT_(FILE|DIR)|'
                        r'(HTTP|HTTPS|NO|ALL)_PROXY|(http|https|no|all)_proxy|OLLAMA_HOST|MAGAZINE_PI_SDK)$')
# A worker's own error line, shown when it dies before reporting. Secrets are
# masked with the export redaction plus credential shapes common in error text.
_ERROR_LINE = re.compile(r'^([A-Za-z_.]*Error|[A-Za-z_.]*Exception)\b')
_AUTH_HEADER = re.compile(r'(?i)(\b(?:proxy-)?authorization\s*[:=]\s*["\']?)(?:[A-Za-z-]+\s+)?[^\s"\',;}]+')
_TOKEN_SHAPES = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{6,}|AIza[0-9A-Za-z_-]{10,}|gh[pousr]_[A-Za-z0-9]{10,}|'
                           r'github_pat_[A-Za-z0-9_]{10,}|xox[abposr]-[A-Za-z0-9-]{6,}|'
                           r'eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,})')
_QUERY_SECRET = re.compile(r'(?i)([?&](?:key|api[_-]?key|token|access_token|auth|sig|signature)=)[^&\s]+')
_CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')


def mask_secrets(text):
    from run_export import redact
    text = _AUTH_HEADER.sub(lambda m: m.group(1) + '[REDACTED]', text)
    text = redact(text)
    text = _TOKEN_SHAPES.sub('[REDACTED]', text)
    return _QUERY_SECRET.sub(lambda m: m.group(1) + '[REDACTED]', text)


# Device files a worker may write; never the whole of /dev (terminals of other processes).
_DEVICES = ('/dev/null', '/dev/zero', '/dev/full', '/dev/random', '/dev/urandom', '/dev/tty')
_PR_SET_CHILD_SUBREAPER = 36
_LIVE_WORKERS = set()  # supervisor PIDs of workers this service is running
_SPAWNING = [0]        # spawns in progress: their PIDs are not yet in _LIVE_WORKERS


_REAPING = [False]     # enabled only in the dedicated service process


def become_subreaper():
    """Orphaned worker descendants re-parent to this service, not to PID 1.

    Called only by the service's own entry point, whose children are all
    workers. Embedding processes (tests, tools) keep their own children.
    """
    try:
        import ctypes
        ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
        _REAPING[0] = True
    except (OSError, AttributeError):
        pass


def reap_orphans():
    """Kill and reap descendants of finished workers that re-parented to this service.

    Deferred while a worker is being spawned (its PID is not registered yet);
    the service also calls this on every request, so a deferral is short.
    """
    if _SPAWNING[0] or not _REAPING[0]:
        return
    me = os.getpid()
    for _ in range(20):
        found = []
        for name in os.listdir('/proc'):
            if not name.isdigit() or int(name) in _LIVE_WORKERS:
                continue
            try:
                with open(f'/proc/{name}/stat', 'rb') as stream:
                    data = stream.read()
                if int(data[data.rindex(b')') + 2:].split()[1]) == me:
                    found.append(int(name))
            except (OSError, ValueError, IndexError):
                continue
        if not found:
            return
        for pid in found:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
_AGENT_FILES = ('auth.json', 'models.json')


class SandboxUnavailable(RuntimeError):
    """Worker confinement is required but cannot be established."""


def _within(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip('/') + '/')


def readable_complement(protected):
    """Top-down list of paths covering '/' except the protected subtrees."""
    protected = [os.path.realpath(p) for p in protected if p]
    out = []

    def walk(directory):
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            return
        for name in names:
            child = os.path.join(directory, name)
            real = os.path.realpath(child)
            if any(real == p or _within(real, p) for p in protected):
                continue  # the protected subtree itself (or a link into it)
            if any(_within(p, real) for p in protected):
                if os.path.isdir(child) and not os.path.islink(child):
                    walk(child)  # an ancestor of a protected subtree: descend
                # A link to an ancestor is never granted: Landlock would follow it
                # and grant the whole target, protected subtree included.
                continue
            out.append(child)
    walk('/')
    return out


def sandbox_mode(config):
    mode = config.get('workerSandbox', 'required')
    if mode not in ('required', 'off'):
        raise SandboxUnavailable('workerSandbox must be "required" or "off"')
    return mode


def protected_roots(config):
    root = config['dataDir']
    # The native plugin always supplies piDataDir (the host's data directory).
    roots = [root, config.get('piDataDir'), config.get('agentDir')]
    roots += list(config.get('workerProtectedPaths') or [])
    return [r for r in roots if r]


def validate_worker_paths(config):
    """Operator-granted extra paths: absolute, never Magazine/Pi state when writable."""
    protected = protected_roots(config)
    names = config.get('workerEnv') or []
    if not isinstance(names, list) or not all(isinstance(n, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', n)
                                             and not n.startswith('PI_WEB_') for n in names):
        raise SandboxUnavailable('workerEnv must be a list of environment variable names (PI_WEB_* is never passed)')
    for key, writable in (('workerReadPaths', False), ('workerWritePaths', True)):
        values = config.get(key) or []
        if not isinstance(values, list) or not all(isinstance(v, str) and os.path.isabs(v) for v in values):
            raise SandboxUnavailable(f'{key} must be a list of absolute paths')
        for value in values:
            if writable and any(_within(value, p) or _within(p, value) for p in protected):
                raise SandboxUnavailable(f'{key} entry {value} overlaps Magazine or Pi state and cannot be writable')


class WorkerFailure(ValueError):
    """The worker reported a failure before a saved outcome existed.

    ``reason`` is the fixed text used to classify the failure; ``detail`` (the
    worker's own last error line, if any) is shown to people but never decides
    how the failure is classified.
    """

    def __init__(self, reason, detail=''):
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason = reason
        self.detail = detail


class PiDispatch:
    def __init__(self, config, session_root):
        self.config = config
        self.session_root = str(session_root)
        self.process = None
        self.reader = None
        self.abort_receipt = None
        self.active_chat_id = None
        self.active_session_file = None
        self.cancel_requested = False
        self.cancel_confirmed = False
        self.on_event = None
        self.terminated = None
        self.host = None
        self._host_tasks = set()

    def _prepare_scratch(self, request):
        """Private per-worker folder with copies of the model configuration."""
        temp = Path(self.config['dataDir']) / 'temp'
        temp.mkdir(mode=0o700, exist_ok=True)
        scratch = temp / f'worker-{uuid.uuid4().hex}'
        for sub in ('', 'home', 'tmp', 'agent'):
            (scratch / sub).mkdir(mode=0o700)
        agent = Path(self.config['agentDir'])
        for name in _AGENT_FILES:
            source = agent / name
            if source.is_file():
                shutil.copyfile(source, scratch / 'agent' / name)
                os.chmod(scratch / 'agent' / name, 0o600)
        store = Path(self.config['dataDir']) / 'models-store.json'
        if store.is_file() and not store.is_symlink():
            shutil.copyfile(store, scratch / 'agent' / 'models-store.json')
        request['agentDir'] = str(scratch / 'agent')
        request['modelsStorePath'] = str(scratch / 'agent' / 'models-store.json')
        return scratch

    def _policy(self, request, scratch, writable):
        """Kernel policy for the worker and for the nested tool domain.

        Every grant carries the real path computed here; the launcher refuses a
        grant whose opened descriptor resolves anywhere else, or that contains
        protected state, or lies inside it without being one of the attempt's
        own folders.
        """
        plugin = str(Path(__file__).resolve().parent)
        protected = [os.path.realpath(p) for p in protected_roots(self.config)]
        cwd = os.path.realpath(self.config['cwd'])
        default_workspace = os.path.realpath(os.path.join(self.config['dataDir'], 'workspace'))

        def grant(path, protected_ok=False):
            real = os.path.realpath(path)
            for root in protected:
                if _within(root, real) and real != root:
                    raise SandboxUnavailable(f'Worker access to {path} would include Magazine or Pi state; '
                                             'configure a project workspace that does not contain the Pi data directory')
                if _within(real, root) and not protected_ok:
                    raise SandboxUnavailable(f'Worker write access to {path} is inside Magazine or Pi state')
            return {'path': str(path), 'real': real, 'protected_ok': protected_ok}

        attempt_dirs = [grant(p, True) for p in writable if os.path.realpath(p) != cwd]
        workspace = grant(cwd, protected_ok=cwd == default_workspace)
        extra_write = [grant(p) for p in self.config.get('workerWritePaths') or []]
        private = [grant(scratch / 'home', True), grant(scratch / 'tmp', True)]
        agent_copy = [grant(scratch / 'agent', True)]  # the SDK locks its config files; tools never get this
        devices = [{'path': d, 'real': os.path.realpath(d), 'protected_ok': False}
                   for d in _DEVICES if os.path.exists(d) and not any(_within(os.path.realpath(d), q) for q in protected)]
        # Read grants: everything outside protected state, plus the worker's own
        # code, the SDK tree and explicitly configured paths (read-only).
        read = [{'path': p, 'real': None, 'protected_ok': False} for p in readable_complement(protected)]
        read.append({'path': plugin, 'real': plugin, 'protected_ok': True})
        read.append({'path': str(scratch), 'real': os.path.realpath(scratch), 'protected_ok': True})
        for path in list(self.config.get('extensionPaths') or []) + list(self.config.get('workerReadPaths') or []):
            literal = os.path.normpath(os.path.abspath(path))
            if os.path.realpath(path) != literal:
                raise SandboxUnavailable(f'Configured worker path {path} contains a symbolic link; configure its real '
                                         'path instead')
            read.append({'path': literal, 'real': literal,
                         'protected_ok': any(_within(literal, p) for p in protected)})
        sdk = request.get('sdkDir')
        if sdk and any(_within(sdk, p) for p in protected):
            node_modules = os.path.realpath(sdk)
            while os.path.basename(node_modules) != 'node_modules' and node_modules != '/':
                node_modules = os.path.dirname(node_modules)
            target = node_modules if node_modules != '/' else os.path.realpath(sdk)
            read.append({'path': target, 'real': target, 'protected_ok': True})
        worker_write = [workspace] + attempt_dirs + agent_copy + private + extra_write + devices
        tool_write = [workspace] + private + extra_write + devices  # never the session/publish folders
        # The tool domain's rules only keep matching on unstable-inode mounts
        # because the launcher pins the worker's grants: tools never get more.
        if not all(g in worker_write for g in tool_write):
            raise SandboxUnavailable('Worker sandbox: a tool grant is not also a worker grant')
        return {'read': read, 'write': worker_write, 'protected': protected, 'min_abi': sandbox.MIN_ABI,
                'required': [g['path'] for g in worker_write if g not in devices], 'deny_unix_sockets': True,
                'tool': {'write': tool_write, 'protected': protected}}

    def _env(self, scratch):
        names = set(self.config.get('workerEnv') or [])
        env = {k: v for k, v in os.environ.items() if _ENV_ALLOW.match(k) or k in names}
        env.update(PI_OFFLINE='1')
        if scratch is not None:
            env.update(HOME=str(scratch / 'home'), TMPDIR=str(scratch / 'tmp'))
        return env

    async def _spawn(self, request):
        writable = getattr(self, '_writable', ())
        worker = [self.config.get('node', 'node'), str(Path(__file__).with_name('worker.mjs'))]
        mode = sandbox_mode(self.config)
        self.scratch = scratch = None
        argv = worker
        if mode == 'required':
            validate_worker_paths(self.config)
            if sandbox.abi_version() < sandbox.MIN_ABI:
                raise SandboxUnavailable(
                    f'Worker sandbox unavailable: this kernel/container does not provide Linux Landlock ABI '
                    f'{sandbox.MIN_ABI} or newer, so Magazine cannot stop workers from modifying its own state. '
                    'Nothing was started. To accept unconfined workers anyway, set "workerSandbox": "off" in the '
                    'plugin config.json.')
            self.scratch = scratch = self._prepare_scratch(request)
            policy_file = scratch / 'policy.json'
            policy_file.write_text(json.dumps(self._policy(request, scratch, writable)), encoding='utf-8')
            argv = [self.config.get('python') or sys.executable, '-I', str(Path(__file__).with_name('sandbox.py')),
                    '--policy', str(policy_file), '--'] + worker
            request['requireSandbox'] = True
            request['shellCommandPrefix'] = sandbox.TOOL_PREFIX
        # The worker's own diagnostics go to a file in its private folder (which
        # it cannot replace: the folder itself is read-only to it), so a worker
        # that dies before reporting can still say why.
        errors = None
        if scratch is not None:
            errors = os.open(scratch / 'stderr.txt', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        _SPAWNING[0] += 1
        try:
            self.process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=errors if errors is not None else asyncio.subprocess.DEVNULL,
                cwd=self.config['cwd'], limit=8 * 1024 * 1024,
                env=self._env(scratch), start_new_session=True)
            _LIVE_WORKERS.add(self.process.pid)
        finally:
            _SPAWNING[0] -= 1
            if errors is not None:
                os.close(errors)
        self.process.stdin.write((json.dumps(request) + '\n').encode())
        await self.process.stdin.drain()

    def _discard_scratch(self):
        scratch, self.scratch = getattr(self, 'scratch', None), None
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)  # fd-based; never follows links

    async def catalogue(self):
        return await self._request({'action': 'catalogue'})

    async def __call__(self, prompt, model_id):
        """pi.5 contract: exact prompt, exact model, final text only."""
        result = await self.run(prompt, model_id)
        if result.get('finish') != 'stop':
            raise ValueError(f"Pi worker did not finish normally: {result.get('finish') or 'missing response'}")
        if not result.get('text', '').strip():
            raise ValueError('Pi worker has no final text')
        return result['text']

    async def run(self, prompt, model_id, *, publish=None, timeout=None, on_event=None, session_dir=None, host=None):
        """One fresh saved session. Returns the worker's structured outcome.

        ``host`` (pi.10, optional): trusted handler for the worker's host-tool and checkpoint calls. Only its
        ``worker_request`` (tool names and descriptions, no credentials) reaches the worker."""
        self.host = host
        self._host_tasks = set()
        self.cancel_requested = False
        self.cancel_confirmed = False
        self.active_chat_id = None
        self.active_session_file = None
        self.terminated = None
        self.on_event = on_event
        self.abort_receipt = asyncio.get_running_loop().create_future()
        session_dir = Path(session_dir or self.session_root)
        session_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        request = {'action': 'run', 'prompt': prompt, 'model': model_id,
                   'tools': self.config.get('tools', []), 'sessionDir': str(session_dir)}
        writable = [self.config['cwd'], session_dir]
        if publish is not None:
            request['publish'] = publish
            writable.append(Path(publish['resultPath']).parent)
        if host is not None:
            request['host'] = host.worker_request()
        result = await self._request(request, timeout, writable)
        if self.cancel_confirmed:
            raise asyncio.CancelledError('Pi worker abort requested')
        if result.get('model') != model_id or not isinstance(result.get('text'), str):
            raise ValueError('Pi dispatch response identity mismatch')
        return result

    async def _request(self, data, timeout=None, writable=()):
        request = {**self.config, **data, 'dataDir': self.config['dataDir']}
        for key in ('workerReadPaths', 'workerWritePaths', 'workerProtectedPaths', 'workerSandbox', 'workerEnv'):
            request.pop(key, None)
        self._writable = writable
        try:
            await self._spawn(request)
        except BaseException:
            self._discard_scratch()
            raise
        self.reader = asyncio.create_task(self._read())
        limit = timeout if timeout is not None else self.config.get('timeoutSeconds', 1800)
        try:
            return await asyncio.wait_for(asyncio.shield(self.reader), limit)
        finally:
            if self.process.returncode is None:
                # Complete prompt has returned, or a dispatch failure occurred.
                # Killing this child prevents an uncertain worker from outliving its claim.
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass  # Worker exited between returncode check and signal.
                try:
                    await asyncio.wait_for(self.process.wait(), 5)
                except asyncio.TimeoutError:
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await self.process.wait()
            self.terminated = self.process.returncode is not None
            self._kill_leftovers()
            if not self.reader.done():
                self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
            self._discard_scratch()

    def _kill_leftovers(self):
        """Nothing a worker started may outlive it, even if its supervisor was killed."""
        if self.process is None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGKILL)  # the worker's own process group
        except (ProcessLookupError, PermissionError):
            pass
        _LIVE_WORKERS.discard(self.process.pid)
        reap_orphans()  # descendants that escaped the group re-parent to this service

    def _stderr_tail(self, limit=400):
        """The worker's last error line (secrets masked), or ''."""
        scratch = getattr(self, 'scratch', None)
        if scratch is None:
            return ''
        try:
            fd = os.open(scratch / 'stderr.txt', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return ''
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return ''
            size = info.st_size
            os.lseek(fd, max(0, size - 8192), os.SEEK_SET)
            text = os.read(fd, 8192).decode('utf-8', 'replace')
        except OSError:
            return ''
        finally:
            os.close(fd)
        lines = [_CONTROL.sub('', line).strip() for line in text.splitlines()]
        lines = [line for line in lines if line]
        errors = [line for line in lines if _ERROR_LINE.match(line)]
        line = errors[-1] if errors else (lines[-1] if lines else '')
        return mask_secrets(line)[:limit]

    def _emit(self, event):
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                pass  # observers never break the worker channel

    async def _read(self):
        result = None
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            if line.startswith(SANDBOX_FAILED):
                try:
                    detail = json.loads(line[len(SANDBOX_FAILED):]).get('error')
                except ValueError:
                    detail = 'unreadable launcher diagnostic'
                raise WorkerFailure(f'Worker sandbox could not be established: {detail}')
            event = json.loads(line)
            kind = event.get('event')
            if kind == 'session':
                self.active_chat_id = event.get('id')
                self.active_session_file = event.get('file')
                atomic_write(str(Path(self.session_root).parent/'active-worker.json'),
                    json.dumps({'session_id':self.active_chat_id,'phase':'created'}).encode())
                self._emit(event)
            elif kind == 'streaming':
                atomic_write(str(Path(self.session_root).parent/'active-worker.json'),
                    json.dumps({'session_id':self.active_chat_id,'phase':'streaming'}).encode())
                self._emit(event)
            elif kind in ('assistant', 'tool_result', 'published'):
                self._emit(event)
            elif kind == 'host_call':
                task = asyncio.create_task(self._answer_host(event))
                self._host_tasks.add(task)
                task.add_done_callback(self._host_tasks.discard)
            elif kind == 'abort':
                self.cancel_confirmed = event.get('confirmed') is True
                if self.abort_receipt and not self.abort_receipt.done():
                    self.abort_receipt.set_result(event.get('confirmed') is True)
            elif kind == 'result':
                result = event
                break
        if result is None:
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except asyncio.TimeoutError:
                pass
            code = self.process.returncode
            ended = ('' if code is None else f'signal {-code}' if code < 0 else f'exit code {code}')
            detail = '; '.join(x for x in (ended, self._stderr_tail()) if x)
            raise WorkerFailure('Pi worker exited without a result', detail)
        if not result.get('ok'):
            if self.cancel_requested:
                # Let the abort acknowledgement arrive before settling failure.
                if self.abort_receipt and not self.abort_receipt.done():
                    try:
                        while True:
                            tail = await asyncio.wait_for(self.process.stdout.readline(), 2)
                            if not tail:
                                break
                            receipt = json.loads(tail)
                            if receipt.get('event') == 'abort':
                                self.cancel_confirmed = receipt.get('confirmed') is True
                                self.abort_receipt.set_result(receipt.get('confirmed') is True)
                                break
                    except asyncio.TimeoutError:
                        pass
                raise asyncio.CancelledError('Pi worker aborted')
            raise WorkerFailure(result.get('error', 'Pi worker failed'))
        return result['result']

    async def _answer_host(self, event):
        """pi.10: perform one worker host call here, outside the sandbox, and answer on the worker's stdin."""
        call_id = str(event.get('id', ''))[:64]
        host = getattr(self, 'host', None)
        try:
            if host is None:
                raise ValueError('Host tools are not enabled for this workflow')
            reply = {'action': 'host_result', 'id': call_id, 'ok': True, 'result': await host.handle(event)}
        except Exception as exc:  # noqa: BLE001 - every refusal goes back to the model as a tool error
            reply = {'action': 'host_result', 'id': call_id, 'ok': False, 'error': mask_secrets(str(exc))[:2000]}
        self._emit({'event': 'host_answer', 'id': call_id, 'ok': reply['ok']})
        process = self.process
        if process is None or process.returncode is not None or process.stdin is None:
            return
        try:
            process.stdin.write((json.dumps(reply) + '\n').encode())
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def terminate(self):
        """Force the active worker's process group to exit; True once it has."""
        process = self.process
        if process is None:
            return True
        if process.returncode is not None:
            self._kill_leftovers()
            return True
        for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                return True
            try:
                await asyncio.wait_for(process.wait(), wait)
                return True
            except asyncio.TimeoutError:
                continue
        return process.returncode is not None

    async def cancel_active(self):
        process = self.process
        if process is None or process.returncode is not None or self.reader is None or self.reader.done():
            return SimpleNamespace(confirmed=False, active=False, detail='No active Pi worker')
        self.cancel_requested = True
        try:
            process.stdin.write(b'{"action":"abort"}\n')
            await process.stdin.drain()
        except (BrokenPipeError,ConnectionResetError):
            return SimpleNamespace(confirmed=False,active=True,detail='Pi worker exited before acknowledging abort')
        try:
            confirmed = await asyncio.wait_for(asyncio.shield(self.abort_receipt), 10)
        except (asyncio.TimeoutError, BrokenPipeError):
            confirmed = False
        return SimpleNamespace(confirmed=confirmed, active=True,
                               detail='Pi SDK abort acknowledged and worker idle' if confirmed else 'Pi abort unconfirmed')
