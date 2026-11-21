"""pi.7 scheduler: Sequential / Round Robin, durable journal, restart retention.

Real Pi SDK + real Magazine service + scripted loopback provider (no model).
Prompt bodies are "SCRIPT <behaviour>\\n<label>"; ``first_prompts`` returns
the labels in the order each cartridge's first model round arrived.
"""
import asyncio
import json
import os
import time
from pathlib import Path

import pytest

import scheduler as sch
from pi6_fakes import wait
from test_pi6_integration import env, attempts, ledger  # noqa: F401  (fixture + helpers)

needs_sdk = pytest.mark.skipif(not os.environ.get("MAGAZINE_PI_SDK"), reason="needs MAGAZINE_PI_SDK")


def run_doc(rpc, label, scripts, mode="result_file_v1"):
    doc = {"schema_version": "1.0", "cartridges": [{"id": f"{label}{i+1}", "job_type": "j", "prompt": f"SCRIPT {s}\n{label}{i+1}"}
                                                   for i, s in enumerate(scripts)]}
    return rpc.call("prepare", {"magazine": json.dumps(doc), "model_map": {"j": "fixture/model-a"},
                                "tool_ids": [], "handover_mode": mode})["run_id"]


def until(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not reached")


# ------------------------------------------------------------- journal units
def test_journal_roundtrip_retains_order_and_never_dispatches(tmp_path):
    async def go():
        launched = []
        async def launcher(entry):
            launched.append(entry["run_id"])
        s = sch.Scheduler(tmp_path, launcher)
        s.set_mode("round_robin")
        s.admit("a" * 12, "local", 0)
        s.admit("b" * 12, "local", 2)
        s.freeze()
        t = sch.Scheduler(tmp_path, launcher)
        assert t.mode == "round_robin"
        assert [e["run_id"] for e in t.queue] == ["a" * 12, "b" * 12]
        assert all(e["needs_start"] for e in t.queue) and t.next_eligible() is None
        await t.pump()
        assert launched == []  # retained entries need an explicit Start
        t.admit("b" * 12, "local", 3)  # explicit Start keeps the retained place
        assert [e["run_id"] for e in t.queue] == ["a" * 12, "b" * 12] and t.next_eligible()["run_id"] == "b" * 12
        raw = json.loads((tmp_path / "scheduler-v1.json").read_text())
        assert set(raw) == {"schema_version", "mode", "current", "queue", "notes"}
        assert all(set(e) == {"run_id", "owner", "from_index", "prepared"} for e in raw["queue"])
    asyncio.run(go())


@pytest.mark.parametrize("content", [
    "{not json", '{"schema_version":2,"mode":"sequential","queue":[]}',
    '{"schema_version":1,"mode":"fast","queue":[]}',
    '{"schema_version":1,"mode":"sequential","mode":"round_robin","queue":[]}',
    '{"schema_version":1,"mode":"sequential","queue":[{"run_id":"../x","owner":"local","from_index":0,"prepared":false}]}',
    '{"schema_version":1,"mode":"sequential","queue":[{"run_id":"aaaaaaaaaaaa","owner":"local","from_index":-1,"prepared":false}]}',
    '{"schema_version":1,"mode":"sequential","queue":[{"run_id":"aaaaaaaaaaaa","owner":"local","from_index":0,"prepared":false},'
    '{"run_id":"aaaaaaaaaaaa","owner":"local","from_index":0,"prepared":false}]}'])
def test_corrupt_journal_blocks_dispatch_and_is_kept(tmp_path, content):
    (tmp_path / "scheduler-v1.json").write_text(content)
    async def launcher(entry):
        raise AssertionError("must not launch")
    s = sch.Scheduler(tmp_path, launcher)
    assert s.persistence_error and "requires repair" in s.persistence_error
    with pytest.raises(sch.SchedulerError):
        s.admit("a" * 12, "local", 0)
    with pytest.raises(sch.SchedulerError):
        s.set_mode("round_robin")
    assert (tmp_path / "scheduler-v1.json").read_text() == content


def test_mode_validation(tmp_path):
    s = sch.Scheduler(tmp_path, None)
    with pytest.raises(sch.SchedulerError):
        s.set_mode("parallel")
    assert s.mode == "sequential"


# --------------------------------------------------------------- integration
@needs_sdk
def test_sequential_runs_one_project_at_a_time(env):
    rpc = env.rpc()
    gate = env.provider.gate("A")
    a = run_doc(rpc, "A", ["gate:A", "ok", "ok"])
    b = run_doc(rpc, "B", ["ok", "ok"])
    assert rpc.call("start", {"run_id": a})["status"] == "accepted"
    assert env.provider.wait_reached("A")
    started = rpc.call("start", {"run_id": b})
    assert started["status"] == "queued" and started["queue_position"] == 1
    status = rpc.call("status", {"run_id": b})
    assert status["status"] == "READY" and status["display_status"] == "QUEUED" and status["queue"]["next"]
    assert rpc.call("start", {"run_id": b}, ok=False)["code"] == 409  # already waiting
    gate.set()
    wait(rpc, a, ("COMPLETED",))
    wait(rpc, b, ("COMPLETED",))
    assert env.provider.first_prompts() == ["A1", "A2", "A3", "B1", "B2"]
    assert env.provider.max_inflight == 1
    # The slot is released when the run's task ends (just after COMPLETED is saved).
    view = until(lambda: (v := rpc.call("scheduler"))["current"] is None and v)
    assert view["queue"] == [] and view["mode"] == "sequential"


@needs_sdk
def test_round_robin_rotates_after_committed_cartridges(env):
    rpc = env.rpc()
    assert rpc.call("set_scheduler", {"mode": "round_robin"})["mode"] == "round_robin"
    gate = env.provider.gate("A")
    a = run_doc(rpc, "A", ["gate:A", "ok", "ok"])
    b = run_doc(rpc, "B", ["ok", "ok"])
    c = run_doc(rpc, "C", ["value:4", "ok"])
    rpc.call("start", {"run_id": a})
    assert env.provider.wait_reached("A")
    assert rpc.call("start", {"run_id": b})["queue_position"] == 1
    assert rpc.call("start", {"run_id": c})["queue_position"] == 2
    gate.set()
    for rid in (a, b, c):
        wait(rpc, rid, ("COMPLETED",), 90)
    assert env.provider.first_prompts() == ["A1", "B1", "C1", "A2", "B2", "C2", "A3"]
    assert env.provider.max_inflight == 1
    for rid, n in ((a, 3), (b, 2), (c, 2)):
        value = ledger(rpc, rid)
        assert all(len(r["attempts"]) == 1 and r["attempts"][0]["phase"] == "committed" for r in value["cartridges"].values())
        assert len(value["cartridges"]) == n


@needs_sdk
def test_mode_change_mid_attempt_takes_effect_at_next_boundary(env):
    rpc = env.rpc()
    rpc.call("set_scheduler", {"mode": "round_robin"})
    gate = env.provider.gate("A")
    a = run_doc(rpc, "A", ["gate:A", "ok"])
    b = run_doc(rpc, "B", ["ok"])
    rpc.call("start", {"run_id": a})
    assert env.provider.wait_reached("A")
    rpc.call("start", {"run_id": b})
    rpc.call("set_scheduler", {"mode": "sequential"})  # during A1: no cancellation, applies at the boundary
    gate.set()
    wait(rpc, a, ("COMPLETED",)); wait(rpc, b, ("COMPLETED",))
    assert env.provider.first_prompts() == ["A1", "A2", "B1"]
    assert [x["status"] for x in attempts(rpc, a, "A1")] == ["done"]


@needs_sdk
def test_stop_queued_and_rotated_runs_independently(env):
    rpc = env.rpc()
    rpc.call("set_scheduler", {"mode": "round_robin"})
    gate_a, gate_c = env.provider.gate("A"), env.provider.gate("C")
    a = run_doc(rpc, "A", ["gate:A", "ok"])
    b = run_doc(rpc, "B", ["ok", "ok", "ok"])
    c = run_doc(rpc, "C", ["gate:C", "ok"])
    d = run_doc(rpc, "D", ["ok"])
    rpc.call("start", {"run_id": a})
    assert env.provider.wait_reached("A")
    for rid in (b, c, d):
        rpc.call("start", {"run_id": rid})
    # D has not entered the core: removing it leaves it READY.
    removed = rpc.call("stop", {"run_id": d})
    assert removed["status"] == "removed_from_queue"
    status = rpc.call("status", {"run_id": d})
    assert status["status"] == "READY" and status["display_status"] == "READY" and not status["control"]
    gate_a.set()
    assert env.provider.wait_reached("C")  # A1, B1 committed and rotated; C1 holds the slot
    # B is now waiting after a committed cartridge: STOP makes it STOPPED, not resumable.
    until(lambda: rpc.call("status", {"run_id": b})["display_status"] == "QUEUED")
    stopped = rpc.call("stop", {"run_id": b})
    assert stopped["status"] == "stopped"
    status = rpc.call("status", {"run_id": b})
    assert status["display_status"] == "STOPPED" and status["done"] == ["B1"]
    assert rpc.call("recover", {"run_id": b}, ok=False)["code"] == 409
    gate_c.set()
    wait(rpc, a, ("COMPLETED",)); wait(rpc, c, ("COMPLETED",))
    assert rpc.call("status", {"run_id": b})["display_status"] == "STOPPED"  # stays stopped after refresh
    assert "B2" not in env.provider.first_prompts()
    assert rpc.call("start", {"run_id": d})["status"] == "accepted"
    wait(rpc, d, ("COMPLETED",))
    assert env.provider.max_inflight == 1


@needs_sdk
def test_failed_or_halted_run_releases_the_slot(env):
    rpc = env.rpc()
    gate = env.provider.gate("A")
    a = run_doc(rpc, "A", ["gate:A", "halt"])
    b = run_doc(rpc, "B", ["ok"])
    rpc.call("start", {"run_id": a})
    assert env.provider.wait_reached("A")
    rpc.call("start", {"run_id": b})
    gate.set()
    assert wait(rpc, a, ("HALTED",))["display_status"] == "HALTED"
    assert wait(rpc, b, ("COMPLETED",))["display_status"] == "COMPLETED"


@needs_sdk
def test_restart_retains_queue_without_unattended_execution(env):
    rpc = env.rpc()
    rpc.call("set_scheduler", {"mode": "round_robin"})
    a = run_doc(rpc, "A", ["ok", "hold-once", "ok"])
    b = run_doc(rpc, "B", ["ok", "gate:B2"])
    gate_b = env.provider.gate("B2")
    rpc.call("start", {"run_id": a})
    rpc.call("start", {"run_id": b})
    assert env.provider.holding.wait(30)  # A2 in progress, B waiting after B1
    rpc = env.restart(kill=True)
    time.sleep(0.5)
    env.provider.hold.set()
    count = len(env.provider.requests)
    view = rpc.call("scheduler")
    assert view["mode"] == "round_robin"
    assert {e["run_id"] for e in view["queue"]} == {a, b} and all(e["recovery_required"] for e in view["queue"])
    for rid in (a, b):
        status = rpc.call("status", {"run_id": rid})
        assert status["queue"]["recovery_required"] and status["display_status"] != "QUEUED"
    time.sleep(1.5)
    assert len(env.provider.requests) == count  # nothing ran on its own
    # Both were inside the core: Start auto-halts them, recovery returns them to READY.
    for rid in (a, b):
        assert rpc.call("start", {"run_id": rid}, ok=False)["code"] == 409
        rpc.call("recover", {"run_id": rid})
    assert [e["run_id"] for e in rpc.call("scheduler")["queue"]] == [a, b]  # order retained through recovery
    before = len(env.provider.first_prompts())
    assert before == 3  # A1, B1, A2 (interrupted)
    assert rpc.call("start", {"run_id": b})["status"] == "accepted"  # A is retained but not started: B may progress
    assert env.provider.wait_reached("B2")  # B2 holds the slot
    assert rpc.call("start", {"run_id": a})["status"] == "queued"
    gate_b.set()
    wait(rpc, a, ("COMPLETED",), 90); wait(rpc, b, ("COMPLETED",), 90)
    assert env.provider.max_inflight == 1
    assert env.provider.first_prompts()[before:] == ["B2", "A2", "A3"]
    # Committed cartridges were never repeated; the interrupted one got a fresh attempt.
    assert [x["status"] for x in attempts(rpc, a, "A1")] == ["done"]
    assert [x["status"] for x in attempts(rpc, a, "A2")] == ["interrupted", "done"]
    assert [x["status"] for x in attempts(rpc, b, "B1")] == ["done"]
    assert env.provider.first_prompts().count("B1") == 1


@needs_sdk
def test_retained_ready_entry_keeps_position_and_can_be_removed(env):
    rpc = env.rpc()
    gate = env.provider.gate("A")
    a = run_doc(rpc, "A", ["gate:A"])
    b = run_doc(rpc, "B", ["ok"])
    c = run_doc(rpc, "C", ["ok"])
    rpc.call("start", {"run_id": a})
    assert env.provider.wait_reached("A")
    rpc.call("start", {"run_id": b}); rpc.call("start", {"run_id": c})
    rpc = env.restart()  # graceful plugin shutdown: retained, nothing dispatched
    gate.set()
    view = rpc.call("scheduler")
    assert [e["run_id"] for e in view["queue"]] == [a, b, c] and all(e["recovery_required"] for e in view["queue"])
    assert rpc.call("status", {"run_id": b})["status"] == "READY"
    assert rpc.call("stop", {"run_id": c})["status"] == "removed_from_queue"
    assert rpc.call("start", {"run_id": b})["status"] == "accepted"  # A still needs recovery; B may progress
    wait(rpc, b, ("COMPLETED",))
    assert [e["run_id"] for e in rpc.call("scheduler")["queue"]] == [a]


@needs_sdk
def test_corrupt_journal_refuses_start_and_keeps_file(env):
    rpc = env.rpc()
    rid = run_doc(rpc, "A", ["ok"])
    rpc.close()
    journal = env.data / "scheduler-v1.json"
    journal.write_text("{broken")
    rpc = env.restart()
    view = rpc.call("scheduler")
    assert "requires repair" in view["error"]
    refused = rpc.call("start", {"run_id": rid}, ok=False)
    assert refused["code"] == 409 and "requires repair" in refused["error"]
    assert journal.read_text() == "{broken" and env.provider.requests == []
