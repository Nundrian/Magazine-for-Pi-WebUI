"""pi.6 integration: actual Pi SDK + actual Magazine service + scripted fake provider.

The provider is a loopback HTTP fixture that replays fixed scripts; no model,
GPU or external endpoint is involved. MAGAZINE_PI_SDK selects the SDK tree.
"""
import base64
import hashlib
import io
import json
import os
import shutil
import time
import zipfile
from pathlib import Path

import pytest

from pi6_fakes import RPC, ScriptedProvider, agent_dir, project_zip, wait

pytestmark = pytest.mark.skipif(not os.environ.get("MAGAZINE_PI_SDK"), reason="needs MAGAZINE_PI_SDK (real SDK tree)")


@pytest.fixture
def env(tmp_path):
    provider = ScriptedProvider()
    agent = agent_dir(tmp_path / "agent", provider.url)
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("FORBIDDEN_PROJECT_CONTEXT_SENTINEL")
    config = {"cwd": str(cwd), "agentDir": str(agent), "extensionPaths": [],
              "allowedTools": ["ls", "read", "write"]}
    state = {"rpc": RPC(tmp_path / "data", config)}

    class Env:
        pass
    e = Env()
    e.provider, e.cwd, e.config, e.tmp = provider, cwd, config, tmp_path
    e.data = tmp_path / "data"
    e.rpc = lambda: state["rpc"]
    def restart(kill=False):
        state["rpc"].close(kill=kill)
        state["rpc"] = RPC(e.data, config)
        return state["rpc"]
    e.restart = restart
    try:
        yield e
    finally:
        state["rpc"].close()
        provider.close()
        evidence = os.environ.get("MAGAZINE_TEST_EVIDENCE_DIR")
        if evidence:
            shutil.copytree(tmp_path, Path(evidence) / tmp_path.name, dirs_exist_ok=True, ignore_dangling_symlinks=True)


def doc(*prompts):
    return {"schema_version": "1.0", "cartridges": [{"id": f"c{i+1}", "job_type": "job", "prompt": p}
                                                    for i, p in enumerate(prompts)]}


def prepare(rpc, *prompts, mode="result_file_v1", tools=(), antiloop=None, model="fixture/model-a"):
    data = {"magazine": json.dumps(doc(*prompts)), "model_map": {"job": model}, "tool_ids": list(tools)}
    if mode:
        data["handover_mode"] = mode
    if antiloop is not None:
        data["antiloop"] = antiloop
    return rpc.call("prepare", data)["run_id"]


def import_project(rpc, manifest, prompts, name="Proj"):
    raw = project_zip(manifest, prompts)
    return rpc.call("import", {"file_name": name + ".zip", "file_data_b64": raw})["project_id"]


def prepare_project(rpc, project_id, **extra):
    preview = rpc.call("preview", {"project_id": project_id})
    assert preview["valid"], preview
    data = {"magazine": json.dumps(preview["document"]), "project_id": project_id,
            "default_model": "fixture/model-a", "tool_ids": [], **extra}
    return rpc.call("prepare", data)["run_id"]


def ledger(rpc, rid):
    return rpc.call("ledger", {"run_id": rid})["ledger"]


def attempts(rpc, rid, cid):
    return ledger(rpc, rid)["cartridges"][cid]["attempts"]


def run_to_end(rpc, rid, timeout=90):
    rpc.call("start", {"run_id": rid})
    return wait(rpc, rid, ("COMPLETED", "HALTED"), timeout)


def user_prompts(provider):
    out = []
    for r in provider.requests:
        user = next(m for m in r["messages"] if m["role"] == "user")["content"]
        out.append(user if isinstance(user, str) else "".join(p.get("text", "") for p in user))
    return out


# -------------------------------------------------------------------- basics
def test_results_returns_verified_committed_handovers(env):
    rpc = env.rpc()
    rid = prepare(rpc, 'SCRIPT ok\nText deliverable', 'SCRIPT value:9\nStructured deliverable')
    assert rpc.call('results', {'run_id': rid})['handovers'] == {}
    assert run_to_end(rpc, rid)['display_status'] == 'COMPLETED'
    result = rpc.call('results', {'run_id': rid})
    assert result['done'] == ['c1', 'c2']
    assert result['handovers']['c1']['payload']['result']['note'] == 'ok'
    assert result['handovers']['c2']['payload']['answer'] == 9
    assert 'ok' not in result['results']['c1']  # final reply and deliverable are distinct
    # Results must not display changed bytes as a verified deliverable.
    attempt = attempts(rpc, rid, 'c1')[0]
    paths = list(env.data.rglob(attempt['handover']['path']))
    assert len(paths) == 1
    path = paths[0]
    path.write_text(path.read_text().replace('"note":"ok"', '"note":"changed"'))
    bad = rpc.call('results', {'run_id': rid})['handovers']['c1']
    assert 'payload' not in bad and 'RESULT_COMMIT_MISMATCH' in bad['error']


def test_prepare_and_reopen_never_run_inference(env):
    rpc = env.rpc()
    rid = prepare(rpc, "SCRIPT ok\nA")
    status = rpc.call("status", {"run_id": rid})
    assert status["display_status"] == "READY" and status["protocol"]["antiloop"] is True
    rpc.call("view_state", {"set": {"run_id": rid}})
    rpc = env.restart()
    assert rpc.call("view_state")["run_id"] == rid and rpc.call("view_state")["run_exists"]
    for _ in range(3):
        assert rpc.call("status", {"run_id": rid})["status"] == "READY"
        rpc.call("runs"); rpc.call("ledger", {"run_id": rid})
    assert env.provider.requests == []


def test_result_file_value_and_file_handover_sequential(env):
    rpc = env.rpc()
    manifest = """
name: Handover chain
cartridges:
  - {id: c1, model: w, prompt_file: p/1.md, artifacts: {outputs: [out.txt]}}
  - id: c2
    model: w
    prompt_file: p/2.md
    artifacts: {inputs: [{from: c1, path: out.txt}]}
  - id: c3
    model: w
    prompt_file: p/3.md
    handover: [{from: c2, select: /answer, as: answer}]
"""
    pid = import_project(rpc, manifest, {"p/1.md": "SCRIPT file:out.txt\nWrite it.", "p/2.md": "SCRIPT value:42\nRead it.",
                                         "p/3.md": "SCRIPT ok\nUse the answer."})
    rid = prepare_project(rpc, pid, tool_ids=["write"])
    assert env.provider.requests == []
    final = run_to_end(rpc, rid)
    assert final["display_status"] == "COMPLETED", final
    prompts = user_prompts(env.provider)
    c2 = next(p for p in prompts if p.startswith("SCRIPT value"))
    c3 = next(p for p in prompts if p.startswith("SCRIPT ok"))
    assert "Input files (verified copies):" in c2 and "out.txt" in c2
    assert "answer (from c2):" in c3 and "42" in c3
    value = ledger(rpc, rid)
    sessions = set()
    for cid in ("c1", "c2", "c3"):
        [a] = value["cartridges"][cid]["attempts"]
        assert a["status"] == "done" and a["phase"] == "committed"
        sessions.add(a["session_id"])
        committed = env.data / "runs/local" / rid / a["handover"]["path"]
        assert hashlib.sha256(committed.read_bytes()).hexdigest() == a["handover"]["sha256"]
        assert json.loads(committed.read_text())["session_id"] == a["session_id"]
        assert a["active_tools"] == sorted(["write", "magazine_publish_result"])
        assert a["prompt_sha256"] != a["delivered_prompt_sha256"]
    assert len(sessions) == 3
    copied = Path(value["cartridges"]["c2"]["attempts"][0]["inputs"][0]["local"])
    assert copied.read_text() == "FILE_CONTENT_out.txt"
    for request in env.provider.requests:
        assert sum(m["role"] == "user" for m in request["messages"]) == 1
        assert "FORBIDDEN_" not in json.dumps(request)
        assert sorted(t["function"]["name"] for t in request["tools"]) == ["magazine_publish_result", "write"]
    # Exports carry the ledger and committed handovers.
    archive = rpc.call("download", {"run_id": rid})
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(archive["file_data_b64"]))) as z:
        names = z.namelist()
        assert "magazine-ledger/LEDGER.md" in names and "magazine-ledger/ledger.json" in names
        assert sum(n.endswith("/commit.json") for n in names) == 3
        manifest_json = json.loads(z.read("manifest.json"))
        assert [c["id"] for c in manifest_json["cartridges"]] == ["c1", "c2", "c3"]
    raw = base64.b64decode(rpc.call("download_jsonl", {"run_id": rid})["file_data_b64"]).decode()
    rows = [json.loads(l) for l in raw.splitlines()]
    kinds = {r["kind"] for r in rows}
    assert {"handover_snapshot", "session_record", "run_snapshot"} <= kinds
    assert {r["cartridge_id"] for r in rows if r["kind"] == "session_record"} == {"c1", "c2", "c3"}
    assert any(r["source"] == "ledger.json" for r in rows)


def test_exact_prompt_in_both_protocols(env):
    rpc = env.rpc()
    prompt = "SCRIPT text:LEGACY_OK\n  exact   spacing\t\n\n"
    rid = prepare(rpc, prompt, mode="legacy_text")
    assert run_to_end(rpc, rid)["display_status"] == "COMPLETED"
    assert user_prompts(env.provider) == [prompt]
    assert rpc.call("results", {"run_id": rid})["results"]["c1"] == "LEGACY_OK"
    env.provider.requests.clear()
    prompt2 = "SCRIPT ok\n  exact   spacing\t"
    rid = prepare(rpc, prompt2)
    assert run_to_end(rpc, rid)["display_status"] == "COMPLETED"
    delivered = user_prompts(env.provider)
    assert all(p.startswith(prompt2) for p in delivered)
    path = next((env.data / "runs/local" / rid / "attempts").rglob("prompt.md"))
    assert path.read_text() == delivered[0]


# ------------------------------------------------------------------ recovery
def test_five_attempt_ceiling_and_continue_cannot_bypass(env):
    rpc = env.rpc()
    rid = prepare(rpc, "SCRIPT nodone\nNever finishes")
    final = run_to_end(rpc, rid, 120)
    assert final["display_status"] == "HALTED"
    tried = attempts(rpc, rid, "c1")
    assert len(tried) == 5 and len(env.provider.attempts["nodone"]) == 5
    assert [a["retry_decision"]["decision"] for a in tried] == ["retry"] * 4 + ["stop"]
    assert tried[-1]["retry_decision"]["reason_code"] == "MAX_ATTEMPTS_REACHED"
    assert all(a["code"] == "DONE_MISSING" for a in tried)
    assert [a["kind"] for a in tried] == ["work", "publish", "publish", "publish", "publish"]
    assert len({a["session_id"] for a in tried}) == 5
    rpc.call("recover", {"run_id": rid})
    refused = rpc.call("start", {"run_id": rid}, ok=False)
    assert refused["code"] == 409 and "sixth" in refused["error"]
    assert len(env.provider.attempts["nodone"]) == 5


def test_smaller_configured_attempts_and_continue_uses_remaining_ceiling(env):
    rpc = env.rpc()
    pid = import_project(rpc, "recovery: {max_attempts: 2}\ncartridges: [{id: c1, model: w, prompt_file: a.md}]",
                         {"a.md": "SCRIPT nodone-until:4\nx"})
    rid = prepare_project(rpc, pid)
    assert run_to_end(rpc, rid)["display_status"] == "HALTED"
    tried = attempts(rpc, rid, "c1")
    assert len(tried) == 2 and tried[-1]["retry_decision"]["reason_code"] == "CONFIGURED_ATTEMPTS_EXHAUSTED"
    rpc.call("recover", {"run_id": rid})
    final = run_to_end(rpc, rid)
    tried = attempts(rpc, rid, "c1")
    assert final["display_status"] == "COMPLETED" and len(tried) == 4  # third attempt fails, fourth commits


def test_missing_publication_retries_as_publish_only(env):
    rpc = env.rpc()
    rid = prepare(rpc, "SCRIPT nopublish-once\nTask")
    assert run_to_end(rpc, rid)["display_status"] == "COMPLETED"
    first, second = attempts(rpc, rid, "c1")
    assert first["code"] == "RESULT_MISSING" and first["recovery"] == "publish"
    assert second["kind"] == "publish" and second["failure_context"]["parent_attempt_id"] == first["attempt_id"]
    assert "AUTOMATIC RECOVERY" in user_prompts(env.provider)[-1]


@pytest.mark.parametrize("script,code", [("error-once", "WORKER_FAILED"), ("length-once", "INCOMPLETE_RESPONSE")])
def test_recoverable_worker_outcomes(env, script, code):
    rpc = env.rpc()
    rid = prepare(rpc, f"SCRIPT {script}\nTask")
    assert run_to_end(rpc, rid)["display_status"] == "COMPLETED"
    first, second = attempts(rpc, rid, "c1")
    assert first["code"] == code and second["status"] == "done"
    if script == "length-once":
        assert first["finish"] == "length"


def test_halt_reply_stops_without_retry(env):
    rpc = env.rpc()
    rid = prepare(rpc, "SCRIPT halt\nx", "SCRIPT ok\nnever")
    final = run_to_end(rpc, rid)
    assert final["display_status"] == "HALTED"
    [a] = attempts(rpc, rid, "c1")
    assert a["code"] == "HALT_RECEIVED" and a["retry_decision"]["reason_code"] == "HALT_RECEIVED"
    assert not any(p.startswith("SCRIPT ok") for p in user_prompts(env.provider))


def test_attempt_timeout_gets_fresh_full_allowance(env):
    rpc = env.rpc()
    pid = import_project(rpc, "recovery: {attempt_seconds: 6}\ncartridges: [{id: c1, model: w, prompt_file: a.md}]",
                         {"a.md": "SCRIPT hold-once\nx"})
    rid = prepare_project(rpc, pid)
    final = run_to_end(rpc, rid)
    env.provider.hold.set()
    assert final["display_status"] == "COMPLETED"
    first, second = attempts(rpc, rid, "c1")
    assert first["code"] == "ATTEMPT_TIMEOUT" and first["timeout_seconds"] == 6 and second["timeout_seconds"] == 6
    assert first["tokens"]["incomplete"]


# ----------------------------------------------------------- integrity checks
def test_result_not_written_by_publisher_is_rejected(env):
    rpc = env.rpc()
    rid = prepare(rpc, "SCRIPT publish-hold\nx", "SCRIPT ok\nnever")
    rpc.call("start", {"run_id": rid})
    assert env.provider.holding.wait(30)
    [target] = list((env.data / "runs/local" / rid / "attempts").rglob("publish/result.json"))
    value = json.loads(target.read_text())
    value["payload"] = {"forged": True}
    target.write_text(json.dumps(value) + "\n")  # same identities, different bytes
    env.provider.hold.set()
    final = wait(rpc, rid, ("COMPLETED", "HALTED"))
    assert final["display_status"] == "HALTED"
    [a] = attempts(rpc, rid, "c1")
    assert a["code"] == "RESULT_RECEIPT_MISMATCH" and a["retry_decision"]["reason_code"] == "NON_RETRYABLE_FAILURE"
    assert not any(p.startswith("SCRIPT ok") for p in user_prompts(env.provider))


def test_duplicate_key_payload_refused_by_publisher(env):
    rpc = env.rpc()
    rid = prepare(rpc, "SCRIPT dupkey\nx")
    assert run_to_end(rpc, rid)["display_status"] == "COMPLETED"
    [a] = attempts(rpc, rid, "c1")
    session = (env.data / "runs/local" / rid / a["session_file"]).read_text().splitlines()
    results = [json.loads(l)["message"] for l in session if '"toolResult"' in l]
    assert results[0]["isError"] is True and "duplicate key" in json.dumps(results[0])
    committed = env.data / "runs/local" / rid / a["handover"]["path"]
    assert json.loads(committed.read_text())["payload"] == {"answer": 3}


def test_corrupted_committed_handover_blocks_consumer_before_inference(env):
    rpc = env.rpc()
    manifest = """
cartridges:
  - {id: c1, model: w, prompt_file: a.md}
  - {id: c2, model: w, prompt_file: b.md, handover: [{from: c1, select: /answer, as: answer}]}
"""
    pid = import_project(rpc, manifest, {"a.md": "SCRIPT value:7\nx", "b.md": "SCRIPT halt\ny"})
    rid = prepare_project(rpc, pid)
    assert run_to_end(rpc, rid)["display_status"] == "HALTED"
    [a] = attempts(rpc, rid, "c1")
    committed = env.data / "runs/local" / rid / a["handover"]["path"]
    committed.write_text(committed.read_text().replace('"answer":7', '"answer":8'))
    before = len(env.provider.requests)
    rpc.call("recover", {"run_id": rid})
    assert run_to_end(rpc, rid)["display_status"] == "HALTED"
    assert len(env.provider.requests) == before  # no inference for the consumer
    last = attempts(rpc, rid, "c2")[-1]
    assert last["code"] == "RESULT_COMMIT_MISMATCH" and last["recovery"] == "hard"


def test_tool_allowlist_validation_and_isolation(env):
    rpc = env.rpc()
    for bad in (["*"], ["+read"], ["magazine_publish_result"], ["re ad"]):
        refused = rpc.call("prepare", {"magazine": json.dumps(doc("x")), "model_map": {"job": "fixture/model-a"},
                                       "tool_ids": bad, "handover_mode": "result_file_v1"}, ok=False)
        assert refused["code"] == 400, refused
    # MCP servers configured globally or in the project are never started.
    marker = env.tmp / "mcp-started"
    server = {"mcpServers": {"probe": {"command": "python3", "args": ["-c", f"open({str(marker)!r},'w')"]}}}
    (Path(env.config["agentDir"]) / "mcp.json").write_text(json.dumps(server))
    (env.cwd / ".pi").mkdir()
    (env.cwd / ".pi/mcp.json").write_text(json.dumps(server))
    rid = prepare(rpc, "SCRIPT ok\nx", tools=["read"])
    assert run_to_end(rpc, rid)["display_status"] == "COMPLETED"
    assert not marker.exists()
    [a] = attempts(rpc, rid, "c1")
    assert a["active_tools"] == ["magazine_publish_result", "read"]


# --------------------------------------------------------------------- Antiloop
def test_antiloop_stops_confirmed_loop_then_no_progress(env):
    rpc = env.rpc()
    rid = prepare(rpc, "SCRIPT loop:12\nList forever", tools=["ls"])
    final = run_to_end(rpc, rid, 120)
    assert final["display_status"] == "HALTED"
    tried = attempts(rpc, rid, "c1")
    assert [a["code"] for a in tried] == ["LOOP_DETECTED", "LOOP_DETECTED"]
    assert tried[0]["retry_decision"]["decision"] == "retry"
    assert tried[1]["retry_decision"]["reason_code"] == "NO_PROGRESS"
    evidence = tried[0]["loop_evidence"]
    assert evidence["reason"] == "identical_call_result" and evidence["repetitions"] == 5
    assert evidence.get("abort_acknowledged") or evidence.get("process_terminated")
    assert tried[0]["loop_police"]["warnings"][0]["repetitions"] == 3
    assert any("Antiloop" in item["text"] for item in ledger(rpc, rid)["activity"])


def test_antiloop_off_allows_repetition_and_parallel_batches_are_not_loops(env):
    rpc = env.rpc()
    rid = prepare(rpc, "SCRIPT loop:8\nList", tools=["ls"], antiloop=False)
    assert run_to_end(rpc, rid)["display_status"] == "COMPLETED"
    [a] = attempts(rpc, rid, "c1")
    assert a["loop_police"] == {"enabled": False}
    rid = prepare(rpc, "SCRIPT parallel\nList", tools=["ls"])
    assert run_to_end(rpc, rid)["display_status"] == "COMPLETED"
    [a] = attempts(rpc, rid, "c1")
    # Five parallel ls calls collapse to one event; the publisher call is the second.
    assert a["loop_police"]["observed_events"] == 2
    assert [e["batch_size"] for e in a["loop_police"]["history"]][:1] == [5]


def test_antiloop_setting_frozen_at_start(env):
    rpc = env.rpc()
    rid = prepare(rpc, "SCRIPT ok\nx")
    assert rpc.call("set_antiloop", {"run_id": rid, "enabled": False})["antiloop"] is False
    assert rpc.call("status", {"run_id": rid})["protocol"]["antiloop"] is False
    assert run_to_end(rpc, rid)["display_status"] == "COMPLETED"
    refused = rpc.call("set_antiloop", {"run_id": rid, "enabled": True}, ok=False)
    assert refused["code"] == 409
    status = rpc.call("status", {"run_id": rid})
    assert status["protocol"]["antiloop_frozen"] is True and status["protocol"]["antiloop"] is False


# ------------------------------------------------------------- STOP and restart
def test_stop_preserves_prior_results_and_is_not_resumable(env):
    rpc = env.rpc()
    rid = prepare(rpc, "SCRIPT ok\nfirst", "SCRIPT hold\nsecond", "SCRIPT ok\nthird")
    rpc.call("start", {"run_id": rid})
    assert env.provider.holding.wait(30)
    result = rpc.call("stop", {"run_id": rid})
    assert result["status"] == "stopped"
    status = wait(rpc, rid, ("STOPPED",))
    assert status["done"] == ["c1"]
    assert rpc.call("results", {"run_id": rid})["results"]["c1"].endswith('{"status":"DONE"}')
    [a] = attempts(rpc, rid, "c2")
    assert a["status"] == "stopped" and a["retry_decision"]["reason_code"] == "OPERATOR_STOP"
    assert rpc.call("recover", {"run_id": rid}, ok=False)["code"] == 409
    assert rpc.call("start", {"run_id": rid}, ok=False)["code"] == 409
    assert not any(p.startswith("SCRIPT ok\nthird") for p in user_prompts(env.provider))


def test_process_interruption_is_not_resumed_automatically(env):
    rpc = env.rpc()
    rid = prepare(rpc, "SCRIPT ok\nfirst", "SCRIPT hold-once\nsecond")
    rpc.call("start", {"run_id": rid})
    assert env.provider.holding.wait(30)
    rpc = env.restart(kill=True)
    time.sleep(0.5)
    env.provider.hold.set()
    count = len(env.provider.requests)
    status = rpc.call("status", {"run_id": rid})
    assert status["status"] == "RUNNING" and not status["active"]
    [a] = attempts(rpc, rid, "c2")
    assert a["status"] == "interrupted" and a["code"] == "INTERRUPTED"
    time.sleep(1)
    assert len(env.provider.requests) == count  # nothing restarted on its own
    refused = rpc.call("start", {"run_id": rid}, ok=False)
    assert refused["code"] == 409 and wait(rpc, rid, ("HALTED",))["done"] == ["c1"]
    rpc.call("recover", {"run_id": rid})
    assert run_to_end(rpc, rid)["display_status"] == "COMPLETED"
    assert [x["status"] for x in attempts(rpc, rid, "c2")] == ["interrupted", "done"]


# ---------------------------------------------------------------------- tokens
def test_token_accounting_with_retries_and_missing_usage(env):
    rpc = env.rpc()
    env.provider.usage = {"prompt_tokens": 120, "completion_tokens": 8, "prompt_tokens_details": {"cached_tokens": 20}}
    rid = prepare(rpc, "SCRIPT nodone-until:2\nx")
    assert run_to_end(rpc, rid)["display_status"] == "COMPLETED"
    first, second = attempts(rpc, rid, "c1")
    for a in (first, second):
        assert a["tokens"]["state"] == "known" and a["tokens"]["rounds"] == 2
        assert (a["tokens"]["input"], a["tokens"]["cache_read"], a["tokens"]["output"], a["tokens"]["total"]) == (200, 40, 16, 256)
    status = rpc.call("status", {"run_id": rid})
    assert status["tokens"]["total"] == 512 and status["tokens"]["state"] == "known"
    assert rpc.call("status", {"run_id": rid})["tokens"] == status["tokens"]  # re-reading adds nothing
    env.provider.usage = None
    rid2 = prepare(rpc, "SCRIPT ok\ny")
    assert run_to_end(rpc, rid2)["display_status"] == "COMPLETED"
    t = rpc.call("status", {"run_id": rid2})["tokens"]
    assert t["state"] == "unavailable" and t["total"] is None and t["unavailable_attempts"] == 1


# ------------------------------------------------------------- legacy records
def test_legacy_pi5_run_keeps_single_attempt_semantics(env):
    rpc = env.rpc()
    rid = prepare(rpc, "SCRIPT nodone\nlegacy", mode=None)
    base = env.data / "runs/local" / rid
    (base / "protocol.json").unlink()  # what a pi.5-prepared READY run looks like
    status = rpc.call("status", {"run_id": rid})
    assert status["protocol"]["legacy"] and status["protocol"]["handover_mode"] == "pi5_legacy"
    assert status["protocol"]["antiloop"] is False
    assert rpc.call("set_antiloop", {"run_id": rid, "enabled": True}, ok=False)["code"] == 409
    final = run_to_end(rpc, rid)
    assert final["display_status"] == "COMPLETED"  # pi.5 accepted plain final text
    [a] = attempts(rpc, rid, "c1")
    assert a["protocol"] == "pi5_legacy" and not (base / "protocol.json").exists()


def test_results_zip_ledger_is_redacted(env):
    rpc = env.rpc()
    rid = prepare(rpc, "SCRIPT halt\nx")
    assert run_to_end(rpc, rid)["display_status"] == "HALTED"
    base = env.data / "runs/local" / rid
    value = json.loads((base / "ledger.json").read_text())
    value["cartridges"]["c1"]["attempts"][0]["note"] = "provider said api_key=sk-SECRET123456 and Bearer abc.def"
    (base / "ledger.json").write_text(json.dumps(value))
    archive = base64.b64decode(rpc.call("download", {"run_id": rid})["file_data_b64"])
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        text = z.read("magazine-ledger/ledger.json").decode() + z.read("magazine-ledger/LEDGER.md").decode()
    assert "sk-SECRET123456" not in text and "abc.def" not in text and "[REDACTED]" in text
