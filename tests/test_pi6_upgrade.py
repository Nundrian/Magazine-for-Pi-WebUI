"""Update/data preservation: real pi.5 records opened by pi.6, byte-for-byte.

The released pi.5-rc.1 plugin ZIP (MAGAZINE_PI5_PLUGIN_ZIP) creates genuine
pi.5 runs with the real SDK and the fake provider; pi.6 then reads, exports
and starts them. No model is involved.
"""
import base64
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import uuid
import zipfile
from pathlib import Path

import pytest

from pi6_fakes import RPC, ScriptedProvider, agent_dir, wait

PI5_ZIP = os.environ.get("MAGAZINE_PI5_PLUGIN_ZIP")
SDK = os.environ.get("MAGAZINE_PI_SDK")
SDK_VERSION = json.loads((Path(SDK) / "package.json").read_text())["version"] if SDK and (Path(SDK) / "package.json").is_file() else None
# The released pi.5 plugin refuses every SDK except 1.0.2, so genuine pi.5
# records can only be produced (and upgraded) on the 0.96.1/1.0.2 host.
pytestmark = pytest.mark.skipif(not PI5_ZIP or SDK_VERSION != "1.0.2",
                                reason="needs MAGAZINE_PI5_PLUGIN_ZIP and SDK 1.0.2 (pi.5's only supported SDK)")


class OldRPC(RPC):
    """The released pi.5 service, launched from its own extracted directory."""

    def __init__(self, plugin, root, config):
        self.root, self.config, self.responses = Path(root), config, queue.Queue()
        self.stderr = (self.root.parent / "pi5-stderr.log").open("ab")
        env = {**os.environ, "PYTHONPATH": str(plugin)}
        self.process = subprocess.Popen([sys.executable, str(plugin / "service.py"), str(root), json.dumps(config)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, text=True, bufsize=1, env=env)
        threading.Thread(target=self._reader, daemon=True).start()
        assert self.responses.get(timeout=30) == {"event": "ready"}


def snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(root).rglob("*") if p.is_file() and not p.is_symlink()}


def old_status(rpc, rid, status):
    import time
    for _ in range(400):
        value = rpc.call("status", {"run_id": rid})
        if value["status"] == status:
            return value
        time.sleep(0.05)
    raise AssertionError(value)


def test_pi5_records_preserved_and_usable(tmp_path):
    with zipfile.ZipFile(PI5_ZIP) as archive:
        archive.extractall(tmp_path / "pi5")
    plugin = tmp_path / "pi5/magazine-pi"
    assert json.loads((plugin / "manifest.json").read_text())["version"] == "0.1.0-pi.5-rc.1"
    provider = ScriptedProvider()
    agent = agent_dir(tmp_path / "agent", provider.url)
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    config = {"cwd": str(cwd), "agentDir": str(agent), "timeoutSeconds": 60, "extensionPaths": [], "allowedTools": []}
    data = tmp_path / "data"
    doc = {"schema_version": "1.0", "cartridges": [{"id": "c1", "job_type": "j", "prompt": "SCRIPT text:OLD_ONE\n"},
                                                   {"id": "c2", "job_type": "j", "prompt": "SCRIPT text:OLD_TWO\n"}]}
    old = OldRPC(plugin, data, config)
    try:
        done = old.call("prepare", {"magazine": json.dumps(doc), "model_map": {"j": "fixture/model-a"}, "tool_ids": []})["run_id"]
        old.call("start", {"run_id": done})
        old_status(old, done, "COMPLETED")
        ready = old.call("prepare", {"magazine": json.dumps(doc), "model_map": {"j": "fixture/model-b"}, "tool_ids": []})["run_id"]
        old_results = old.call("results", {"run_id": done})
    finally:
        old.close()
    before = snapshot(data)
    requests = len(provider.requests)
    new = RPC(data, config)
    try:
        runs = {r["run_id"]: r for r in new.call("runs")["runs"]}
        assert runs[done]["display_status"] == "COMPLETED" and runs[ready]["display_status"] == "READY"
        assert new.call("results", {"run_id": done}) == old_results
        status = new.call("status", {"run_id": done})
        assert status["protocol"]["legacy"] and status["ledger"] is None
        assert status["tokens"]["source"] == "retained_sessions_without_ledger"
        assert status["tokens"]["state"] == "unavailable" and status["tokens"]["attempts"] == 2
        assert new.call("ledger", {"run_id": done})["ledger"] is None
        archive = new.call("download", {"run_id": done})
        assert base64.b64decode(archive["file_data_b64"])[:2] == b"PK"
        rows = [json.loads(l) for l in base64.b64decode(new.call("download_jsonl", {"run_id": done})["file_data_b64"]).decode().splitlines()]
        assert {r["cartridge_id"] for r in rows if r["kind"] == "session_record"} == {"c1", "c2"}
        assert len(provider.requests) == requests  # reading never runs inference
        after = snapshot(data)
        assert {k: v for k, v in after.items() if k in before} == before  # no existing byte changed
        assert set(after) - set(before) <= {"runs/local/" + done + "/export.json"} | {k for k in after if k.startswith(("temp/", "finished-work/"))}
        # A READY pi.5 run starts under its recorded pi.5 semantics.
        new.call("start", {"run_id": ready})
        final = wait(new, ready, ("COMPLETED", "HALTED"))
        assert final["display_status"] == "COMPLETED"
        assert new.call("results", {"run_id": ready})["results"] == {"c1": "OLD_ONE", "c2": "OLD_TWO"}
        value = new.call("ledger", {"run_id": ready})["ledger"]
        assert value["protocol"] == "pi5_legacy" and all(len(r["attempts"]) == 1 for r in value["cartridges"].values())
        assert not (data / "runs/local" / ready / "protocol.json").exists()
    finally:
        new.close()
        provider.close()
    # Rollback: the pi.5 service still reads everything, including the pi.6-run legacy record.
    old = OldRPC(plugin, data, config)
    try:
        assert old.call("results", {"run_id": ready})["results"] == {"c1": "OLD_ONE", "c2": "OLD_TWO"}
        assert {r["run_id"] for r in old.call("runs")["runs"]} == {done, ready}
    finally:
        old.close()
