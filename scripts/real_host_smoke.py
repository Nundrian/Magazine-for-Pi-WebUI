"""Isolated real Pi Web UI host + packaged plugin smoke check. No model.

Usage: python scripts/real_host_smoke.py --host-root <npm prefix with node_modules/pi-web-ui>
       [--plugin <dir or zip>] [--port 18787] [--report out.json]

Starts the real host CLI with throwaway data/agent/workspace directories,
loopback binding, no catalog sync, provider credentials removed, and a
scripted loopback provider. Checks native plugin discovery, static client
asset, non-inference routes, one fake-provider run, plugin reload and host
restart persistence. It never touches another Pi installation.
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from pi6_fakes import ScriptedProvider, agent_dir, project_zip  # noqa: E402

SCRUB = re.compile(r"^(AWS|ANTHROPIC|OPENAI|AZURE|GOOGLE|GEMINI|VERTEX|BEDROCK|MISTRAL|GROQ|XAI|OPENROUTER|DEEPSEEK|OLLAMA|PI_WEB_TOKEN)")

WS_PROBE = r"""
const {createRequire}=require('module');
const [hostDir,port,mode]=process.argv.slice(1);
const {WebSocket}=createRequire(hostDir+'/package.json')('ws');
const ws=new WebSocket(`ws://127.0.0.1:${port}/ws`);
const timer=setTimeout(()=>{console.log(JSON.stringify({error:'timeout'}));process.exit(1);},30000);
let reloaded=false;
ws.on('open',()=>ws.send(JSON.stringify({type:'hello',clientId:'magazine-smoke-'+mode})));
ws.on('message',raw=>{const v=JSON.parse(raw);if(v.type!=='plugins')return;
  if(mode==='reload'&&!reloaded){reloaded=true;ws.send(JSON.stringify({type:'plugins_reload'}));return;}
  console.log(JSON.stringify({epoch:v.epoch,plugin:v.plugins.find(p=>p.id==='magazine-pi')||null}));clearTimeout(timer);ws.close();});
ws.on('error',e=>{console.log(JSON.stringify({error:e.message}));process.exit(1);});
"""


class Host:
    def __init__(self, host_root, data, agent, cwd, port, log):
        self.cmd = [str(Path(host_root) / "node_modules/.bin/pi-web-ui"), "--no-browser"]
        self.env = {k: v for k, v in os.environ.items() if not SCRUB.match(k)}
        self.env.update({"PI_WEB_DATA_DIR": str(data), "PI_CODING_AGENT_DIR": str(agent), "PI_WEB_HOST": "127.0.0.1",
                         "PI_WEB_PORT": str(port), "PI_WEB_CWD": str(cwd), "PI_WEB_LOCALE": "en",
                         "PI_WEB_PLUGIN_CATALOG_URL": "off", "PI_OFFLINE": "1", "PI_TELEMETRY": "0",
                         "PYTHONDONTWRITEBYTECODE": "1"})
        self.cwd, self.port, self.log = cwd, port, log
        self.process = None

    def start(self):
        self.process = subprocess.Popen(self.cmd, env=self.env, cwd=self.cwd, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(300):
            if self.process.poll() is not None:
                raise RuntimeError("host exited; see log")
            try:
                with urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=1) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.1)
        raise TimeoutError("host not ready")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=20)


def rpc(port, action, data=None, expected=200):
    req = Request(f"http://127.0.0.1:{port}/plugins-api/magazine-pi/rpc", data=json.dumps({"action": action, "data": data or {}}).encode(),
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=70) as r:
            code, raw = r.status, r.read()
    except HTTPError as e:
        code, raw = e.code, e.read()
    assert code == expected, (action, code, raw[:400])
    return json.loads(raw)


def probe(host_root, port, mode="hello"):
    out = subprocess.run(["node", "-e", WS_PROBE, str(Path(host_root) / "node_modules/pi-web-ui"), str(port), mode],
                         capture_output=True, text=True, timeout=60)
    return json.loads(out.stdout.strip().splitlines()[-1])


def install(plugin, data, config):
    target = data / "plugins/magazine-pi"
    if plugin.suffix == ".zip":
        with zipfile.ZipFile(plugin) as z:
            z.extractall(data / "plugins")
    else:
        shutil.copytree(plugin, target, ignore=shutil.ignore_patterns("__pycache__"))
    (target / "config.json").write_text(json.dumps(config))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-root", required=True)
    parser.add_argument("--plugin", default=str(ROOT / "magazine-pi"))
    parser.add_argument("--port", type=int, default=18787)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--report")
    args = parser.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="mag-real-host-"))
    provider = ScriptedProvider()
    agent = agent_dir(tmp / "agent", provider.url)
    cwd = tmp / "workspace"
    cwd.mkdir()
    data = tmp / "data"
    install(Path(args.plugin), data, {"workspace": str(cwd / "magazine"), "agentDir": str(agent), "python": args.python,
                                      "allowedTools": ["read", "ls"]})
    log = (tmp / "host.log").open("wb")
    host = Host(args.host_root, data, agent, cwd, args.port, log)
    report = {"host_root": args.host_root, "checks": {}, "provider": "scripted loopback fixture; no model"}
    checks = report["checks"]
    try:
        host.start()
        version = json.loads((Path(args.host_root) / "node_modules/pi-web-ui/package.json").read_text())["version"]
        report["pi_web_ui"] = version
        found = probe(args.host_root, args.port)
        assert found.get("plugin") and not found["plugin"].get("error"), found
        checks["native_discovery"] = {"active": found["plugin"].get("active"), "version": found["plugin"].get("version")}
        with urlopen(f"http://127.0.0.1:{args.port}/plugins/magazine-pi/client/entry.mjs", timeout=10) as r:
            body = r.read().decode()
            assert r.status == 200 and "export default" in body
            assert not re.search(r"^\s*import\s", body, re.M), "client bundle must not import modules"
        checks["client_asset"] = "served, import-free"
        for name in ("manifest.json", "index.mjs", "config.json", "service.py"):
            try:
                with urlopen(f"http://127.0.0.1:{args.port}/plugins/magazine-pi/{name}", timeout=10) as r:
                    leaked = b"magazine" in r.read().lower() and name != "manifest.json"
            except HTTPError:
                leaked = False
            assert not leaked, f"{name} must not be served"
        checks["server_files_not_served"] = True
        cat = rpc(args.port, "catalogue")["result"]
        assert {"fixture/model-a", "fixture/model-b"} <= {m["id"] for m in cat["models"]}
        report["compatibility"] = cat["compatibility"]
        checks["catalogue_without_inference"] = not provider.requests
        raw = base64.b64decode(project_zip("name: Smoke\ncartridges:\n  - {id: a, model: w, prompt_file: a.md}\n  - {id: b, model: w, prompt_file: b.md, handover: [{from: a, select: /answer, as: answer}]}\n",
                                           {"a.md": "SCRIPT value:5\nx", "b.md": "SCRIPT ok\ny"}))
        req = Request(f"http://127.0.0.1:{args.port}/plugins-api/magazine-pi/upload", data=raw,
                      headers={"Content-Type": "application/octet-stream", "X-Magazine-File-Name": "smoke.zip"})
        with urlopen(req, timeout=30) as r:
            imported = json.load(r)
        assert imported["ok"], imported
        preview = rpc(args.port, "preview", {"project_id": imported["result"]["project_id"]})["result"]
        run = rpc(args.port, "prepare", {"magazine": json.dumps(preview["document"]), "project_id": imported["result"]["project_id"],
                                         "default_model": "fixture/model-a", "tool_ids": []})["result"]
        rid = run["run_id"]
        checks["ready_without_inference"] = run["display_status"] == "READY" and not provider.requests
        rpc(args.port, "view_state", {"set": {"run_id": rid}})
        rpc(args.port, "start", {"run_id": rid})
        for _ in range(600):
            status = rpc(args.port, "status", {"run_id": rid})["result"]
            if status["status"] in ("COMPLETED", "HALTED") and not status["active"]:
                break
            time.sleep(0.1)
        assert status["display_status"] == "COMPLETED", status
        checks["fake_provider_run"] = {"status": status["display_status"], "provider_requests": len(provider.requests),
                                       "attempts": [c["attempts"] for c in status["ledger"]["cartridges"]]}
        checks["handover_delivered"] = any("answer (from a)" in json.dumps(r) for r in provider.requests)
        zipped = base64.b64decode(rpc(args.port, "download", {"run_id": rid})["result"]["file_data_b64"])
        checks["results_zip_bytes"] = len(zipped)
        reloaded = probe(args.host_root, args.port, "reload")
        assert reloaded.get("plugin") and not reloaded["plugin"].get("error"), reloaded
        assert rpc(args.port, "status", {"run_id": rid})["result"]["display_status"] == "COMPLETED"
        checks["plugins_reload"] = {"epoch": reloaded.get("epoch"), "run_retained": True}
        host.stop()
        host.start()
        probe(args.host_root, args.port)
        state = rpc(args.port, "view_state")["result"]
        assert state["run_id"] == rid and state["run_exists"]
        assert rpc(args.port, "status", {"run_id": rid})["result"]["display_status"] == "COMPLETED"
        checks["host_restart_persistence"] = True
        checks["requests_after_restart"] = len(provider.requests)
        report["result"] = "PASS"
    except Exception as error:
        report["result"] = "FAIL"
        report["error"] = repr(error)[:2000]
    finally:
        host.stop()
        log.close()
        provider.close()
        report["log_tail"] = (tmp / "host.log").read_text(errors="replace")[-1500:]
        if args.report:
            Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: report.get(k) for k in ("pi_web_ui", "result", "error", "compatibility")}))
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
