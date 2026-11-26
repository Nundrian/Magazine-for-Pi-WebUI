"""Deterministic fakes for pi.6 tests. No model, no GPU, loopback HTTP only.

ScriptedProvider speaks the OpenAI chat-completions streaming dialect that the
real Pi SDK (pi-ai openai-completions) consumes. A cartridge prompt's first
line ``SCRIPT <name>`` selects a behaviour; the provider never generates text.
"""
from __future__ import annotations

import base64
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DONE = '{"status":"DONE"}'


class ScriptedProvider:
    def __init__(self):
        self.host_replies = []
        self.requests = []
        self.attempts = {}          # script name -> attempt ids seen (ordered)
        self.hold = threading.Event()
        self.holding = threading.Event()
        self.usage = None           # e.g. {'prompt_tokens': 100, 'completion_tokens': 7}
        self.lock = threading.Lock()
        self.gates = {}             # name -> threading.Event (script gate:NAME waits for it)
        self.reached = {}           # name -> threading.Event (set when a gate is reached)
        self.inflight = 0
        self.max_inflight = 0
        self.sequences = {}         # name -> list of bash commands (script bashseq:NAME); 'HOLD' blocks
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                with fixture.lock:
                    fixture.requests.append(body)
                    fixture.inflight += 1
                    fixture.max_inflight = max(fixture.max_inflight, fixture.inflight)
                try:
                    fixture.respond(self, body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with fixture.lock:
                        fixture.inflight -= 1

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.server.server_port}/v1'

    def gate(self, name):
        return self.gates.setdefault(name, threading.Event())

    def wait_reached(self, name, timeout=30):
        return self.reached.setdefault(name, threading.Event()).wait(timeout)

    def first_prompts(self):
        """Cartridge prompt first lines, in the order their first round arrived."""
        out = []
        for r in self.requests:
            if any(m['role'] == 'assistant' for m in r['messages']):
                continue
            user = next(m for m in r['messages'] if m['role'] == 'user')['content']
            user = user if isinstance(user, str) else ''.join(p.get('text', '') for p in user)
            out.append(user.splitlines()[1] if user.startswith('SCRIPT') and len(user.splitlines()) > 1 else user.splitlines()[0])
        return out

    def close(self):
        self.hold.set()
        for event in self.gates.values():
            event.set()
        self.server.shutdown()
        self.server.server_close()

    # ------------------------------------------------------------ dialect
    def stream(self, handler, model, deltas, reason, usage=None):
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/event-stream')
        handler.end_headers()
        def chunk(delta, finish=None):
            value = {'id': 'chat-fake', 'object': 'chat.completion.chunk', 'created': int(time.time()),
                     'model': model, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
            handler.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode())
            handler.wfile.flush()
        chunk({'role': 'assistant'})
        for delta in deltas[:-1]:
            chunk(delta)
        chunk(deltas[-1], reason)
        usage = usage if usage is not None else self.usage
        if usage:
            value = {'id': 'chat-fake', 'object': 'chat.completion.chunk', 'created': int(time.time()),
                     'model': model, 'choices': [], 'usage': usage}
            handler.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode())
        handler.wfile.write(b'data: [DONE]\n\n')
        handler.wfile.flush()

    def text(self, handler, model, text, reason='stop'):
        self.stream(handler, model, [{'content': text}], reason)

    def call(self, handler, model, name, arguments, call_id=None):
        self.stream(handler, model, [{'tool_calls': [{'index': 0, 'id': call_id or 'call-' + uuid.uuid4().hex[:10],
            'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}], 'tool_calls')

    def calls(self, handler, model, items):
        self.stream(handler, model, [{'tool_calls': [{'index': i, 'id': 'call-' + uuid.uuid4().hex[:10], 'type': 'function',
            'function': {'name': n, 'arguments': json.dumps(a)}} for i, (n, a) in enumerate(items)]}], 'tool_calls')

    # ------------------------------------------------------------ scripts
    def respond(self, handler, body):
        model = body['model']
        messages = body['messages']
        user = next(m for m in messages if m['role'] == 'user')['content']
        if isinstance(user, list):
            user = ''.join(p.get('text', '') for p in user)
        first = user.splitlines()[0] if user else ''
        script = first[len('SCRIPT '):].strip() if first.startswith('SCRIPT ') else first
        attempt = (re.search(r'Attempt: ([0-9a-f]{32})', user) or [None, None])[1]
        with self.lock:
            seen = self.attempts.setdefault(script, [])
            if attempt and attempt not in seen:
                seen.append(attempt)
            number = seen.index(attempt) + 1 if attempt else 1
        rounds = sum(1 for m in messages if m['role'] == 'assistant')
        last = messages[-1]
        tools = [t['function']['name'] for t in body.get('tools') or []]
        out_dir = (re.search(r'Save your output files in: (\S+)', user) or [None, None])[1]
        name, _, arg = script.partition(':')

        def publish(**kw):
            self.call(handler, model, 'magazine_publish_result', kw or {'verified': True, 'note': 'ok', 'checks': ['looked']})

        def finish(text='Finished the task.'):
            self.text(handler, model, text + '\n' + DONE)

        # pi.10 scripts: host broker and deterministic checkpoint tools
        if name == 'hostcall':
            if rounds == 0:
                op, _, pid = arg.partition('@')
                return self.call(handler, model, 'magazine_host', {'service': 'unreal_connector', 'operation': op,
                                                                   'arguments': {'project_id': pid} if pid else {}})
            if rounds == 1:
                self.host_replies.append(messages[-1].get('content'))
                return publish()
            return finish()
        if name == 'checkpoint':
            report = (re.search(r'REPORT_PATH=(\S+)', user) or [None, None])[1]
            if rounds == 0:
                return self.call(handler, model, 'write', {'path': report, 'content': json.dumps({'step_id': arg, 'status': 'DONE'})})
            if rounds == 1:
                return self.call(handler, model, 'magazine_checkpoint', {})
            if rounds == 2:
                self.host_replies.append(messages[-1].get('content'))
                return publish()
            return finish()
        if name == 'arch-error':
            handler.send_response(500); handler.end_headers()
            handler.wfile.write(b'{"error":{"message":"llama runner: unknown model architecture: \'ornith\'"}}'); return
        if name == 'bashseq':
            commands = self.sequences.get(arg, [])
            if rounds < len(commands):
                if commands[rounds] == 'HOLD':
                    self.holding.set()
                    handler.send_response(200); handler.send_header('Content-Type', 'text/event-stream'); handler.end_headers()
                    value = {'id': 'x', 'object': 'chat.completion.chunk', 'created': 0, 'model': model,
                             'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'Working'}, 'finish_reason': None}]}
                    handler.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode()); handler.wfile.flush()
                    self.hold.wait(60)
                    return
                return self.call(handler, model, 'bash', {'command': commands[rounds]})
            if rounds == len(commands):
                return publish(verified=True, note='done', files=[], checks=['claimed'])
            return finish()
        if name == 'error-once' and number == 1:
            handler.send_response(500); handler.end_headers()
            handler.wfile.write(b'{"error":{"message":"injected provider failure"}}'); return
        if name == 'hold' or (name == 'hold-once' and number == 1):
            self.holding.set()
            handler.send_response(200); handler.send_header('Content-Type', 'text/event-stream'); handler.end_headers()
            value = {'id': 'x', 'object': 'chat.completion.chunk', 'created': 0, 'model': model,
                     'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'Working'}, 'finish_reason': None}]}
            handler.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode()); handler.wfile.flush()
            self.hold.wait(60)
            return
        if name == 'gate' and rounds == 0:
            self.reached.setdefault(arg, threading.Event()).set()
            self.gates.setdefault(arg, threading.Event()).wait(60)
        if name == 'text':
            return self.text(handler, model, arg or 'PLAIN_TEXT_RESPONSE')
        if name == 'halt':
            return self.text(handler, model, 'Cannot continue.\n{"status":"HALT","reason":"fixture halt"}')
        if name == 'nodone' or (name == 'nodone-until' and number < int(arg)):
            if rounds == 0:
                return publish()
            return self.text(handler, model, 'I ran out of time before finishing.')
        if name == 'nopublish-once' and number == 1:
            return finish('Done but forgot to publish.')
        if name == 'length-once' and number == 1:
            return self.text(handler, model, 'truncated', 'length')
        if name == 'loop':
            # identical ls calls with identical results until Antiloop intervenes
            limit = int(arg or 12)
            if rounds < limit:
                return self.call(handler, model, 'ls', {'path': '.'})
            if rounds == limit:
                return publish()
            return finish()
        if name == 'parallel':
            if rounds == 0:
                return self.calls(handler, model, [('ls', {'path': '.'})] * 5)
            if rounds == 1:
                return publish()
            return finish()
        if name == 'value':
            if rounds == 0:
                return publish(payload_json=json.dumps({'answer': int(arg or 42), 'result': {'step': 'x'}}))
            return finish()
        if name == 'dupkey':
            if rounds == 0:
                return publish(payload_json='{"answer": 1, "answer": 2}')
            if rounds == 1:
                return publish(payload_json=json.dumps({'answer': 3}))
            return finish()
        if name == 'file':
            if rounds == 0:
                return self.call(handler, model, 'write', {'path': f'{out_dir}/{arg or "out.txt"}', 'content': 'FILE_CONTENT_' + (arg or 'x')})
            if rounds == 1:
                return publish(verified=True, note='wrote file', files=[arg or 'out.txt'], checks=['read back'])
            return finish()
        if name == 'publish-hold':
            if rounds == 0:
                return publish()
            self.holding.set()
            self.hold.wait(60)
            return finish()
        # default: publish then DONE
        if rounds == 0:
            return publish()
        return finish()


class RPC:
    def __init__(self, root, config):
        self.root = Path(root)
        self.config = config
        self.responses = queue.Queue()
        self.stderr = (self.root.parent / 'service-stderr.log').open('ab')
        env = {**os.environ, 'PYTHONPATH': str(ROOT / 'magazine-pi')}
        self.process = subprocess.Popen([sys.executable, str(ROOT / 'magazine-pi/service.py'), str(root), json.dumps(config)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, text=True, bufsize=1, env=env)
        threading.Thread(target=self._reader, daemon=True).start()
        assert self.responses.get(timeout=30) == {'event': 'ready'}

    def _reader(self):
        for line in self.process.stdout:
            self.responses.put(json.loads(line))

    def call(self, action, data=None, ok=True, timeout=60):
        rid = uuid.uuid4().hex
        self.process.stdin.write(json.dumps({'id': rid, 'action': action, 'data': data or {}}) + '\n')
        self.process.stdin.flush()
        value = self.responses.get(timeout=timeout)
        assert value['id'] == rid, value
        if ok:
            assert value['ok'], value
            return value['result']
        return value

    def close(self, kill=False):
        if self.process.poll() is None:
            if kill:
                self.process.kill()
            else:
                self.process.stdin.close()
            self.process.wait(timeout=30)
        self.stderr.close()


def agent_dir(path, provider_url, models=('model-a', 'model-b')):
    path.mkdir(parents=True, exist_ok=True)
    config = {'providers': {'fixture': {'baseUrl': provider_url, 'api': 'openai-completions', 'apiKey': 'fixture-key',
        'models': [{'id': name, 'name': name, 'reasoning': False, 'input': ['text'], 'contextWindow': 8192,
                    'maxTokens': 256, 'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}}
                   for name in models]}}}
    (path / 'models.json').write_text(json.dumps(config))
    (path / 'auth.json').write_text('{}')
    return path


def wait(rpc, rid, statuses, timeout=60):
    deadline = time.monotonic() + timeout
    statuses = (statuses,) if isinstance(statuses, str) else statuses
    value = None
    while time.monotonic() < deadline:
        value = rpc.call('status', {'run_id': rid})
        if value['display_status'] in statuses or value['status'] in statuses:
            if not value.get('active') or value['status'] != 'RUNNING':
                return value
        time.sleep(0.05)
    raise AssertionError(value)


def project_zip(manifest, prompts):
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('magazine.yaml', manifest)
        for name, text in prompts.items():
            archive.writestr(name, text)
    return base64.b64encode(buffer.getvalue()).decode()
