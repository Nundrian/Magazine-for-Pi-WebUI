"""Headless Chrome against an isolated real Pi Web UI host. No model.

Checks the native Magazine view: loads without console errors (blank-view
guard), collapsible cartridge list, per-cartridge and bulk model assignment,
Antiloop/protocol controls, Prepare READY without inference, view close and
reopen restoring the selected run without starting anything (browser storage
and, separately, server-side view state), explicit Start, token summary,
progress table, activity feed and ledger.

Usage: python scripts/real_host_ui.py --host-root <prefix> [--port 18900] [--out dir]
Chrome runs with --disable-gpu and software rendering.
"""
import argparse
import base64
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from real_host_smoke import Host, install, probe, rpc  # noqa: E402
from pi6_fakes import ScriptedProvider, agent_dir, project_zip  # noqa: E402

CHROME_ARGS = ["--disable-gpu", "--use-gl=swiftshader", "--disable-dev-shm-usage", "--no-first-run",
               "--disable-background-networking", "--disable-sync", "--disable-component-update", "--no-default-browser-check"]


def open_magazine(page, base):
    page.goto(base)
    page.wait_for_load_state("networkidle")
    tab = page.get_by_text("Magazine", exact=True).filter(visible=True)
    if not tab.count():
        for name in ("Plugins", "More plugin buttons"):
            button = page.get_by_role("button", name=name, exact=False)
            if button.count():
                button.first.click()
                break
        tab = page.get_by_text("Magazine", exact=True).filter(visible=True)
    try:
        tab.first.click(timeout=10000)
        page.locator(".mag").wait_for(timeout=20000)
    except Exception:
        page.screenshot(path="/tmp/ui-debug.png", full_page=True)
        Path("/tmp/ui-debug.txt").write_text(page.evaluate("""()=>[...document.querySelectorAll('button,[role=tab],a')].map(b=>(b.getAttribute('aria-label')||'')+'|'+(b.title||'')+'|'+b.innerText.slice(0,40)).join('\\n')"""))
        raise
    page.wait_for_function("document.querySelector('.mag [data-id=message]').textContent !== 'Reconnecting to saved projects…'", timeout=30000)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-root", required=True)
    parser.add_argument("--plugin", default=str(ROOT / "magazine-pi"))
    parser.add_argument("--port", type=int, default=18900)
    parser.add_argument("--chrome", default="/usr/bin/google-chrome")
    parser.add_argument("--out", default=str(ROOT / "evidence-ui"))
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="mag-ui-"))
    provider = ScriptedProvider()
    provider.usage = {'prompt_tokens': 100, 'completion_tokens': 7, 'total_tokens': 107}  # fixture usage, no model
    agent = agent_dir(tmp / "agent", provider.url)
    cwd = tmp / "workspace"
    cwd.mkdir()
    data = tmp / "data"
    install(Path(args.plugin), data, {"workspace": str(cwd / "magazine"), "agentDir": str(agent), "python": args.python, "allowedTools": ["read"]})
    log = (tmp / "host.log").open("wb")
    host = Host(args.host_root, data, agent, cwd, args.port, log)
    base = f"http://127.0.0.1:{args.port}"
    checks, errors = {}, []
    version = json.loads((Path(args.host_root) / "node_modules/pi-web-ui/package.json").read_text())["version"]
    try:
        host.start()
        probe(args.host_root, args.port)
        raw = base64.b64decode(project_zip(
            "name: UI check\ncartridges:\n  - {id: a, model: fixture/model-a, prompt_file: a.md}\n  - {id: b, model: fixture/model-a, prompt_file: b.md}\n"
            "  - {id: c, model: fixture/model-a, prompt_file: c.md, handover: [{from: b, select: /answer, as: answer}]}\n",
            {"a.md": "SCRIPT ok\nFirst", "b.md": "SCRIPT value:9\nSecond", "c.md": "SCRIPT ok\nThird"}))
        with urlopen(Request(base + "/plugins-api/magazine-pi/upload", data=raw, headers={
                "Content-Type": "application/octet-stream", "X-Magazine-File-Name": "ui-check.zip"}), timeout=30) as r:
            project = json.load(r)["result"]["project_id"]
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=args.chrome, headless=True, args=CHROME_ARGS)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            open_magazine(page, base)
            checks["view_loaded"] = page.locator(".mag h1").inner_text() == "Magazine"
            page.locator("[data-id=projects]").select_option(project)
            checks["dashboard_empty_no_invented_selection"] = page.locator("[data-id=dashboard] tr").count() == 0
            checks["dashboard_token_counter_empty"] = page.locator("[data-id=dash-tok-total]").inner_text() == "—" and page.locator("[data-id=token-counter]").is_hidden()
            page.locator("[data-id=cartridges] details").nth(2).wait_for()
            carts = page.locator("[data-id=cartridges] details.cart")
            assert carts.count() == 3
            checks["declared_model_preselected"] = page.locator("[data-id=shared-model]").input_value() == "fixture/model-a" and not page.locator("[data-id=individual]").is_checked()
            checks["cartridges_collapsed_by_default"] = all(carts.nth(i).get_attribute("open") is None for i in range(3))
            page.locator("[data-id=expand]").click()
            checks["expand_all"] = all(carts.nth(i).get_attribute("open") is not None for i in range(3))
            assert "Second" in carts.nth(1).locator("pre").inner_text()
            page.locator("[data-id=collapse]").click()
            page.locator("[data-id=shared-model]").select_option("fixture/model-a")
            page.locator("[data-id=individual]").check()
            page.locator("[data-id=cartridges] input[data-select=b]").check()
            page.locator("[data-id=cartridges] input[data-select=c]").check()
            page.locator("[data-id=bulk-model]").select_option("fixture/model-b")
            page.locator("[data-id=bulk-apply]").click()
            values = page.eval_on_selector_all("[data-id=cartridges] select[data-cartridge]", "s=>s.map(x=>x.value)")
            checks["bulk_assignment"] = values == ["", "fixture/model-b", "fixture/model-b"]
            checks["antiloop_default_on"] = page.locator("[data-id=antiloop]").is_checked()
            checks["protocol_default"] = page.locator("input[name=mag-mode][value=result_file_v1]").is_checked()
            page.screenshot(path=str(out / f"ui-{version}-prepare.png"), full_page=True)
            page.locator("[data-id=prepare]").click()
            page.locator("[data-id=run-summary] .badge.READY").wait_for(timeout=20000)
            selected = lambda pg: pg.locator("[data-id=dashboard] tr[aria-selected=true] button[data-run]").get_attribute("data-run")
            rid = selected(page)
            checks["ready_without_inference"] = not provider.requests
            page.locator(f"[data-id=dashboard] tr:has(button[data-run='{rid}'])").wait_for()
            checks["ready_token_cell_empty"] = page.locator(f"[data-id=dashboard] tr:has(button[data-run='{rid}']) td").nth(5).inner_text().strip() == "—"
            status = rpc(args.port, "status", {"run_id": rid})["result"]
            checks["models_recorded"] = status["cartridge_models"] == {"a": "fixture/model-a", "b": "fixture/model-b", "c": "fixture/model-b"}
            assert page.locator("[data-id=start]").is_enabled() and page.locator("[data-id=stop]").is_disabled()
            checks["dashboard_lists_project"] = page.locator("[data-id=dashboard] tr").count() == 1
            # Close and reopen the view: same run, nothing started.
            page.reload()
            open_magazine(page, base)
            page.locator("[data-id=run-summary] .badge").wait_for()
            checks["reopen_restores_run"] = selected(page) == rid
            checks["reopen_restores_project"] = page.locator("[data-id=projects]").input_value() == project
            time.sleep(2.5)
            checks["reopen_no_inference"] = not provider.requests
            # Scheduler: Round Robin persists; a second project queues behind the first.
            page.locator("[data-id=scheduler-mode]").select_option("round_robin")
            time.sleep(1)
            checks["scheduler_mode_saved"] = rpc(args.port, "scheduler")["result"]["mode"] == "round_robin"
            gate = provider.gate("U")
            second = rpc(args.port, "prepare", {"magazine": json.dumps({"schema_version": "1.0", "cartridges": [
                {"id": "g1", "job_type": "j", "prompt": "SCRIPT gate:U\nGated"}, {"id": "g2", "job_type": "j", "prompt": "SCRIPT ok\nAfter"}]}),
                "model_map": {"j": "fixture/model-a"}, "tool_ids": [], "handover_mode": "result_file_v1"})["result"]["run_id"]
            rpc(args.port, "start", {"run_id": second})
            assert provider.wait_reached("U")
            page.locator("[data-id=start]").click()  # the UI run: queued behind the gated project
            page.locator("[data-id=run-summary] .badge.QUEUED").wait_for(timeout=20000)
            row = page.locator(f"[data-id=dashboard] tr:has(button[data-run='{rid}'])")
            checks["dashboard_shows_queue"] = "Next (#1)" in row.inner_text()
            checks["queued_without_inference"] = not any("SCRIPT ok\nFirst" in json.dumps(r) for r in provider.requests)
            # Restoration priority: remembered idle-looking selection loses to the running project.
            page.locator(f"[data-id=dashboard] button[data-run='{second}']").click()
            time.sleep(0.5)
            rpc(args.port, "view_state", {"set": {"run_id": rid}})
            page.reload()
            open_magazine(page, base)
            page.locator("[data-id=run-summary] .badge").wait_for()
            checks["restore_prefers_remembered_queued"] = selected(page) == rid  # remembered run is queued: kept
            page.screenshot(path=str(out / f"ui-{version}-queue.png"), full_page=True)
            checks["scheduler_mode_after_reload"] = page.locator("[data-id=scheduler-mode]").input_value() == "round_robin"
            gate.set()
            page.locator("[data-id=run-summary] .badge.COMPLETED").wait_for(timeout=90000)
            order = provider.first_prompts()
            checks["round_robin_order"] = order == ["Gated", "First", "After", "Second", "Third"]
            summary = page.locator("[data-id=run-summary]").inner_text()
            checks["token_summary_visible"] = "Tokens:" in summary
            page.wait_for_function("document.querySelector('.mag [data-id=tok-total]').textContent !== '—'", timeout=20000)
            run_total = int(page.locator("[data-id=tok-total]").inner_text().replace(",", ""))
            num = "(id=>Number((document.querySelector('.mag [data-id='+id+']').textContent||'').replace(/[^0-9]/g,''))||0)"
            try:  # the dashboard refreshes on its own poll; allow it one or two cycles
                page.wait_for_function(f"{num}('dash-tok-total') >= {num}('tok-total')", timeout=15000)
            except Exception:
                pass
            dash_total = int(page.locator("[data-id=dash-tok-total]").inner_text().replace(",", ""))
            checks["token_counter_visible"] = page.locator("[data-id=token-counter]").is_visible() and run_total > 0
            checks["token_counter_matches_server"] = run_total == rpc(args.port, "status", {"run_id": rid})["result"]["tokens"]["total"]
            checks["dashboard_token_counter_sums"] = dash_total >= run_total
            checks["ledger_overview_rows"] = page.locator("[data-id=ledger] tbody > tr:not(.detail-row)").count() == 3
            page.locator("[data-id=results-box] > summary").click()
            page.locator("[data-id=results] > details").first.locator(":scope > summary").click()
            checks["saved_results_deliverable"] = page.locator("[data-id=results] > details").first.locator(":scope > pre").first.inner_text() == "ok"
            checks["saved_results_committed_json"] = page.locator("[data-id=results]").get_by_text("Committed handover (JSON)", exact=True).count() == 3
            page.locator("[data-id=results] > details").first.locator("details > summary").click()
            page.locator("[data-id=progress]").click()
            page.wait_for_function("document.querySelector('[data-id=message]').textContent.includes('Status refreshed:')")
            checks["saved_results_remain_expanded"] = page.locator("[data-id=results] > details").first.evaluate("d=>d.open && d.querySelector('details').open")
            page.locator("[data-id=ledger] button[aria-expanded=false]").first.click()
            page.locator("[data-id=ledger] .attempt").first.wait_for(timeout=20000)
            checks["ledger_expands_attempts"] = page.locator("[data-id=ledger] button[aria-expanded=true]").count() == 1
            page.locator("[data-id=activity-box] summary").click()
            checks["activity_items"] = page.locator("[data-id=activity] li").count() >= 3
            page.screenshot(path=str(out / f"ui-{version}-completed.png"), full_page=True)
            page.locator("[data-id=token-counter]").screenshot(path=str(out / f"ui-{version}-token-counter.png"))
            page.locator("[data-id=dash-tokens]").screenshot(path=str(out / f"ui-{version}-dashboard-tokens.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            time.sleep(0.5)
            checks["mobile_no_horizontal_overflow"] = page.locator(".mag").evaluate("e=>e.scrollWidth<=e.clientWidth+1")
            page.screenshot(path=str(out / f"ui-{version}-mobile.png"), full_page=True)
            # A fresh browser profile (no local storage) restores from server-side view state.
            fresh = browser.new_context(viewport={"width": 1280, "height": 900})
            other = fresh.new_page()
            other.on("pageerror", lambda e: errors.append(str(e)))
            open_magazine(other, base)
            other.locator("[data-id=run-summary] .badge.COMPLETED").wait_for(timeout=20000)
            checks["server_view_state_restores_run"] = selected(other) == rid
            browser.close()
        checks["console_errors"] = errors
        result = "PASS" if all(v is True for k, v in checks.items() if k != "console_errors") and not errors else "FAIL"
    except Exception as error:
        result = "FAIL"
        checks["error"] = repr(error)[:1500]
    finally:
        host.stop()
        log.close()
        provider.close()
        tail = (tmp / "host.log").read_text(errors="replace")[-1000:]
        shutil.rmtree(tmp, ignore_errors=True)
    report = {"pi_web_ui": version, "result": result, "checks": checks, "browser": "Chrome headless, --disable-gpu, swiftshader",
              "provider": "scripted loopback fixture; no model", "host_log_tail": tail if result != "PASS" else None}
    (out / f"ui-{version}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"pi_web_ui": version, "result": result, **({"error": checks.get("error")} if result != "PASS" else {})}))
    sys.exit(0 if result == "PASS" else 1)


if __name__ == "__main__":
    main()
