"""pi.10 end to end: real Pi SDK + real Magazine service + sandboxed worker + scripted provider + a fake
Unreal Connector on loopback. No model, GPU, Unreal or Windows service is used.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_pi6_integration import attempts, import_project, prepare_project, run_to_end
from test_pi9_reliability import make_env, run_dir  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(not os.environ.get("MAGAZINE_PI_SDK"), reason="needs MAGAZINE_PI_SDK (real SDK tree)")
UC_KEY = "uehl_" + "K" * 40


class FakeConnector:
    def __init__(self, projects=("nebulaswamp",), running=0):
        self.seen = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                outer.seen.append(("GET", self.path, self.headers.get("Authorization")))
                if self.headers.get("Authorization") != "Bearer " + UC_KEY:
                    return self._reply(401, {"detail": "missing or invalid bearer token"})
                if self.path == "/health":
                    return self._reply(200, {"service": "uehl", "version": "0.2.0", "unreal_processes_running": running})
                if self.path == "/projects":
                    return self._reply(200, {"projects": [{"project_id": p, "access": "write"} for p in projects]})
                if self.path == "/capabilities":
                    return self._reply(200, {"operations": dict.fromkeys(
                        ("health", "discover_projects", "project_status", "submit_change"), {})})
                return self._reply(404, {})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                outer.seen.append(("POST", self.path, self.headers.get("Authorization"), body))
                return self._reply(200, {"project_id": json.loads(body or b"{}").get("project_id"), "job_id": "j-42",
                                         "echo": UC_KEY})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def connect(e, port):
    """Owner-side configuration (what Manage > Host connections writes), pointed at the loopback fixture."""
    path = e.data / "host-services.json"
    path.write_text(json.dumps({"version": 1, "services": {"unreal_connector": {
        "host": "127.0.0.1", "port": port, "token": UC_KEY}}}))
    os.chmod(path, 0o600)


MANIFEST = ("schema_version: '1.0'\nname: HostFlow\n"
            "requires: {services: {unreal_connector: {operations: [health, discover_projects, project_status], "
            "projects: [nebulaswamp]}}}\n"
            "cartridges:\n- id: c1\n  model: m\n  prompt_file: c1.md\n")


def test_worker_calls_connector_without_seeing_the_key(make_env):
    e = make_env()
    uc = FakeConnector()
    try:
        connect(e, uc.port)
        pid = import_project(e.rpc, MANIFEST, {"c1.md": "SCRIPT hostcall:project_status@nebulaswamp\nInspect."})
        rid = prepare_project(e.rpc, pid)
        status = run_to_end(e.rpc, rid)
        assert status["status"] == "COMPLETED", status
        posts = [s for s in uc.seen if s[0] == "POST"]
        assert posts and posts[0][1] == "/project_status" and posts[0][2] == "Bearer " + UC_KEY
        reply = json.dumps(e.provider.host_replies)
        assert "j-42" in reply and UC_KEY not in reply and "[REDACTED]" in reply
        base = run_dir(e, rid)
        sessions = "".join(p.read_text(errors="replace") for p in base.rglob("*.jsonl"))
        assert UC_KEY not in sessions                         # never in any transcript or evidence file
        calls = [json.loads(x) for x in (base / "host-calls" / "c1.jsonl").read_text().splitlines()]
        assert calls[-1]["operation"] == "project_status" and calls[-1]["refs"]["job_id"] == "j-42"
        assert status["dependencies"]["last_check"]["ok"] is True
    finally:
        uc.close()


def test_undeclared_project_is_refused_to_the_model(make_env):
    e = make_env()
    uc = FakeConnector()
    try:
        connect(e, uc.port)
        pid = import_project(e.rpc, MANIFEST, {"c1.md": "SCRIPT hostcall:project_status@production_project\nX."})
        rid = prepare_project(e.rpc, pid)
        run_to_end(e.rpc, rid)
        assert not [s for s in uc.seen if s[0] == "POST"]     # nothing reached the Connector
        assert "declared projects" in json.dumps(e.provider.host_replies)
    finally:
        uc.close()


def test_preflight_refuses_start_before_any_model_call(make_env):
    e = make_env()
    uc = FakeConnector(projects=("uehldisposable",))
    try:
        connect(e, uc.port)
        pid = import_project(e.rpc, MANIFEST, {"c1.md": "SCRIPT hostcall:health\nX."})
        rid = prepare_project(e.rpc, pid)
        refused = e.rpc.call("start", {"run_id": rid}, ok=False)
        assert refused["code"] == 409 and "nebulaswamp" in refused["error"] and "nothing was started" in refused["error"]
        assert not e.provider.requests
        recheck = e.rpc.call("preflight", {"run_id": rid})
        assert recheck["ok"] is False and not e.provider.requests
    finally:
        uc.close()
    connect(e, uc.port)                                    # fixture has stopped listening
    refused = e.rpc.call("start", {"run_id": rid}, ok=False)
    assert refused["code"] == 409 and "not reachable" in refused["error"]


def test_ordinary_workflow_unchanged_and_gets_no_host_tool(make_env):
    e = make_env()
    pid = import_project(e.rpc, "schema_version: '1.0'\nname: Plain\ncartridges:\n- id: c1\n  model: m\n  prompt_file: a.md\n",
                         {"a.md": "SCRIPT hostcall:health\nX."})
    rid = prepare_project(e.rpc, pid)
    run_to_end(e.rpc, rid)
    tools = {t["function"]["name"] for r in e.provider.requests for t in (r.get("tools") or [])}
    assert "magazine_host" not in tools and "magazine_checkpoint" not in tools


def checkpoint_manifest(root, required=True):
    return ("schema_version: '1.0'\nname: Chk\n"
            f"project_checkpoint: {{root: '{root}', required: {str(required).lower()}}}\n"
            "recovery: {max_attempts: 1}\n"
            "cartridges:\n- id: c001\n  model: m\n  prompt_file: a.md\n- id: c002\n  model: m\n  prompt_file: b.md\n")


def test_checkpoint_tool_commits_exact_bytes_and_gates_done(make_env):
    e = make_env()
    root = e.cwd / "Proj" / ".magazine-level-v2"
    root.mkdir(parents=True)
    pid = import_project(e.rpc, checkpoint_manifest(root), {
        "a.md": f"SCRIPT checkpoint:c001\nREPORT_PATH={root}/c001.json\nDo c001.",
        "b.md": "SCRIPT default\nDo c002 but forget the checkpoint."})
    rid = prepare_project(e.rpc, pid, tool_ids=["write"])
    status = run_to_end(e.rpc, rid)
    state = json.loads((root / "state.json").read_text())
    assert state["last_committed_step"] == "c001" and state["committed_by"] == "magazine_checkpoint"
    import hashlib
    assert state["last_report_hash_sha256"] == hashlib.sha256((root / "c001.json").read_bytes()).hexdigest()
    # c002 said DONE without committing: not accepted
    assert status["status"] == "HALTED", status
    assert attempts(e.rpc, rid, "c002")[-1]["code"] == "CHECKPOINT_NOT_COMMITTED"



def test_test_connection_really_calls_the_service(make_env):
    """Live finding: Test connection reported 'Working' without contacting the Connector."""
    e = make_env()
    uc = FakeConnector()
    try:
        connect(e, uc.port)
        r = e.rpc.call("host_test", {"service": "unreal_connector"})
        assert r["ok"] is True and r["checks"] and any(s[1] == "/health" for s in uc.seen)
    finally:
        uc.close()
    r = e.rpc.call("host_test", {"service": "unreal_connector"})   # nothing listening any more
    assert r["ok"] is False and "not reachable" in " ".join(r["problems"])
    (e.data / "host-services.json").write_text(json.dumps({"version": 1, "services": {"unreal_connector": {
        "host": "127.0.0.1", "port": 9, "token": "uehl_" + "W" * 40}}}))
    assert e.rpc.call("host_test", {"service": "unreal_connector"})["ok"] is False
