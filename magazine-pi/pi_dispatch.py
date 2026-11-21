"""Fresh Pi workers behind the unchanged injected dispatch boundary.

One worker process per attempt. The process is the cancellation unit: STOP and
Antiloop ask the SDK to abort and wait for its acknowledgement; a timeout or an
unacknowledged worker is terminated by process group and waited for.
"""
import asyncio
import json
import os
import signal
from pathlib import Path
from types import SimpleNamespace
from magazine.durability import atomic_write


class WorkerFailure(ValueError):
    """The worker reported a failure before a saved outcome existed."""


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

    async def _spawn(self, request):
        self.process = await asyncio.create_subprocess_exec(
            self.config.get('node', 'node'), str(Path(__file__).with_name('worker.mjs')),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=self.config['cwd'], limit=8 * 1024 * 1024,
            env={**os.environ, 'PI_OFFLINE': '1'}, start_new_session=True)
        self.process.stdin.write((json.dumps(request) + '\n').encode())
        await self.process.stdin.drain()

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

    async def run(self, prompt, model_id, *, publish=None, timeout=None, on_event=None):
        """One fresh saved session. Returns the worker's structured outcome."""
        self.cancel_requested = False
        self.cancel_confirmed = False
        self.active_chat_id = None
        self.active_session_file = None
        self.terminated = None
        self.on_event = on_event
        self.abort_receipt = asyncio.get_running_loop().create_future()
        request = {'action': 'run', 'prompt': prompt, 'model': model_id,
                   'tools': self.config.get('tools', []), 'sessionDir': self.session_root}
        if publish is not None:
            request['publish'] = publish
        result = await self._request(request, timeout)
        if self.cancel_confirmed:
            raise asyncio.CancelledError('Pi worker abort requested')
        if result.get('model') != model_id or not isinstance(result.get('text'), str):
            raise ValueError('Pi dispatch response identity mismatch')
        return result

    async def _request(self, data, timeout=None):
        request = {**self.config, **data, 'dataDir': self.config['dataDir']}
        await self._spawn(request)
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
            if not self.reader.done():
                self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)

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
            elif kind == 'abort':
                self.cancel_confirmed = event.get('confirmed') is True
                if self.abort_receipt and not self.abort_receipt.done():
                    self.abort_receipt.set_result(event.get('confirmed') is True)
            elif kind == 'result':
                result = event
                break
        if result is None:
            raise WorkerFailure('Pi worker exited without a result')
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

    async def terminate(self):
        """Force the active worker's process group to exit; True once it has."""
        process = self.process
        if process is None or process.returncode is not None:
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
