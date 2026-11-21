"""pi.6 unit tests: pure algorithms, simulated Pi events, isolated temp storage.

No model, no provider, no network. Every Pi event below is a fixture.
"""
import json
import subprocess
import textwrap
from pathlib import Path

import pytest

import antiloop
import handover as ho
import ledger as lg
import protocol as proto
import tokens
import executor as ex

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------ recovery policy
def test_recovery_defaults_are_five_attempts_of_1800_seconds():
    p = proto.recovery_policy()
    assert p == {"max_attempts": 5, "attempt_seconds": 1800, "max_retries": 4, "seconds": 1800, "io_retries": 2}


@pytest.mark.parametrize("value,expected", [
    ({"max_retries": 5}, 5), ({"max_retries": 0}, 1), ({"max_attempts": 3}, 3), ({"max_retries": 2}, 3)])
def test_recovery_aliases_never_admit_a_sixth_attempt(value, expected):
    assert proto.recovery_policy(value)["max_attempts"] == expected


@pytest.mark.parametrize("value", [{"max_attempts": 6}, {"max_attempts": 0}, {"attempt_seconds": 0},
    {"attempt_seconds": 7201}, {"max_attempts": 2, "max_retries": 3}, {"seconds": 60, "attempt_seconds": 61},
    {"unknown": 1}, {"max_retries": True}, {"io_retries": 6}])
def test_recovery_rejects_invalid_or_conflicting(value):
    with pytest.raises(proto.ProtocolError):
        proto.recovery_policy(value)


def test_loop_policy_defaults_and_validation():
    assert proto.loop_policy() == {"enabled": True, "warn_repeats": 3, "stop_repeats": 5,
                                   "error_repeats": 3, "cycle_repeats": 3, "history_size": 64}
    for bad in ({"warn_repeats": 5, "stop_repeats": 5}, {"history_size": 8}, {"enabled": "yes"}, {"x": 1}):
        with pytest.raises(proto.ProtocolError):
            proto.loop_policy(bad)


# ---------------------------------------------------------------- retry policy
def failed(code, recovery="repair", status="failed"):
    return {"status": status, "code": code, "recovery": recovery}


def test_five_identical_done_missing_failures_use_four_retries_then_stop():
    decisions = []
    fingerprints = set()
    for n in range(1, 6):
        attempt = failed("DONE_MISSING")
        fp = "same"
        d = lg.decide_retry(attempt, n, 5 - n, repeated=fp in fingerprints)
        fingerprints.add(fp)
        decisions.append(d["decision"])
        if d["decision"] != "retry":
            assert d["reason_code"] == "MAX_ATTEMPTS_REACHED"
    assert decisions == ["retry", "retry", "retry", "retry", "stop"]


def test_repeated_non_completion_failure_stops_early_as_no_progress():
    d = lg.decide_retry(failed("LOOP_DETECTED"), 2, 3, repeated=True)
    assert (d["decision"], d["reason_code"]) == ("stop", "NO_PROGRESS")
    d = lg.decide_retry(failed("ATTEMPT_TIMEOUT"), 1, 4, repeated=False)
    assert (d["decision"], d["reason_code"]) == ("retry", "RECOVERABLE_FAILURE")


@pytest.mark.parametrize("code", ["DONE_MISSING", "OUTPUT_MISSING", "NO_FINAL_ANSWER", "INCOMPLETE_RESPONSE"])
def test_repeated_completion_omissions_remain_retryable(code):
    d = lg.decide_retry(failed(code), 3, 2, repeated=True)
    assert (d["decision"], d["reason_code"]) == ("retry", "RECOVERABLE_COMPLETION")


def test_publish_recovery_is_completion_failure():
    d = lg.decide_retry(failed("RESULT_MISSING", "publish"), 2, 3, repeated=True)
    assert d["decision"] == "retry" and d["recoverable_completion_failure"]


@pytest.mark.parametrize("attempt,reason", [
    (failed("RESULT_IDENTITY_MISMATCH", "hard"), "NON_RETRYABLE_FAILURE"),
    (failed("STOP_UNCONFIRMED", "hard"), "NON_RETRYABLE_FAILURE"),
    ({"status": "halted", "code": "HALT_RECEIVED"}, "HALT_RECEIVED"),
    ({"status": "stopped", "code": "STOPPED"}, "OPERATOR_STOP"),
    ({"status": "interrupted", "code": "CANCELLED"}, "CANCELLED")])
def test_genuine_blockers_stop(attempt, reason):
    assert lg.decide_retry(attempt, 1, 4)["reason_code"] == reason


def test_stop_request_overrides_retry_and_configured_limit_respected():
    assert lg.decide_retry(failed("DONE_MISSING"), 1, 4, stop_requested=True)["reason_code"] == "OPERATOR_STOP"
    assert lg.decide_retry(failed("DONE_MISSING"), 2, 0)["reason_code"] == "CONFIGURED_ATTEMPTS_EXHAUSTED"


# -------------------------------------------------------------------- Antiloop
def call(cid, name="ls", args="a"):
    return {"event": "assistant", "toolCalls": [{"id": cid, "name": name, "argsSha256": args}]}


def result(cid, name="ls", res="r", error=False, no_change=False):
    return {"event": "tool_result", "toolCallId": cid, "toolName": name, "resultSha256": res,
            "isError": error, "noChange": no_change}


def feed(police, n, **kw):
    for i in range(n):
        police.observe(call(f"c{i}", kw.get("name", "ls"), kw.get("args", "a")))
        police.observe(result(f"c{i}", kw.get("name", "ls"), kw.get("res", "r"), kw.get("error", False), kw.get("no_change", False)))


def test_antiloop_warns_at_three_and_stops_at_five():
    police = antiloop.LoopPolice()
    feed(police, 4)
    assert len(police.warnings) == 1 and police.warnings[0]["repetitions"] == 3
    with pytest.raises(antiloop.LoopDetected) as caught:
        police.observe(call("c4")); police.observe(result("c4"))
    assert caught.value.evidence["reason"] == "identical_call_result"
    assert caught.value.evidence["repetitions"] == 5


def test_antiloop_three_identical_errors_stop():
    police = antiloop.LoopPolice()
    feed(police, 2, error=True)
    with pytest.raises(antiloop.LoopDetected) as caught:
        police.observe(call("e")); police.observe(result("e", error=True))
    assert caught.value.evidence["reason"] == "identical_errors"


def test_antiloop_short_cycle():
    police = antiloop.LoopPolice()
    seq = ["a", "b"] * 3
    with pytest.raises(antiloop.LoopDetected) as caught:
        for i, r in enumerate(seq):
            police.observe(call(f"k{i}", args=r)); police.observe(result(f"k{i}", res=r))
    assert caught.value.evidence["reason"] == "short_cycle"


def test_antiloop_false_positive_controls():
    police = antiloop.LoopPolice()
    # Silence / text only: nothing counted.
    police.observe({"event": "assistant", "toolCalls": []})
    police.observe({"event": "streaming"})
    assert police.observed == 0
    # Unfinished tool calls (no result) are never counted.
    for i in range(10):
        police.observe(call(f"u{i}"))
    assert police.observed == 0
    # Repeated snapshots of the same call/result are not new events.
    police = antiloop.LoopPolice()
    police.observe(call("x")); police.observe(result("x"))
    for _ in range(10):
        police.observe(call("x")); police.observe(result("x"))
    assert police.observed == 1
    # Changing results break exact repetition.
    police = antiloop.LoopPolice()
    for i in range(10):
        police.observe(call(f"p{i}")); police.observe(result(f"p{i}", res=f"r{i}"))
    assert police.observed == 10
    # Results for unknown calls are ignored.
    police.observe(result("never-issued"))
    assert police.observed == 10


def test_antiloop_parallel_batch_is_one_event():
    police = antiloop.LoopPolice()
    for b in range(4):
        ids = [f"b{b}-{i}" for i in range(5)]
        police.observe({"event": "assistant", "toolCalls": [{"id": i, "name": "ls", "argsSha256": "a"} for i in ids]})
        for i in ids[:-1]:
            police.observe(result(i))
        assert police.observed == b  # batch incomplete until its last result
        police.observe(result(ids[-1]))
    assert police.observed == 4  # 20 parallel calls are 4 serial repetitions, not 20


def test_antiloop_mutation_resets_unless_no_change():
    police = antiloop.LoopPolice()
    for i in range(20):
        police.observe(call(f"w{i}", "write")); police.observe(result(f"w{i}", "write"))
    assert police.history == []  # each successful write is progress evidence
    police = antiloop.LoopPolice()
    with pytest.raises(antiloop.LoopDetected):
        for i in range(5):
            police.observe(call(f"n{i}", "edit")); police.observe(result(f"n{i}", "edit", no_change=True))


def test_antiloop_disabled_and_bounded_identity_state():
    police = antiloop.LoopPolice({"enabled": False})
    feed(police, 20)
    assert police.observed == 0
    police = antiloop.LoopPolice()
    police.seen = set(range(antiloop.MAX_SEEN_IDS))
    police.observe(call("z")); police.observe(result("z"))
    assert police.limit_reached and police.observed == 0
    police = antiloop.LoopPolice({"history_size": 12, "stop_repeats": 11, "warn_repeats": 10})
    for i in range(30):
        police.observe(call(f"h{i}")); police.observe(result(f"h{i}", res=str(i % 7)))
    assert len(police.history) <= 12


# ---------------------------------------------------------------------- tokens
def session_file(tmp_path, usages, tail=b""):
    lines = [json.dumps({"type": "session", "id": "s1"})]
    for i, u in enumerate(usages):
        lines.append(json.dumps({"type": "message", "id": f"m{i}", "message": {"role": "assistant", "usage": u}}))
        lines.append(json.dumps({"type": "message", "id": f"t{i}", "message": {"role": "toolResult"}}))
    path = tmp_path / "s.jsonl"
    path.write_bytes(("\n".join(lines) + "\n").encode() + tail)
    return path


U = lambda i, o, cr=0, cw=0: {"input": i, "output": o, "cacheRead": cr, "cacheWrite": cw,
                              "totalTokens": i + o + cr + cw, "cost": {}}


def test_tokens_known_sum_rounds_without_double_counting_reasoning(tmp_path):
    u = U(100, 10, 5, 0); u["reasoning"] = 4
    s = tokens.from_session(session_file(tmp_path, [u, U(200, 20)]), finished_normally=True)
    assert (s["state"], s["input"], s["output"], s["cache_read"], s["total"], s["reasoning"]) == ("known", 300, 30, 5, 335, 4)


def test_tokens_zero_placeholder_is_unavailable_not_zero(tmp_path):
    s = tokens.from_session(session_file(tmp_path, [U(0, 0)]), finished_normally=True)
    assert s["state"] == "unavailable" and s["total"] is None
    mixed = tokens.from_session(session_file(tmp_path, [U(0, 0), U(50, 5)]), finished_normally=True)
    assert mixed["state"] == "partial" and mixed["total"] == 55 and mixed["rounds"] == 2 and mixed["rounds_reported"] == 1


def test_tokens_interrupted_and_partial_tail(tmp_path):
    s = tokens.from_session(session_file(tmp_path, [U(10, 1)]), finished_normally=False)
    assert s["state"] == "partial" and s["incomplete"]
    t = tokens.from_session(session_file(tmp_path, [U(10, 1)], tail=b'{"type":"message","id":"m9","mess'), finished_normally=True)
    assert t["state"] == "partial" and t["total"] == 11
    assert tokens.from_session(tmp_path / "missing.jsonl", finished_normally=True)["state"] == "unavailable"


def test_tokens_rereading_replaces_and_retries_add(tmp_path):
    path = session_file(tmp_path, [U(10, 1)])
    first = tokens.from_session(path, finished_normally=True)
    second = tokens.from_session(path, finished_normally=True)
    assert first == second  # a re-read is a replacement snapshot
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines + [lines[1]]) + "\n")  # duplicated entry id
    assert tokens.from_session(path, finished_normally=True)["total"] == 11
    run = tokens.total([first, tokens.unavailable(), first])
    assert run["total"] == 22 and run["incomplete"] and run["unavailable_attempts"] == 1 and run["state"] == "partial"
    assert tokens.total([])["state"] == "unavailable"


def test_ledger_recompute_counts_each_attempt_once(tmp_path):
    value = lg.new("abc", ["c1", "c2"], "result_file_v1")
    snap = tokens.summarize(1, [tokens.message_usage(U(5, 1))], True)
    value["cartridges"]["c1"]["attempts"] = [{"tokens": snap}, {"tokens": snap}]
    value["cartridges"]["c2"]["attempts"] = [{"tokens": tokens.unavailable()}]
    lg.save(tmp_path, value)
    lg.save(tmp_path, lg.load(tmp_path))
    again = lg.load(tmp_path)
    assert again["tokens"]["total"] == 12 and again["cartridges"]["c1"]["tokens"]["total"] == 12
    assert again["tokens"]["incomplete"]


# ----------------------------------------------------------- reply judgement
@pytest.mark.parametrize("text,status", [
    ("All done.\n" + proto.DONE_LINE, "done"),
    ("Saved the file. " + proto.DONE_LINE, "done"),
    ("```json\n" + proto.DONE_LINE + "\n```", "done"),
    ("I said " + proto.DONE_LINE + " earlier but then more text", "failed"),
    ('Stop.\n{"status":"HALT","reason":"no input"}', "halted"),
    ("No closing line", "failed")])
def test_judge_requires_closing_done(text, status):
    assert proto.judge(text, [], True)[0] == status


def test_judge_missing_outputs():
    status, note = proto.judge("x\n" + proto.DONE_LINE, ["out.txt"], True)
    assert status == "failed" and "out.txt" in note


def test_select_pointer():
    assert proto.select_pointer({"a": {"b/c": [1, 2]}}, "/a/b~1c/1") == 2
    with pytest.raises(proto.ProtocolError):
        proto.select_pointer({"a": 1}, "/b")


# ---------------------------------------------------------- envelope/handover
def expected():
    return {"run_id": "r1", "cartridge_id": "c1", "attempt_id": "a1", "session_id": "s1"}


def envelope(**kw):
    return {**expected(), "schema": "magazine.result.v1", "payload": {"answer": 1}, **kw}


PROTO = {"result_profile": "generic", "cartridges": {
    "c1": {"handover": [], "inputs": [], "outputs": [], "retry_safe": False, "dir": "c1"},
    "c2": {"handover": [{"from": "c1", "select": "/answer", "as": "answer"}], "inputs": [], "outputs": [], "retry_safe": False, "dir": "c2"}}}


def test_envelope_accepts_exact_identity_and_selectors():
    assert ho.validate_envelope(envelope(), expected(), PROTO, "c1") == ["/answer"]


@pytest.mark.parametrize("doc,code", [
    (envelope(session_id="other"), "RESULT_IDENTITY_MISMATCH"),
    (envelope(attempt_id="a2"), "RESULT_IDENTITY_MISMATCH"),
    (envelope(run_id="r2"), "RESULT_IDENTITY_MISMATCH"),
    (envelope(schema="other"), "RESULT_SCHEMA_INVALID"),
    (envelope(payload=[1]), "RESULT_SCHEMA_INVALID"),
    (envelope(extra=1), "RESULT_SCHEMA_INVALID"),
    (envelope(payload={"other": 1}), "RESULT_POINTER_MISSING")])
def test_envelope_rejections(doc, code):
    with pytest.raises(ho.ResultFailure) as caught:
        ho.validate_envelope(doc, expected(), PROTO, "c1")
    assert caught.value.code == code


def test_task_profile_requires_checks_when_verified():
    p = dict(PROTO, result_profile="magazine.task.v1")
    good = envelope(payload={"answer": 1, "result": {"step": "c1", "verified": True, "files": [], "checks": ["x"], "note": ""}})
    assert ho.validate_envelope(good, expected(), p, "c1")
    bad = envelope(payload={"answer": 1, "result": {"step": "c1", "verified": True, "files": [], "checks": [], "note": ""}})
    with pytest.raises(ho.ResultFailure):
        ho.validate_envelope(bad, expected(), p, "c1")


def test_strict_result_file_reading(tmp_path):
    (tmp_path / "publish").mkdir()
    (tmp_path / "publish/result.json").write_text('{"a":1,"a":2}')
    with pytest.raises(ho.ResultFailure) as caught:
        ho.read_result_file(tmp_path)
    assert caught.value.code == "RESULT_JSON_INVALID" and caught.value.recovery == "publish"
    (tmp_path / "publish/result.json").write_text('{"a":NaN}')
    with pytest.raises(ho.ResultFailure):
        ho.read_result_file(tmp_path)
    (tmp_path / "publish/result.json").unlink()
    with pytest.raises(ho.ResultFailure) as caught:
        ho.read_result_file(tmp_path)
    assert caught.value.code == "RESULT_MISSING"
    (tmp_path / "real.json").write_text("{}")
    (tmp_path / "publish/result.json").symlink_to(tmp_path / "real.json")
    with pytest.raises(ho.ResultFailure) as caught:
        ho.read_result_file(tmp_path)
    assert caught.value.code == "RESULT_NOT_REGULAR"
    (tmp_path / "publish/result.json").unlink()
    (tmp_path / "publish/result.json").write_bytes(b" " * (ho.MAX_RESULT_BYTES + 1))
    with pytest.raises(ho.ResultFailure) as caught:
        ho.read_result_file(tmp_path)
    assert caught.value.code == "RESULT_TOO_LARGE"


def committed_fixture(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    layout = ho.Layout(tmp_path / "run", tmp_path / "ws", "r1", PROTO)
    attempt = {"attempt_id": "a1", "session_id": "s1"}
    session = tmp_path / "s.jsonl"
    session.write_text(json.dumps({"type": "session", "id": "s1"}) + "\n")
    attempt["session_archive"] = ho.archive_session(layout, "c1", attempt, session)
    doc = envelope()
    info = ho.commit_attempt_result(layout, "c1", attempt, doc, {"sha256": "x", "bytes": 1}, ["/answer"])
    attempt.update(handover=info, status="done", phase="committed")
    value = lg.new("r1", ["c1", "c2"], "result_file_v1")
    value["cartridges"]["c1"]["attempts"] = [attempt]
    return layout, value, attempt


def test_committed_handover_reverified_before_use(tmp_path):
    layout, value, attempt = committed_fixture(tmp_path)
    assert ho.committed_payload(layout, value, "c1") == {"answer": 1}
    path = layout.base / attempt["handover"]["path"]
    path.write_text(path.read_text().replace('"answer":1', '"answer":2'))
    with pytest.raises(ho.ResultFailure) as caught:
        ho.committed_payload(layout, value, "c1")
    assert caught.value.code == "RESULT_COMMIT_MISMATCH"


def test_committed_handover_archive_and_marker_tamper(tmp_path):
    layout, value, attempt = committed_fixture(tmp_path)
    (layout.base / attempt["session_archive"]["path"]).write_text("tampered\n")
    with pytest.raises(ho.ResultFailure):
        ho.committed_payload(layout, value, "c1")
    layout, value, attempt = committed_fixture(tmp_path / "b")
    marker = layout.base / Path(attempt["handover"]["path"]).with_name("commit.json")
    marker.write_text(marker.read_text().replace('"s1"', '"s2"'))
    with pytest.raises(ho.ResultFailure):
        ho.committed_payload(layout, value, "c1")


def test_uncommitted_or_partial_results_are_rejected(tmp_path):
    layout, value, attempt = committed_fixture(tmp_path)
    attempt["phase"] = "committing"
    with pytest.raises(ho.ResultFailure) as caught:
        ho.committed_payload(layout, value, "c1")
    assert caught.value.code == "RESULT_NOT_COMMITTED"


def test_session_archive_identity_checked(tmp_path):
    layout = ho.Layout(tmp_path / "run", tmp_path / "ws", "r1", PROTO)
    session = tmp_path / "s.jsonl"
    session.write_text(json.dumps({"type": "session", "id": "other"}) + "\n")
    with pytest.raises(ho.ResultFailure) as caught:
        ho.archive_session(layout, "c1", {"attempt_id": "a1", "session_id": "s1"}, session)
    assert caught.value.code == "SESSION_ARCHIVE_FAILED"


def test_file_inputs_checked_against_recorded_hash(tmp_path):
    p = {"result_profile": "generic", "cartridges": {
        "c1": {"handover": [], "inputs": [], "outputs": ["o.txt"], "retry_safe": True, "dir": "c1"},
        "c2": {"handover": [], "inputs": [{"from": "c1", "path": "o.txt"}], "outputs": [], "retry_safe": False, "dir": "c2"}}}
    layout = ho.Layout(tmp_path / "run", tmp_path / "ws", "r1", p)
    out = layout.work("c1") / "outputs/o.txt"
    out.parent.mkdir(parents=True)
    out.write_text("DATA")
    files, missing = ho.collect_outputs(layout, "c1")
    assert not missing
    value = lg.new("r1", ["c1", "c2"], "result_file_v1")
    value["cartridges"]["c1"]["attempts"] = [{"status": "done", "outputs": files}]
    delivered = ho.prepare_files(layout, value, "c2")
    assert Path(delivered[0]["local"]).read_text() == "DATA" and delivered[0]["verified"]
    out.write_text("CHANGED")
    with pytest.raises(ho.ResultFailure) as caught:
        ho.prepare_files(layout, value, "c2")
    assert caught.value.code == "INPUT_INTEGRITY_FAILED"
    value["cartridges"]["c1"]["attempts"] = [{"status": "failed"}]
    with pytest.raises(ho.ResultFailure):
        ho.prepare_files(layout, value, "c2")


def test_candidates_snapshot_never_verified(tmp_path):
    p = {"cartridges": {"c1": {"handover": [], "inputs": [], "outputs": ["o.txt", "missing.txt"], "retry_safe": True, "dir": "c1"}}}
    layout = ho.Layout(tmp_path / "run", tmp_path / "ws", "r1", p)
    (layout.work("c1") / "outputs").mkdir(parents=True)
    (layout.work("c1") / "outputs/o.txt").write_text("partial")
    found = ho.snapshot_candidates(layout, "c1", {"attempt_id": "a1"})
    assert found == [{"path": "o.txt", "bytes": 7, "sha256": ho.sha256_bytes(b"partial"), "verified": False}]


# ------------------------------------------------------------------ manifests
def write(tmp_path, manifest, prompts):
    for name, text in prompts.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    (tmp_path / "magazine.yaml").write_text(textwrap.dedent(manifest))
    return tmp_path


def test_plain_v1_manifest_unchanged_for_core(tmp_path):
    write(tmp_path, """
        schema_version: "1.0"
        name: Simple
        cartridges:
          - {id: first, model: concise, prompt_file: p/1.md}
        """, {"p/1.md": "Exact prompt\n  with spacing  \n"})
    doc, protocol, name = proto.parse_manifest(tmp_path)
    assert doc == {"schema_version": "1.0", "cartridges": [{"id": "first", "job_type": "concise", "prompt": "Exact prompt\n  with spacing  \n"}]}
    assert name == "Simple" and protocol["extended_manifest"] is False


def test_extended_manifest_declarations(tmp_path):
    write(tmp_path, """
        name: Ext
        handover_mode: result_file_v1
        result_profile: magazine.task.v1
        recovery: {max_retries: 2, seconds: 900}
        loop_police: {enabled: false}
        cartridges:
          - id: c01
            model: writer
            prompt_file: c/1.md
            artifacts: {outputs: [data/x.csv]}
          - id: c02
            model: writer
            prompt: "Use it"
            handover: [{from: c01, select: /result/note, as: note}]
            artifacts: {inputs: [{from: c01, path: data/x.csv}]}
            constraints: ignored
        """, {"c/1.md": "Make x"})
    doc, protocol, _ = proto.parse_manifest(tmp_path)
    assert [c["id"] for c in doc["cartridges"]] == ["c01", "c02"]
    assert protocol["recovery"]["max_attempts"] == 3 and protocol["recovery"]["attempt_seconds"] == 900
    assert protocol["loop_police"]["enabled"] is False
    assert protocol["cartridges"]["c01"]["retry_safe"] is True and protocol["cartridges"]["c02"]["retry_safe"] is False
    assert protocol["ignored_fields"] == ["c02.constraints"]


@pytest.mark.parametrize("manifest", [
    "cartridges: [{id: a, model: m, prompt: x, surprise: 1}]",
    "extra: 1\ncartridges: [{id: a, model: m, prompt: x}]",
    "cartridges: [{id: a, model: m, prompt: x, handover: [{from: b, select: /x}]}]",
    "cartridges: [{id: a, model: m, prompt: x}, {id: a, model: m, prompt: y}]",
    "cartridges: [{id: a, model: m, prompt: x, artifacts: {outputs: [../escape]}}]",
    "cartridges: [{id: a, model: m, prompt: x, artifacts: {outputs: [/abs]}}]",
    "handover_mode: weird\ncartridges: [{id: a, model: m, prompt: x}]",
    "handover_mode: legacy_chat\ncartridges: [{id: a, model: m, prompt: x, artifacts: {outputs: [o]}}]",
    "cartridges: [{id: a, model: m, prompt: x}]\ncartridges: []",
    "cartridges: [{id: a, model: m, prompt_file: ../x.md}]"])
def test_manifest_rejections(tmp_path, manifest):
    write(tmp_path, manifest, {})
    from magazine.schema import MagazineSchemaError
    with pytest.raises((MagazineSchemaError, ValueError, FileNotFoundError)):
        proto.parse_manifest(tmp_path)


def test_unsafe_ids_get_safe_directories():
    assert proto.cartridge_dir_name("c01") == "c01"
    name = proto.cartridge_dir_name("../evil id")
    assert name.startswith("cid-") and "/" not in name


# ------------------------------------------------------------- prompt delivery
def test_attempt_prompt_preserves_exact_prompt_prefix(tmp_path):
    layout = ho.Layout(tmp_path, tmp_path / "ws", "r1", PROTO)
    for prompt in ("Exact\n  prompt  ", "Trailing newline\n"):
        text = ex.attempt_prompt(prompt, {"attempt_id": "a" * 32, "kind": "work"}, PROTO, "c2", layout,
                                 [("answer", "c1", 42)], [])
        assert text.startswith(prompt) and "answer (from c1)" in text and "42" in text
        assert proto.DONE_INSTRUCTION in text and "magazine_publish_result" in text
        assert "put the full deliverable text in note" in text


def test_effective_timeout_respects_smaller_explicit_limit():
    p = {"recovery": proto.recovery_policy()}
    assert ex.effective_timeout(p, {}) == 1800
    assert ex.effective_timeout(p, {"timeoutSeconds": 1200}) == 1200
    assert ex.effective_timeout(p, {"timeoutSeconds": 7200}) == 1800


def test_worker_error_classification():
    assert ex.classify_worker_error("Worker changed assigned model").recovery == "hard"
    assert ex.classify_worker_error("Saved Pi session prompt mismatch").code == "WORKER_INTEGRITY"
    assert ex.classify_worker_error("Pi worker exited without a result").recovery == "repair"


# ---------------------------------------------------------------- reconcile
def test_restart_reconcile_marks_running_attempt_interrupted_without_resuming(tmp_path):
    value = lg.new("r1", ["c1"], "result_file_v1")
    value["cartridges"]["c1"]["attempts"] = [{"attempt_number": 1, "status": "running", "attempt_id": "a"}]
    lg.save(tmp_path, value)
    assert ex.reconcile(tmp_path, True) is None
    after = ex.reconcile(tmp_path, False)
    attempt = after["cartridges"]["c1"]["attempts"][0]
    assert attempt["status"] == "interrupted" and attempt["retry_decision"]["decision"] == "stop"
    assert attempt["tokens"]["state"] == "unavailable"


# --------------------------------------------------------- compatibility (node)
def node(script):
    return subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30, cwd=ROOT)


def test_compatibility_pairs_and_diagnostics():
    out = node(f"""
      import {{checkCompatibility}} from '{ROOT}/magazine-pi/index.mjs';
      import fs from 'node:fs';
      const t=JSON.parse(fs.readFileSync('{ROOT}/magazine-pi/compatibility.json'));
      const r=[];
      for (const [h,s] of [['0.96.1','1.0.2'],['0.115.0','1.1.0'],['0.120.0','1.5.2']]) r.push(checkCompatibility(h,s,t).level);
      for (const [h,s,o] of [['0.96.1','1.1.0'],['0.121.0','1.1.0'],[undefined,'1.0.2'],['0.121.0','1.1.0',{{host:'0.121.0',sdk:'1.0.2'}}]])
        try {{ checkCompatibility(h,s,t,o); r.push('ACCEPTED'); }} catch (e) {{ r.push(e.message.includes('Nothing was changed') ? 'REFUSED' : e.message); }}
      r.push(checkCompatibility('0.121.0','1.1.0',t,{{host:'0.121.0',sdk:'1.1.0'}}).level);
      console.log(JSON.stringify(r));""")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["accepted", "offline-verified", "offline-verified",
                                      "REFUSED", "REFUSED", "REFUSED", "REFUSED", "unverified-operator-override"]


def test_worker_refuses_unverified_sdk_and_bad_tool_names(tmp_path):
    fake = tmp_path / "sdk"
    fake.mkdir()
    (fake / "package.json").write_text(json.dumps({"name": "@earendil-works/pi-coding-agent", "version": "9.9.9", "main": "x.js"}))
    out = node(f"""
      import {{loadSdk, strictJson, reportsNoChange}} from '{ROOT}/magazine-pi/worker.mjs';
      const r=[];
      process.env.MAGAZINE_PI_SDK='{fake}';
      try {{ await loadSdk({{}}); r.push('LOADED'); }} catch (e) {{ r.push(e.message); }}
      try {{ await loadSdk({{sdkVersion:'1.0.2'}}); r.push('LOADED'); }} catch (e) {{ r.push(e.message); }}
      try {{ strictJson('{{"a":1,"a":2}}'); r.push('OK'); }} catch (e) {{ r.push('DUP'); }}
      try {{ strictJson('{{"a":1}} x'); r.push('OK'); }} catch (e) {{ r.push('TRAIL'); }}
      r.push(JSON.stringify(strictJson('{{"__proto__":{{"x":1}},"b":[1,2.5e3,null,true]}}')));
      r.push(reportsNoChange({{details:{{changed:false}}}}), reportsNoChange({{content:[{{text:'{{"changed":true}}'}}]}}));
      process.stdout.write(JSON.stringify(r));  // worker.mjs redirects console.log to stderr""")
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert "has not verified Pi SDK 9.9.9" in r[0] and "verified with 1.0.2" in r[1]
    assert r[2:4] == ["DUP", "TRAIL"] and json.loads(r[4]) == {"__proto__": {"x": 1}, "b": [1, 2500, None, True]}
    assert r[5:] == [True, False]


# ------------------------------------------------- browser/server module guard
@pytest.mark.parametrize("name", ["client/entry.mjs", "index.mjs", "worker.mjs"])
def test_modules_parse(name):
    out = subprocess.run(["node", "--check", str(ROOT / "magazine-pi" / name)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr


def test_client_bundle_is_import_free_and_exports_mount():
    source = (ROOT / "magazine-pi/client/entry.mjs").read_text()
    import re
    assert not re.search(r"^\s*import\s", source, re.M) and "import(" not in source
    assert not re.search(r"\bnode:|\brequire\(", source)
    out = node(f"""import m from '{ROOT}/magazine-pi/client/entry.mjs';
      import {{chooseRun, tokenLine, queueText, duration}} from '{ROOT}/magazine-pi/client/entry.mjs';
      const done={{run_id:'a',status:'COMPLETED',display_status:'COMPLETED'}}, run={{run_id:'b',status:'RUNNING',display_status:'RUNNING',active:true}},
            ready={{run_id:'c',status:'READY',display_status:'READY'}}, queued={{run_id:'d',status:'READY',display_status:'QUEUED'}};
      const all=[done,run,ready,queued];
      console.log(JSON.stringify([typeof m.mount,
        chooseRun(all,'d'),            // remembered and queued: keep it
        chooseRun(all,'c'),            // remembered but idle while another runs: the running one
        chooseRun([done,ready],'c'),   // nothing running: the remembered run, any state
        chooseRun(all,'gone'),         // remembered gone: a running run
        chooseRun([done,ready],'gone'),// remembered gone and nothing running: no invented choice
        chooseRun([],''),
        tokenLine(null), tokenLine({{available:false,attempts:2}}), tokenLine({{available:true,input:10,output:2,total:12,incomplete:true,unavailable_attempts:1}}),
        queueText({{queued:true,position:2,next:true}}), queueText({{recovery_required:true,queued:true,position:1}}), queueText({{holder:true}}),
        duration(65,false), duration(600,true)]));""")
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r[:7] == ["function", "d", "b", "c", "b", "", ""]
    assert r[7] == "Tokens: unavailable" and "2 attempts reported no usage" in r[8]
    assert "10 in / 2 out / 12 total" in r[9] and "incomplete" in r[9]
    assert r[10:] == ["Next (#2)", "Recovery required", "Executing", "65 s", "10 min · active"]


def test_token_counter_and_aggregate():
    out = node(f"""import {{tokenCounter, sumTokens}} from '{ROOT}/magazine-pi/client/entry.mjs';
      const a={{available:true,input:1200,output:300,total:1500,cache_read:50,cache_write:0,incomplete:false}};
      const b={{available:true,input:10,output:2,total:12,incomplete:true,unavailable_attempts:1}};
      const u={{available:false,attempts:1}};
      console.log(JSON.stringify([tokenCounter(null,'READY'), tokenCounter(u,'RUNNING'), tokenCounter(u,'HALTED'),
        tokenCounter(a,'RUNNING'), tokenCounter(b,'COMPLETED'), sumTokens([a,b,u,null]), sumTokens([])]));""")
    assert out.returncode == 0, out.stderr
    none, wait, unrep, live, inc, agg, empty = json.loads(out.stdout)
    assert none["total"] == "—" and none["note"] == "No tokens reported yet." and none["cache"] == ""
    assert wait["note"].startswith("Waiting for reported usage")
    assert "1 attempt reported no usage" in unrep["note"]
    assert live["input"] == "1,200" and live["output"] == "300" and live["total"] == "1,500"
    assert live["note"].startswith("Live") and live["cache"] == "Cache read 50 · cache write 0"
    assert "Incomplete: 1 attempt without reported usage" in inc["note"]
    assert agg["input"] == 1210 and agg["output"] == 302 and agg["total"] == 1512 and agg["cache_read"] == 50
    assert agg["projects"] == 4 and agg["unreported"] == 2 and agg["incomplete"] and agg["available"]
    assert empty["available"] is False and empty["total"] is None and empty["projects"] == 0


def test_handover_text_uses_committed_text_and_preserves_full_deliverable():
    out = node(f"""import {{handoverText}} from '{ROOT}/magazine-pi/client/entry.mjs';
      console.log(JSON.stringify([null, {{}}, {{result:'A sentence.'}},
        {{result:{{note:'First line.\\nSecond line.'}}}}, {{result:{{note:'  '}}}},
        {{result:{{note:42}}}}, {{result:7}}, {{other:'not a result'}}].map(handoverText)));""")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ['', '', 'A sentence.', 'First line.\nSecond line.', '', '', '', '']


def test_declared_models_shared_mixed_missing_and_unavailable():
    out = node(f"""import {{declaredModels}} from '{ROOT}/magazine-pi/client/entry.mjs';
      const available=[{{id:'fixture/a'}},{{id:'fixture/b'}}];
      const cases=[[], [{{id:'a',job_type:'fixture/a'}},{{id:'b',model:'fixture/a'}}],
        [{{id:'a',job_type:'fixture/a'}},{{id:'b',job_type:'fixture/b'}}],
        [{{id:'a',job_type:'missing'}},{{id:'b',model:'fixture/b'}}],
        [{{id:'a'}},{{id:'b',model:'fixture/a'}}], [{{id:'a',model:'missing'}}]];
      console.log(JSON.stringify(cases.map(c=>declaredModels(c,available))));""")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [
        {'shared':'','perCartridge':{}}, {'shared':'fixture/a','perCartridge':{}},
        {'shared':'','perCartridge':{'a':'fixture/a','b':'fixture/b'}},
        {'shared':'','perCartridge':{'b':'fixture/b'}},
        {'shared':'','perCartridge':{'b':'fixture/a'}}, {'shared':'','perCartridge':{}}]


def test_token_cell_before_attempts_and_live_counter_note():
    out = node(f"""import {{tokenCell,tokenCounter}} from '{ROOT}/magazine-pi/client/entry.mjs';
      const partial={{available:true,attempts:1,total:12,input:10,output:2,incomplete:true,unavailable_attempts:0}};
      console.log(JSON.stringify([tokenCell(null),tokenCell({{available:false,attempts:0}}),
        tokenCell({{available:false,attempts:1}}),tokenCell(partial),
        tokenCounter(partial,'RUNNING'),tokenCounter(partial,'STOP_REQUESTED'),tokenCounter(partial,'HALTED'),
        tokenCounter({{...partial,unavailable_attempts:1}},'RUNNING')]));""")
    assert out.returncode == 0, out.stderr
    none, ready, missing, cell, live, stopping, halted, incomplete = json.loads(out.stdout)
    assert none == ready == ['—','']
    assert missing == ['unavailable','not reported'] and cell == ['12','incomplete']
    assert 'Incomplete' not in live['note'] and 'Incomplete' not in stopping['note']
    assert 'Incomplete' in halted['note']
    assert 'Incomplete: 1 attempt without reported usage' in incomplete['note']


def test_individual_ledger_choice_preserves_any_of_200_cartridges():
    module = (ROOT / 'magazine-pi/client/entry.mjs').as_uri()
    out = node(f"""import {{ledgerChoice}} from '{module}';
      const rows=Array.from({{length:200}},(_,i)=>({{id:'c'+i,status:i===50?'failed':i===99?'running':'pending'}}));
      console.log(JSON.stringify([ledgerChoice(rows,'c0','c99'),ledgerChoice(rows,'c199','c99'),
        ledgerChoice(rows,'c100','c99'),ledgerChoice(rows,'missing','c99'),ledgerChoice(rows,'',''),ledgerChoice([],'','')]));""")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ['c0', 'c199', 'c100', 'c99', 'c50', '']
