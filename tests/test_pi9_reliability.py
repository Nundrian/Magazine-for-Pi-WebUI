"""pi.9 reliability: worker isolation, controller integrity, emergency STOP,
state repair, deterministic model failures and restore points.

Real Pi SDK + real Magazine service + scripted loopback provider. No model,
GPU or external endpoint is used. Simulated workers run real shell commands
through the SDK's own bash tool, exactly as a local model would.
"""
import json
import os
import shutil
import signal
import time
from pathlib import Path

import pytest

from pi6_fakes import RPC, ScriptedProvider, agent_dir, wait
from test_pi6_integration import attempts, ledger, prepare, run_to_end

pytestmark = pytest.mark.skipif(not os.environ.get("MAGAZINE_PI_SDK"), reason="needs MAGAZINE_PI_SDK (real SDK tree)")
TOKEN = "pi-web-token-fixture-7f3a"


class Env:
    pass


@pytest.fixture
def make_env(tmp_path, monkeypatch):
    created = []

    def build(**config_extra):
        monkeypatch.setenv("PI_WEB_TOKEN", TOKEN)  # the host credential a worker must never see
        provider = ScriptedProvider()
        agent = agent_dir(tmp_path / "pi-data" / "agent", provider.url)
        (agent / "mcp.json").write_text('{"mcpServers":{"kit":{"headers":{"Authorization":"Bearer SECRET-MCP"}}}}')
        cwd = tmp_path / "workspace"
        cwd.mkdir(exist_ok=True)
        config = {"cwd": str(cwd), "agentDir": str(agent), "extensionPaths": [],
                  "allowedTools": ["bash", "read", "write", "ls"], "piDataDir": str(tmp_path / "pi-data"),
                  **config_extra}
        e = Env()
        e.provider, e.cwd, e.config, e.tmp, e.agent = provider, cwd, config, tmp_path, agent
        e.data = tmp_path / "pi-data" / "magazine"
        e.sentinel = tmp_path / "pi-data" / "plugins-sentinel.txt"
        e.sentinel.write_text("pi configuration\n")
        e.rpc = RPC(e.data, config)
        created.append(e)
        return e
    yield build
    for e in created:
        e.provider.close()
        try:
            e.rpc.close(kill=True)
        except Exception:
            pass


def run_dir(e, rid):
    return e.data / "runs" / "local" / rid


def controller_bytes(e, rid):
    base = run_dir(e, rid)
    return {rel: (base / rel).read_bytes() if (base / rel).exists() else None
            for rel in ("run/state.json", "run/execution.json", "magazine.json", "meta.json", "protocol.json")}


def attack_commands(e, rid):
    base = run_dir(e, rid)
    state = base / "run" / "state.json"
    log = e.cwd / "attack.log"
    probe = (lambda label, cmd: f"( {cmd} ) >/dev/null 2>&1; echo \"{label}=$?\" >> {log}")
    return [
        probe("redirect", f"echo '{{}}' > {state}"),
        probe("append", f"echo x >> {state}"),
        probe("python", f"python3 -c \"open('{state}','w').write('x')\""),
        probe("node", f"node -e \"require('fs').writeFileSync('{state}','x')\""),
        probe("backup_copy", f"cp -a {state} {state}.bak.c001"),
        probe("symlink", f"ln -s {state} {e.cwd}/link && echo x > {e.cwd}/link"),
        probe("traversal", f"echo x > {e.cwd}/../pi-data/magazine/runs/local/{rid}/run/state.json"),
        probe("ledger", f"echo '{{}}' > {base}/ledger.json"),
        probe("unlink", f"rm -f {base}/run/execution.json"),
        probe("pi_config", f"echo x >> {e.sentinel}"),
        probe("read_mcp", f"cat {e.agent}/mcp.json"),
        probe("hardlink", f"ln {state} {e.cwd}/hard"),
        probe("unix_socket", "python3 -c \"import socket; socket.socket(socket.AF_UNIX)\""),
        f"env | grep -c PI_WEB_TOKEN >> {log}; echo \"token_env=done\" >> {log}",
        f"cat /proc/$PPID/environ 2>/dev/null | tr '\\0' '\\n' | grep -c {TOKEN} >> {log} ; echo environ=done >> {log}",
        probe("forge_channel", "echo '{\"event\":\"result\",\"ok\":true}' > /proc/$PPID/fd/1"),
        probe("publish_dir", f"echo x > {base}/attempts/c1/forged/publish/result.json"),
        probe("session_dir", f"echo x > {base}/sessions/forged.jsonl"),
        probe("dgram_pair", "python3 -c \"import socket; socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)\""),
        f"setsid sh -c 'echo $$ > {e.cwd}/survivor.pid; sleep 120; echo late > {e.cwd}/late.txt' >/dev/null 2>&1 < /dev/null &",
        probe("system_tmp", "echo x > /tmp/magazine-worker-probe-$$"),
        probe("runtime_guard", "echo forged > /tmp/magazine_runtime_id"),
        probe("private_tmp", "echo ok > \"$TMPDIR/ok\" && test \"$HOME\" != /root"),
        probe("workspace_write", f"echo legitimate > {e.cwd}/project-output.txt"),
    ]


def parse_log(e):
    out = {}
    for line in (e.cwd / "attack.log").read_text().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key] = value
    return out


def test_worker_cannot_modify_controller_state_or_read_host_secrets(make_env):
    """The Nebula Swamp failure, attempted deliberately through every common route."""
    e = make_env()
    rid = prepare(e.rpc, "SCRIPT bashseq:attack\nTask", tools=["bash"])
    before = controller_bytes(e, rid)
    e.provider.sequences["attack"] = attack_commands(e, rid)
    status = run_to_end(e.rpc, rid)
    assert status["status"] == "COMPLETED", status
    log = parse_log(e)
    for route in ("redirect", "append", "python", "node", "backup_copy", "symlink", "traversal", "ledger",
                  "unlink", "pi_config", "read_mcp", "hardlink", "unix_socket", "forge_channel", "publish_dir",
                  "session_dir", "dgram_pair"):
        assert log[route] != "0", (route, log)  # every route was refused by the kernel
    assert log["system_tmp"] != "0" and log["runtime_guard"] != "0"
    assert log["workspace_write"] == "0" and log["private_tmp"] == "0"
    assert (e.cwd / "project-output.txt").read_text() == "legitimate\n"
    assert "SECRET-MCP" not in (e.cwd / "attack.log").read_text()
    raw = (e.cwd / "attack.log").read_text()
    assert raw.count("0\ntoken_env=done") == 1  # grep -c printed 0: no PI_WEB_TOKEN in the worker
    if os.getuid() != 0:  # CAP_SYS_PTRACE (root in CI) legitimately bypasses the ptrace scope
        assert "0\nenviron=done" in raw
    assert e.sentinel.read_text() == "pi configuration\n"
    after = controller_bytes(e, rid)
    assert after["magazine.json"] == before["magazine.json"] and after["meta.json"] == before["meta.json"]
    assert json.loads(after["run/state.json"])["status"] == "COMPLETED"
    assert not list((run_dir(e, rid) / "run").glob("*.bak*"))
    attempt = attempts(e.rpc, rid, "c1")[0]
    assert attempt["sandbox"]["mechanism"] == "landlock" and attempt["sandbox"]["unix_sockets"] is False
    assert attempt["session_file"].startswith("sessions/" + attempt["attempt_id"] + "/")
    assert not attempt.get("integrity")
    # scratch folders are removed after every worker
    assert not list((e.data / "temp").glob("worker-*"))
    survivor = int((e.cwd / "survivor.pid").read_text())
    deadline = time.monotonic() + 15
    while os.path.exists(f"/proc/{survivor}") and time.monotonic() < deadline:
        time.sleep(0.2)
    alive = os.path.exists(f"/proc/{survivor}") and "Z" not in open(f"/proc/{survivor}/stat").read().split(")")[1][:3]
    assert not alive, "a setsid descendant outlived its worker"


def test_integrity_fence_rejects_tampering_when_sandbox_is_off(make_env):
    """Defence in depth: even unconfined, a modified controller fails closed and halts cleanly."""
    e = make_env(workerSandbox="off")
    rid = prepare(e.rpc, "SCRIPT bashseq:tamper\nTask", tools=["bash"])
    state = run_dir(e, rid) / "run" / "state.json"
    e.provider.sequences["tamper"] = [
        f"cp -a {state} {state}.bak.c001",
        "python3 - <<'EOF'\nimport json\np='" + str(state) + "'\nv=json.load(open(p))\n"
        "v.update(done=['c1'],current=None,todo=[],status='RUNNING',last_committed_step='c1',report_path='/x')\n"
        "json.dump(v,open(p,'w'))\nEOF",
    ]
    status = run_to_end(e.rpc, rid)
    assert status["status"] == "HALTED", status
    assert status["done"] == []
    tries = attempts(e.rpc, rid, "c1")
    assert len(tries) == 1, "integrity failure must not be retried"
    assert tries[0]["code"] == "CONTROLLER_STATE_MODIFIED" and tries[0]["status"] == "failed"
    integrity = tries[0]["integrity"]
    quarantine = run_dir(e, rid) / integrity["quarantine"]
    names = sorted(p.name for p in quarantine.iterdir())
    assert "run__state.json" in names and "run__state.json.bak.c001" in names and "manifest.json" in names
    tampered = json.loads((quarantine / "run__state.json").read_text())
    assert tampered["last_committed_step"] == "c1"
    assert not (state.parent / "state.json.bak.c001").exists()
    assert ledger(e.rpc, rid)["cartridges"]["c1"]["status"] != "done"


def test_emergency_stop_with_corrupted_state_while_worker_is_running(make_env):
    e = make_env(workerSandbox="off")
    rid = prepare(e.rpc, "SCRIPT bashseq:corrupt-hold\nTask", tools=["bash"])
    state = run_dir(e, rid) / "run" / "state.json"
    e.provider.sequences["corrupt-hold"] = [
        "python3 -c \"import json;p='" + str(state) + "';v=json.load(open(p));v['project_root']='/x';json.dump(v,open(p,'w'))\"",
        "HOLD",
    ]
    e.rpc.call("start", {"run_id": rid})
    assert e.provider.holding.wait(60)
    damaged = e.rpc.call("status", {"run_id": rid})
    assert damaged["status"] == "STATE_INVALID" and damaged["done"] == [] and len(damaged["order"]) == 1
    result = e.rpc.call("stop", {"run_id": rid}, timeout=90)
    assert result["status"] == "emergency_stopped", result
    assert result["emergency"]["active_task"] is True
    assert result["emergency"]["worker"] in ("abort_acknowledged", "terminated")
    assert result["emergency"]["worker_processes_after"] == "absent"
    final = e.rpc.call("status", {"run_id": rid})
    # The fence restored Magazine's own state on cancellation, so the core halted it normally.
    assert final["status"] in ("HALTED", "STATE_INVALID") and final["status"] != "RUNNING"
    assert not final["active"]
    assert (state.parent / "emergency-stop.json").exists()
    e.provider.hold.set()
    time.sleep(0.5)
    assert len([r for r in e.provider.requests]) == 2  # nothing resumed


def test_crash_with_damaged_state_then_explicit_repair(make_env):
    """Service killed while a worker had damaged the state; repair after restart, never resumes."""
    e = make_env(workerSandbox="off")
    rid = prepare(e.rpc, "First", "SCRIPT bashseq:crash\nSecond", tools=["bash"])
    state = run_dir(e, rid) / "run" / "state.json"
    e.provider.sequences["crash"] = [
        "python3 -c \"import json;p='" + str(state) + "';v=json.load(open(p));v['report_sha256']='0';json.dump(v,open(p,'w'))\"",
        "HOLD",
    ]
    e.rpc.call("start", {"run_id": rid})
    assert e.provider.holding.wait(60)
    e.rpc.close(kill=True)          # abrupt service death; the worker is orphaned
    e.rpc = RPC(e.data, e.config)    # restart
    status = e.rpc.call("status", {"run_id": rid})
    assert status["status"] == "STATE_INVALID"
    runs = e.rpc.call("runs")["runs"]
    assert [r["display_status"] for r in runs if r["run_id"] == rid] == ["STATE_INVALID"]
    e.provider.hold.set()            # the orphaned worker exits when its service pipe closes
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        preview = e.rpc.call("repair_preview", {"run_id": rid})
        reply = e.rpc.call("repair_state", {"run_id": rid, "confirm": True}, ok=False)
        if reply["ok"]:
            break
        time.sleep(0.5)
    assert reply["ok"], reply
    receipt = reply["result"]
    assert receipt["state"]["status"] == "HALTED" and receipt["state"]["done"] == ["c1"]
    assert receipt["state"]["current"] == "c2" and receipt["state"]["revision"] == 3
    assert [i["cartridge_id"] for i in receipt["implicated_attempts"]] == ["c2"]
    assert [d["accepted"] for d in preview["decisions"]] == [True, False]
    requests = len(e.provider.requests)
    repaired = e.rpc.call("status", {"run_id": rid})
    assert repaired["status"] == "HALTED" and repaired["execution"]["halt_reason"] == "state-repaired"
    recovered = e.rpc.call("recover", {"run_id": rid})
    assert recovered["status"] == "READY"
    time.sleep(1)
    assert len(e.provider.requests) == requests  # repair and recovery never start inference
    assert (run_dir(e, rid) / "repair.json").exists()


def test_incompatible_model_halts_without_wasting_attempts(make_env):
    e = make_env()
    rid = prepare(e.rpc, "SCRIPT arch-error\nTask")
    status = run_to_end(e.rpc, rid)
    assert status["status"] == "HALTED"
    tries = attempts(e.rpc, rid, "c1")
    assert len(tries) == 1 and tries[0]["code"] == "MODEL_INCOMPATIBLE"
    assert tries[0]["retry_decision"]["decision"] == "stop"


def test_unreachable_provider_retries_once_then_halts(make_env, tmp_path):
    e = make_env()
    models = json.loads((e.agent / "models.json").read_text())
    models["providers"]["down"] = dict(models["providers"]["fixture"], baseUrl="http://127.0.0.1:9/v1")
    (e.agent / "models.json").write_text(json.dumps(models))
    rid = prepare(e.rpc, "Task", model="down/model-a")
    status = run_to_end(e.rpc, rid)
    assert status["status"] == "HALTED"
    tries = attempts(e.rpc, rid, "c1")
    assert [t["code"] for t in tries] == ["PROVIDER_UNAVAILABLE"] * 2, tries
    assert tries[-1]["retry_decision"]["reason_code"] == "NO_PROGRESS"


def test_missing_required_project_path_refuses_start(make_env):
    e = make_env()
    from test_pi6_integration import import_project, prepare_project
    manifest = "schema_version: '1.0'\nname: Swamp\nrequired_paths: ['/definitely/not/mounted/NebulaSwamp']\n" \
               "cartridges:\n- id: c1\n  model: m\n  prompt_file: c1.md\n"
    pid = import_project(e.rpc, manifest, {"c1.md": "Windows target E:\\3D-Library\\Projects\\NebulaSwamp\nTask"})
    preview = e.rpc.call("preview", {"project_id": pid})
    assert preview["path_check"]["missing"] and preview["path_check"]["windows_paths"] == ["E:\\3D-Library\\Projects\\NebulaSwamp"]
    rid = prepare_project(e.rpc, pid)
    refused = e.rpc.call("start", {"run_id": rid}, ok=False)
    assert refused["code"] == 409 and "/definitely/not/mounted/NebulaSwamp" in refused["error"]
    assert not e.provider.requests


def test_sandbox_required_but_unavailable_starts_nothing(make_env, monkeypatch):
    import pi_dispatch
    import sandbox
    e = make_env()
    monkeypatch.setattr(sandbox, "abi_version", lambda: 0)
    dispatch = pi_dispatch.PiDispatch({**e.config, "dataDir": str(e.data)}, e.data / "x")
    import asyncio
    with pytest.raises(pi_dispatch.SandboxUnavailable, match="Landlock"):
        asyncio.run(dispatch._request({"action": "catalogue"}))
    assert dispatch.process is None


def test_repair_refuses_while_a_worker_process_exists(make_env, monkeypatch):
    import asyncio
    import integrity
    import interrupted
    import service as svc
    e = make_env()
    rid = prepare(e.rpc, "Task")
    e.rpc.close()
    state = run_dir(e, rid) / "run" / "state.json"
    value = json.loads(state.read_text()); value["extra"] = 1; state.write_text(json.dumps(value))
    service = svc.Service(e.data, dict(e.config))
    monkeypatch.setattr(interrupted, "worker_evidence", lambda: {"status": "present", "pids": [4242]})
    with pytest.raises(integrity.IntegrityError, match="worker process"):
        integrity.repair_state(service, "local", rid)
    assert json.loads(state.read_text())["extra"] == 1  # untouched
    monkeypatch.setattr(interrupted, "worker_evidence", lambda: {"status": "uncertain", "pids": []})
    with pytest.raises(integrity.IntegrityError):
        integrity.repair_state(service, "local", rid)
    monkeypatch.setattr(interrupted, "worker_evidence", lambda: {"status": "absent", "pids": []})
    receipt = integrity.repair_state(service, "local", rid)
    assert receipt["state"] == {"done": [], "current": "c1", "todo": [], "status": "HALTED", "revision": 0} or \
        receipt["state"]["status"] in ("HALTED",)
    e.rpc = RPC(e.data, e.config)


def test_permission_tampering_is_detected_restored_and_rejected(make_env):
    """chmod is not governed by Landlock: Magazine must notice, put access back and fail closed."""
    e = make_env()
    rid = prepare(e.rpc, "SCRIPT bashseq:chmod\nTask", tools=["bash"])
    base = run_dir(e, rid)
    e.provider.sequences["chmod"] = [f"chmod 000 {base}/run/state.json; chmod 000 {base}/meta.json; echo done"]
    status = run_to_end(e.rpc, rid)
    assert status["status"] == "HALTED", status
    attempt = attempts(e.rpc, rid, "c1")[0]
    assert attempt["code"] == "CONTROLLER_STATE_MODIFIED"
    assert any(f["path"].endswith("state.json") for f in attempt["integrity"]["files"])
    assert os.stat(base / "run" / "state.json").st_mode & 0o600 == 0o600
    assert e.rpc.call("runs")["runs"][0]["run_id"] == rid  # the run list still works


def test_service_heals_permissions_changed_outside_an_attempt(make_env):
    e = make_env()
    rid = prepare(e.rpc, "Task")
    base = run_dir(e, rid)
    os.chmod(base / "run", 0)
    os.chmod(base / "meta.json", 0)
    status = e.rpc.call("status", {"run_id": rid})
    assert status["status"] == "READY"
    events = (e.data / "integrity-events.jsonl").read_text()
    assert "permissions_restored" in events


def test_stop_falls_back_to_emergency_cancellation_when_records_are_unreadable(make_env):
    e = make_env(workerSandbox="off")
    rid = prepare(e.rpc, "SCRIPT bashseq:hold-only\nTask", tools=["bash"])
    e.provider.sequences["hold-only"] = ["HOLD"]
    e.rpc.call("start", {"run_id": rid})
    assert e.provider.holding.wait(60)
    execution = run_dir(e, rid) / "run" / "execution.json"
    execution.write_text("{not json")
    result = e.rpc.call("stop", {"run_id": rid}, timeout=90)
    assert result["status"] in ("emergency_stopped", "stopped"), result
    final = e.rpc.call("status", {"run_id": rid})
    assert not final["active"]
    e.provider.hold.set()


def test_killing_the_supervisor_leaves_no_survivor_at_abi_3(make_env, monkeypatch):
    """Without Landlock signal scoping (ABI < 6) a tool can kill the launcher; nothing may survive."""
    monkeypatch.setenv("MAGAZINE_LANDLOCK_MAX_ABI", "3")
    e = make_env(workerEnv=["MAGAZINE_LANDLOCK_MAX_ABI"])
    rid = prepare(e.rpc, "SCRIPT bashseq:kill-supervisor\nTask", tools=["bash"])
    e.provider.sequences["kill-supervisor"] = [
        f"setsid sh -c 'echo $$ > {e.cwd}/survivor.pid; sleep 120; echo late > {e.cwd}/late.txt' "
        ">/dev/null 2>&1 < /dev/null & sleep 1; kill -9 $(awk '{print $4}' /proc/$PPID/stat); sleep 2; echo after",
    ]
    status = run_to_end(e.rpc, rid)
    assert status["status"] == "HALTED", status
    tries = attempts(e.rpc, rid, "c1")
    assert all(t["sandbox"] is None or t["sandbox"]["abi"] == 3 for t in tries)
    assert tries[0]["status"] == "failed" and "exited" in (tries[0].get("error") or tries[0].get("message") or json.dumps(tries[0])), tries[0]
    survivor = int((e.cwd / "survivor.pid").read_text())
    deadline = time.monotonic() + 15
    while os.path.exists(f"/proc/{survivor}") and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not os.path.exists(f"/proc/{survivor}") or "Z" in open(f"/proc/{survivor}/stat").read().split(")")[1][:3]
    assert not any("worker.mjs" in open(f"/proc/{p}/cmdline", "rb").read().decode(errors="ignore")
                   for p in os.listdir("/proc") if p.isdigit() and os.path.exists(f"/proc/{p}/cmdline")
                   and str(e.data) in open(f"/proc/{p}/cmdline", "rb").read().decode(errors="ignore"))
