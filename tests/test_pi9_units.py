"""pi.9 unit checks: sandbox policy construction, error classification, prompt boundary."""
import os

import pytest

import executor as ex
import pi_dispatch as pd
import protocol as proto


def test_readable_complement_excludes_protected_trees_and_links_into_them(tmp_path):
    data = tmp_path / "pi" / "magazine"
    (data / "runs").mkdir(parents=True)
    (tmp_path / "pi" / "plugins").mkdir()
    (tmp_path / "public").mkdir()
    os.symlink(tmp_path / "pi", tmp_path / "alias-to-pi")             # link into a protected tree
    os.symlink(tmp_path, tmp_path / "public" / "alias-to-ancestor")   # link to an ancestor of it
    readable = pd.readable_complement([str(tmp_path / "pi")])
    assert str(tmp_path / "public") in readable
    for path in readable:
        real = os.path.realpath(path)
        assert not real.startswith(str(tmp_path / "pi")), path
        assert real != str(tmp_path), path


def test_policy_refuses_a_workspace_that_contains_magazine_state(tmp_path):
    data = tmp_path / "pi" / "magazine"
    data.mkdir(parents=True)
    d = pd.PiDispatch({"cwd": str(tmp_path / "pi"), "dataDir": str(data), "agentDir": str(tmp_path / "pi" / "agent")},
                      data / "sessions")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(pd.SandboxUnavailable, match="would include Magazine or Pi state"):
        d._policy({}, scratch, [tmp_path / "pi"])


def test_operator_write_paths_cannot_overlap_state(tmp_path):
    data = tmp_path / "pi" / "magazine"
    config = {"dataDir": str(data), "agentDir": str(tmp_path / "pi" / "agent"), "piDataDir": str(tmp_path / "pi"),
              "workerWritePaths": [str(tmp_path / "pi" / "plugins")]}
    with pytest.raises(pd.SandboxUnavailable, match="overlaps"):
        pd.validate_worker_paths(config)
    config["workerWritePaths"] = ["relative/path"]
    with pytest.raises(pd.SandboxUnavailable, match="absolute"):
        pd.validate_worker_paths(config)
    config["workerWritePaths"] = [str(tmp_path / "projects")]
    pd.validate_worker_paths(config)


def test_policy_never_grants_tmp_and_scrubs_host_credentials(tmp_path, monkeypatch):
    data = tmp_path / "pi" / "magazine"
    data.mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    d = pd.PiDispatch({"cwd": str(work), "dataDir": str(data), "agentDir": str(tmp_path / "pi" / "agent")}, data)
    scratch = tmp_path / "scratch"
    (scratch / "home").mkdir(parents=True)
    for sub in ("tmp", "agent"):
        (scratch / sub).mkdir()
    policy = d._policy({}, scratch, [work])
    writes = [g["path"] for g in policy["write"]]
    assert "/tmp" not in writes and "/dev" not in writes and str(work) in writes and policy["deny_unix_sockets"]
    assert all(g["real"] for g in policy["write"]) and policy["protected"]
    tool = [g["path"] for g in policy["tool"]["write"]]
    assert str(scratch / "agent") in writes and str(scratch / "agent") not in tool
    monkeypatch.setenv("PI_WEB_TOKEN", "secret")
    monkeypatch.setenv("PI_WEB_PORT", "8787")
    env = d._env(scratch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret2")
    env = d._env(scratch)
    assert "PI_WEB_TOKEN" not in env and "PI_WEB_PORT" not in env and "TELEGRAM_BOT_TOKEN" not in env
    assert "PATH" in env
    assert env["HOME"] == str(scratch / "home") and env["TMPDIR"] == str(scratch / "tmp")


@pytest.mark.parametrize("message,code,recovery", [
    ("500 llama runner: unknown model architecture: 'ornith'", "MODEL_INCOMPATIBLE", "hard"),
    ("registry.ollama.ai/library/x does not support tools", "MODEL_INCOMPATIBLE", "hard"),
    ("model 'qwen9' not found, try pulling it first", "MODEL_INCOMPATIBLE", "hard"),
    ("model requires more system memory (34 GiB) than is available", "PROVIDER_UNAVAILABLE", "repair"),
    ("Connection error.", "PROVIDER_UNAVAILABLE", "repair"),
    ("fetch failed: connect ECONNREFUSED 127.0.0.1:11434", "PROVIDER_UNAVAILABLE", "repair"),
])
def test_model_failure_classification(message, code, recovery):
    failure = ex.classify_model_error(message)
    assert failure.code == code and failure.recovery == recovery
    assert ex.classify_worker_error(message).code == code


def test_ordinary_failures_stay_recoverable():
    assert ex.classify_model_error("500 internal error: context deadline exceeded") is None
    assert ex.classify_worker_error("something odd").recovery == "repair"


def test_attempt_prompt_states_the_controller_boundary(tmp_path):
    import handover as ho
    protocol = proto.default_protocol({"cartridges": [{"id": "c1", "prompt": "x"}]}, "result_file_v1")
    protocol["cartridges"] = {"c1": {"handover": [], "inputs": [], "outputs": [], "retry_safe": False, "dir": "c1"}}
    layout = ho.Layout(tmp_path, tmp_path, "r" * 12, protocol)
    text = ex.attempt_prompt("Update state.json on DONE.\n", {"attempt_id": "a" * 32, "kind": "work"}, protocol,
                             "c1", layout, [], [])
    assert text.startswith("Update state.json on DONE.\n")
    assert "MAGAZINE CONTROLLER BOUNDARY" in text and "HALT" in text


def test_required_paths_and_windows_hints():
    assert proto.required_paths(None) == []
    with pytest.raises(proto.ProtocolError):
        proto.required_paths(["E:\\3D-Library"])
    with pytest.raises(proto.ProtocolError):
        proto.required_paths(["/projects/../etc"])
    assert proto.check_required_paths(["/definitely/missing"]) == ["/definitely/missing is not available inside the Pi container"]
    doc = {"cartridges": [{"prompt": "Target `E:\\3D-Library\\Projects\\NebulaSwamp`; never guess."}]}
    assert proto.windows_path_hints(doc) == ["E:\\3D-Library\\Projects\\NebulaSwamp"]


def test_policy_refuses_grants_inside_state_except_attempt_folders(tmp_path):
    data = tmp_path / "pi" / "magazine"
    (data / "runs").mkdir(parents=True)
    for workspace, ok in ((data / "runs", False), (tmp_path / "pi" / "plugins", False), (data / "workspace", True)):
        workspace.mkdir(parents=True, exist_ok=True)
        d = pd.PiDispatch({"cwd": str(workspace), "dataDir": str(data), "piDataDir": str(tmp_path / "pi"),
                           "agentDir": str(tmp_path / "pi" / "agent")}, data)
        scratch = data / "temp" / "worker-x"
        for sub in ("home", "tmp", "agent"):
            (scratch / sub).mkdir(parents=True, exist_ok=True)
        if ok:
            d._policy({}, scratch, [workspace, data / "runs" / "r" / "sessions" / "a"])
        else:
            with pytest.raises(pd.SandboxUnavailable):
                d._policy({}, scratch, [workspace])


def test_launcher_refuses_a_grant_swapped_for_a_symlink(tmp_path):
    import json
    import subprocess
    import sys
    protected = tmp_path / "state"
    protected.mkdir()
    (protected / "state.json").write_text("{}")
    grant = tmp_path / "work" / "extra"
    grant.mkdir(parents=True)
    real = os.path.realpath(grant)
    grant.rmdir()
    os.symlink(protected, grant)  # swapped after the parent validated it
    policy = {"read": [], "write": [{"path": str(grant), "real": real, "protected_ok": False}],
              "protected": [str(protected)], "required": [], "tool": {"write": [], "protected": []}}
    (tmp_path / "policy.json").write_text(json.dumps(policy))
    out = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(pd.__file__), "sandbox.py"),
                          "--policy", str(tmp_path / "policy.json"), "--", "true"], capture_output=True, text=True)
    assert out.returncode == 70 and "changed after validation" in out.stdout


def test_seccomp_program_jumps_land_on_return_instructions():
    import sandbox
    program = sandbox._seccomp_program("x86_64")
    for index, (code, jt, jf, k) in enumerate(program):
        if code in (0x15, 0x35):
            for target in (index + 1 + jt, index + 1 + jf):
                assert 0 <= target < len(program)
    assert program[-1] == (0x06, 0, 0, sandbox._RET_ERRNO | 13)


# ---------------------------------------------------------------- pi.9 live fixes
# Found on the installed deployment: Docker Desktop's drvfs/9p bind mounts
# (cache=5) give a directory a fresh inode once nothing references it, so a
# Landlock rule on it stopped matching after the launcher closed the grant's
# descriptor; the worker could not read its own code. The launcher now holds
# every grant descriptor (close-on-exec) for the worker's whole life.

def _children(pid):
    try:
        with open(f"/proc/{pid}/task/{pid}/children") as stream:
            return [int(p) for p in stream.read().split()]
    except OSError:
        return []


def _fd_targets(pid):
    out = set()
    for name in os.listdir(f"/proc/{pid}/fd"):
        try:
            out.add(os.readlink(f"/proc/{pid}/fd/{name}"))
        except OSError:
            pass
    return out


def test_launcher_holds_grant_descriptors_while_the_worker_runs(tmp_path):
    import json
    import subprocess
    import sys
    import time
    import sandbox
    if sandbox.abi_version() < sandbox.MIN_ABI:
        pytest.skip("needs Landlock")
    protected = tmp_path / "state"
    protected.mkdir()
    work = protected / "attempt"  # inside protected state: only the write grant covers it
    work.mkdir()
    real = os.path.realpath(work)
    policy = {"read": pd.readable_complement([str(protected)]),
              "write": [{"path": str(work), "real": real, "protected_ok": True},
                        {"path": "/dev/null", "real": "/dev/null", "protected_ok": False}],
              "protected": [str(protected)], "required": [str(work)], "tool": {"write": [], "protected": []}}
    (tmp_path / "policy.json").write_text(json.dumps(policy))
    launcher = subprocess.Popen([sys.executable, "-I", os.path.join(os.path.dirname(pd.__file__), "sandbox.py"),
                                 "--policy", str(tmp_path / "policy.json"), "--", "sleep", "30"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 10
        while not _children(launcher.pid) and time.time() < deadline:
            time.sleep(0.05)
        (child,) = _children(launcher.pid)
        while os.path.basename(os.readlink(f"/proc/{child}/exe")) != "sleep" and time.time() < deadline:
            time.sleep(0.05)  # between fork and exec the child still holds the pins
        assert os.path.basename(os.readlink(f"/proc/{child}/exe")) == "sleep"
        assert real in _fd_targets(launcher.pid)        # the supervisor pins the write grant
        assert real not in _fd_targets(child)           # ...without leaking it to the worker
    finally:
        launcher.terminate()
        launcher.wait(10)


def test_pinned_descriptors_are_close_on_exec_and_only_for_applied_rules(tmp_path, monkeypatch):
    import fcntl
    import sandbox
    if sandbox.abi_version() < sandbox.MIN_ABI:
        pytest.skip("needs Landlock")
    monkeypatch.setattr(sandbox, "_enforce", lambda fd: None)  # never confine the test process itself
    monkeypatch.setattr(sandbox, "_PINNED", [])
    (tmp_path / "file.txt").write_text("x")
    before = set(os.listdir("/proc/self/fd"))
    # A write grant on a regular file under a write-only (nest) layer carries no
    # applicable right after filtering: no rule, so its descriptor must not stay open.
    monkeypatch.setattr(sandbox, "_FILE_ONLY", 0)
    applied = sandbox._apply([], [str(tmp_path / "file.txt")], True, [], set(), sandbox.abi_version())
    assert applied["write"] == [] and sandbox._PINNED == [] and set(os.listdir("/proc/self/fd")) == before
    monkeypatch.undo()
    monkeypatch.setattr(sandbox, "_enforce", lambda fd: None)
    monkeypatch.setattr(sandbox, "_PINNED", [])
    applied = sandbox._apply([str(tmp_path)], [], False, [], set(), sandbox.abi_version())
    try:
        assert applied["read"] == [str(tmp_path)] and len(sandbox._PINNED) == 1
        fd = sandbox._PINNED[0]
        assert fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
        assert os.readlink(f"/proc/self/fd/{fd}") == os.path.realpath(tmp_path)
    finally:
        for fd in sandbox._PINNED:
            os.close(fd)


def _crashing_node(tmp_path, body):
    script = tmp_path / "fake-node"
    script.write_text("#!/bin/sh\nread request\n" + body + "\nexit 1\n")
    script.chmod(0o755)
    return str(script)


def test_worker_that_dies_before_reporting_says_why(tmp_path):
    import asyncio
    import sandbox
    if sandbox.abi_version() < sandbox.MIN_ABI:
        pytest.skip("needs Landlock")
    data, work, agent = tmp_path / "data", tmp_path / "work", tmp_path / "agent"
    for d in (data, work, agent):
        d.mkdir()
    node = _crashing_node(tmp_path, "echo 'node:fs:560' >&2\n"
                                    "echo \"Error: EACCES: permission denied, open '$1' token=abc123\" >&2\n"
                                    "echo '    at Object.openSync (node:fs:560:18)' >&2\n"
                                    "echo 'Node.js v22.23.5' >&2")
    d = pd.PiDispatch({"cwd": str(work), "dataDir": str(data), "agentDir": str(agent), "node": node,
                       "piDataDir": str(tmp_path / "pi")}, data / "sessions")
    with pytest.raises(pd.WorkerFailure) as info:
        asyncio.run(d.catalogue())
    message = str(info.value)
    assert message.startswith("Pi worker exited without a result: exit code 1; Error: EACCES: permission denied, open ")
    assert "worker.mjs" in message and "abc123" not in message and "[REDACTED]" in message
    assert info.value.reason == "Pi worker exited without a result"
    assert not list((data / "temp").iterdir())  # the private folder, diagnostics included, is discarded


def test_worker_stderr_tail_masks_secrets_and_falls_back_to_the_last_line(tmp_path):
    d = pd.PiDispatch({"cwd": str(tmp_path), "dataDir": str(tmp_path)}, tmp_path)
    d.scratch = tmp_path
    (tmp_path / "stderr.txt").write_text("warming up\nAuthorization: Bearer sk-live-1234 rejected\n")
    assert "sk-live-1234" not in d._stderr_tail() and d._stderr_tail().endswith("rejected")
    (tmp_path / "stderr.txt").write_text('TypeError: bad "apiKey": "k-999" here\n    at x\n')
    assert d._stderr_tail() == 'TypeError: bad "apiKey": "[REDACTED]" here'
    (tmp_path / "stderr.txt").write_text("Error: \x1b[31mred\x1b[0m done\n")
    assert "\x1b" not in d._stderr_tail()
    os.unlink(tmp_path / "stderr.txt")
    os.symlink("/etc/hostname", tmp_path / "stderr.txt")
    assert d._stderr_tail() == ""  # never follows a link
    d.scratch = None
    assert d._stderr_tail() == ""


@pytest.mark.parametrize("line,secret", [
    ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ("Authorization: token ghp_abcdefghij1234567890", "ghp_abcdefghij1234567890"),
    ("Error: connect via https://user:pass@proxy.example/ failed", "user:pass"),
    ('Error: {"password": "correct horse battery"}', "correct horse"),
    ("Error: Incorrect API key provided: sk-proj-abcdef123456", "sk-proj-abcdef123456"),
    ("Error: GET https://x.example/v1?key=AIzaSyA1234567890abcdef failed", "AIzaSyA1234567890abcdef"),
    ("Error: jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4", "eyJhbGciOiJIUzI1"),
])
def test_worker_error_lines_mask_common_credential_forms(line, secret):
    assert secret not in pd.mask_secrets(line)


def test_worker_detail_never_decides_the_failure_classification():
    exc = pd.WorkerFailure("Pi worker exited without a result", "Error: model 'x' not found, try pulling it")
    assert "not found" in str(exc)
    assert ex.classify_worker_error(exc.reason).code == "WORKER_FAILED"


def test_sandbox_raises_its_descriptor_limit_for_many_grants(tmp_path, monkeypatch):
    import resource
    import subprocess
    import sys
    import sandbox
    if sandbox.abi_version() < sandbox.MIN_ABI:
        pytest.skip("needs Landlock")
    grants = []
    for i in range(90):
        (tmp_path / f"g{i}").mkdir()
        grants.append(str(tmp_path / f"g{i}"))
    import json
    policy = {"read": pd.readable_complement([str(tmp_path / "none")]) + grants,
              "write": [{"path": "/dev/null", "real": "/dev/null", "protected_ok": False}],
              "protected": [], "required": [], "tool": {"write": [], "protected": []}}
    (tmp_path / "policy.json").write_text(json.dumps(policy))
    hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    out = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(pd.__file__), "sandbox.py"),
                          "--policy", str(tmp_path / "policy.json"), "--", "true"], capture_output=True, text=True,
                         preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_NOFILE, (64, hard)))
    assert out.returncode == 0, out.stdout
