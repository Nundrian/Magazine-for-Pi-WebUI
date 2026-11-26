"""A reaped process group must not turn a verified response into failure."""
import asyncio
import signal
import pytest
import pi_dispatch as dispatch


@pytest.mark.parametrize('kill_timeout', [False, True])
def test_process_exit_during_cleanup_preserves_result(monkeypatch, kill_timeout):
    signals=[]
    class Process:
        pid=1234
        returncode=None
        waits=0
        async def wait(self):
            self.waits += 1
            if kill_timeout and self.waits == 1: raise asyncio.TimeoutError
            self.returncode=0
            return 0
    async def scenario():
        worker=dispatch.PiDispatch({'dataDir':'/unused'}, '/unused')
        async def spawn(request): worker.process=Process()
        async def read(): return {'text':'verified response'}
        worker._spawn=spawn; worker._read=read
        def disappeared(pid, sig):
            assert pid==1234
            signals.append(sig)
            raise ProcessLookupError
        monkeypatch.setattr(dispatch.os, 'killpg', disappeared)
        assert await worker._request({}) == {'text':'verified response'}
        assert worker.terminated and worker.reader.done()
    asyncio.run(scenario())
    expected = [signal.SIGTERM, signal.SIGKILL] if kill_timeout else [signal.SIGTERM]
    # pi.9: the worker's whole process group is always SIGKILLed afterwards, so
    # nothing a worker started can outlive it even if its supervisor died.
    assert signals[:len(expected)] == expected and set(signals[len(expected):]) <= {signal.SIGKILL}
    assert signals[-1] == signal.SIGKILL


def test_exit_race_does_not_hide_worker_failure(monkeypatch):
    class Process:
        pid=1234
        returncode=None
        async def wait(self): self.returncode=1; return 1
    async def scenario():
        worker=dispatch.PiDispatch({'dataDir':'/unused'}, '/unused')
        async def spawn(request): worker.process=Process()
        async def read(): raise dispatch.WorkerFailure('original worker error')
        worker._spawn=spawn; worker._read=read
        monkeypatch.setattr(dispatch.os, 'killpg', lambda *a: (_ for _ in ()).throw(ProcessLookupError()))
        with pytest.raises(dispatch.WorkerFailure, match='original worker error'):
            await worker._request({})
        assert worker.terminated
    asyncio.run(scenario())
