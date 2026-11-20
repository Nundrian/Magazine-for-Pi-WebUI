"""Isolated update rehearsal on a real Pi Web UI host. No model.

Installs an older plugin ZIP with the host CLI, starts the host, then runs the two
in-container update scripts (update-remove.sh, install.sh) exactly as shipped and
checks that only magazine-pi is unloaded and reloaded with the new version, through
ordinary client connections (no Plugins -> Reload, no restart). A second plugin is
installed alongside to confirm it stays loaded.

Usage: python scripts/real_host_update.py --host-root <prefix> --old <old-plugin.zip>
       --new <new-plugin.zip> --install-dir <dir with install.sh and update-remove.sh>
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from real_host_smoke import Host  # noqa: E402
from pi6_fakes import ScriptedProvider, agent_dir  # noqa: E402

PROBE = r"""
const [dir,port]=process.argv.slice(1);const {WebSocket}=require(require.resolve('ws',{paths:[dir]}));
const ws=new WebSocket('ws://127.0.0.1:'+port+'/ws');const t=setTimeout(()=>{console.log('{"error":"timeout"}');process.exit(1)},30000);
ws.on('open',()=>ws.send(JSON.stringify({type:'hello',clientId:'update-probe-'+Date.now()})));
ws.on('message',raw=>{const v=JSON.parse(raw);if(v.type!=='plugins')return;
 console.log(JSON.stringify({epoch:v.epoch,plugins:v.plugins.map(p=>({id:p.id,version:p.version,active:p.active,error:p.error||null}))}));clearTimeout(t);ws.close();});
"""


def probe(host_root, port):
    out = subprocess.run(["node", "-e", PROBE, str(Path(host_root) / "node_modules/pi-web-ui"), str(port)],
                         capture_output=True, text=True, timeout=60)
    return json.loads(out.stdout.strip().splitlines()[-1])


def catalogue_version(port):
    req = Request(f"http://127.0.0.1:{port}/plugins-api/magazine-pi/rpc", data=json.dumps({"action": "catalogue", "data": {}}).encode(),
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=60) as r:
        return json.loads(r.read())["result"]["magazine_version"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host-root", required=True)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--install-dir", required=True)
    ap.add_argument("--port", type=int, default=18950)
    ap.add_argument("--report")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="mag-update-"))
    provider = ScriptedProvider()
    agent = agent_dir(tmp / "agent", provider.url)
    cwd = tmp / "workspace"
    cwd.mkdir()
    data = tmp / "data"
    bindir = tmp / "bin"
    bindir.mkdir()
    (bindir / "pi-web-ui").symlink_to(Path(args.host_root) / "node_modules/.bin/pi-web-ui")
    env = {**os.environ, "PI_WEB_DATA_DIR": str(data), "MAGAZINE_PYTHON": sys.executable,
           "PATH": f"{bindir}:{os.environ['PATH']}"}
    old = tmp / "old"
    zipfile.ZipFile(args.old).extractall(old)
    subprocess.run(["pi-web-ui", "install", str(old / "magazine-pi"), "--name", "magazine-pi", "--no-build", "--data-dir", str(data)],
                   env=env, check=True, capture_output=True)
    other = tmp / "other"  # a second, unrelated plugin that must stay loaded
    (other / "client").mkdir(parents=True)
    (other / "manifest.json").write_text(json.dumps({"id": "other-plugin", "name": "Other", "version": "1.0.0", "description": "fixture",
                                                    "apiVersion": 2, "client": {"entry": "client/entry.mjs"}}))
    (other / "client/entry.mjs").write_text("export default {mount(el){el.textContent='other'}};\n")
    (other / "index.mjs").write_text("export async function activate(host){host.log('info','other active');}\n")
    subprocess.run(["pi-web-ui", "install", str(other), "--name", "other-plugin", "--no-build", "--data-dir", str(data)],
                   env=env, check=True, capture_output=True)
    (data / "magazine").mkdir(parents=True, exist_ok=True)
    (data / "magazine" / "keep.txt").write_text("run data must survive\n")
    log = (tmp / "host.log").open("wb")
    host = Host(args.host_root, data, agent, cwd, args.port, log)
    report = {"checks": {}, "provider": "none used"}
    c = report["checks"]
    try:
        host.start()
        before = probe(args.host_root, args.port)
        mp = {p["id"]: p for p in before["plugins"]}
        c["old_active"] = bool(mp.get("magazine-pi", {}).get("active")) and not mp["magazine-pi"]["error"]
        c["old_version_rpc"] = catalogue_version(args.port)
        c["other_active_before"] = bool(mp.get("other-plugin", {}).get("active"))
        r1 = subprocess.run(["sh", str(Path(args.install_dir) / "update-remove.sh")], env=env, capture_output=True, text=True)
        c["remove_exit"] = r1.returncode
        c["backup_made"] = any((data / "install-backups").glob("magazine-pi-*/plugin/manifest.json"))
        mid = probe(args.host_root, args.port)  # an ordinary client connection
        ids = {p["id"]: p for p in mid["plugins"]}
        c["unloaded_after_connection"] = "magazine-pi" not in ids
        c["other_still_active"] = bool(ids.get("other-plugin", {}).get("active"))
        shutil.copy(args.new, "/tmp/magazine-pi7.zip")
        r2 = subprocess.run(["sh", str(Path(args.install_dir) / "install.sh")], env=env, capture_output=True, text=True)
        c["install_exit"] = r2.returncode
        report["logs"] = {"remove": r1.stdout[-1500:] + r1.stderr[-500:], "install": r2.stdout[-1500:] + r2.stderr[-500:]}
        leftover = data / "plugins" / "magazine-pi"
        report["leftover_before_install"] = sorted(str(x.relative_to(leftover)) for x in leftover.rglob("*"))[:20] if leftover.exists() else None
        after = probe(args.host_root, args.port)
        ap_ = {p["id"]: p for p in after["plugins"]}
        c["new_active"] = bool(ap_.get("magazine-pi", {}).get("active")) and not ap_["magazine-pi"]["error"]
        c["new_version_listed"] = ap_.get("magazine-pi", {}).get("version")
        c["new_version_rpc"] = catalogue_version(args.port)
        c["other_active_after"] = bool(ap_.get("other-plugin", {}).get("active"))
        c["run_data_kept"] = (data / "magazine" / "keep.txt").read_text() == "run data must survive\n"
        c["host_pid_unchanged"] = host.process.poll() is None
        report["logs"] = {"remove": r1.stdout[-1500:] + r1.stderr[-500:], "install": r2.stdout[-1500:] + r2.stderr[-500:]}
        new_version = json.loads(zipfile.ZipFile(args.new).read("magazine-pi/manifest.json"))["version"]
        ok = (c["old_active"] and c["remove_exit"] == 0 and c["backup_made"] and c["unloaded_after_connection"]
              and c["other_still_active"] and c["install_exit"] == 0 and c["new_active"] and c["new_version_rpc"] == new_version
              and c["new_version_listed"] == new_version and c["other_active_after"] and c["run_data_kept"] and c["host_pid_unchanged"])
        report["result"] = "PASS" if ok else "FAIL"
    except Exception as error:  # noqa: BLE001
        report["result"] = "FAIL"
        report["error"] = repr(error)[:1500]
    finally:
        host.stop()
        log.close()
    text = json.dumps(report, indent=2)
    if args.report:
        Path(args.report).write_text(text + "\n")
    print(text)
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
