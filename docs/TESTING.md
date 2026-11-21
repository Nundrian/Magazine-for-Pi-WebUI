# Offline testing — Magazine for Pi

No test uses a model, a GPU or a real provider. All execution goes through a
loopback **scripted fake provider** (`tests/pi6_fakes.py`). It replays fixed
OpenAI-compatible stream scripts selected by a `SCRIPT <name>` first line. Both
`tests/conftest.py` and the host tests delete provider-credential environment
variables (AWS_*, ANTHROPIC_*, OPENAI_*, …) and set `PI_OFFLINE=1`, so no built-in
provider is reachable even by mistake.

For pi.8-ui.3 results, actual deployment migration and separate genuine local
model acceptance, see [the import repair record](IMPORT-REPAIR.md). The tests in
`test_import_publication.py` inject exact syscall, synchronization, cleanup and
interruption failures. `test_dispatch_exit_race.py` verifies process exit between
the return-code check and both cleanup signals. Neither uses a model.

## What is real and what is simulated

| Real | Simulated |
|---|---|
| Pi SDK trees 1.0.2 / 1.1.0 / 1.5.2 (exact npm trees; 1.0.2 from the owner's Pi Magazine lock, 1.1.0 with the production lock's package versions) | model output (scripted provider) |
| Magazine service process, worker processes, core engine, files on disk | Pi events in `test_pi6_units.py` (fixture events) |
| Pi Web UI 0.96.1 / 0.115.0 / 0.120.0 servers, WebSocket plugin discovery, plugin reload, host restart | provider usage numbers |
| Headless Chrome (`--disable-gpu`, SwiftShader) against those hosts | — |
| Released pi.5-rc.1 plugin ZIP (SHA-256 `9f91eeec…c9e6`) producing genuine pi.5 records | — |

## Commands

```sh
python3.11 -m venv venv && venv/bin/pip install pytest==9.1.1 httpx==0.28.1 PyYAML==6.0.3
export PYTHONPATH=$PWD/magazine-pi:$PWD/tests PYTHONDONTWRITEBYTECODE=1
export MAGAZINE_PI_SDK=<host>/node_modules/@earendil-works/pi-coding-agent
export MAGAZINE_PI5_PLUGIN_ZIP=<path>/Magazine-Pi-0.1.0-pi.5-rc.1-plugin.zip   # optional
venv/bin/python -m pytest tests -q -rs
PI_WEB_UI_DIR=<host>/node_modules/pi-web-ui MAGAZINE_PYTHON=venv/bin/python node --test tests/host/mock_host.test.mjs
venv/bin/python scripts/real_host_smoke.py --host-root <host> --port 18096
venv/bin/python scripts/real_host_ui.py --host-root <host> --port 18996    # needs playwright + Chrome
```

`<host>` is an npm prefix holding `node_modules/pi-web-ui`, for example
`npm i pi-web-ui@0.96.1 @earendil-works/pi-coding-agent@1.0.2` with the exact lock.

## rc.2 delta (token counter, executed 19 November 2026)

rc.2 changes only the client view (token counter), docs, version strings and the UI script. Only the
affected checks were re-run; the pi.7 results below otherwise stand unchanged.

| Check | 0.96.1 / SDK 1.0.2 | 0.115.0 / SDK 1.1.0 | 0.120.0 / SDK 1.5.2 |
|---|---|---|---|
| `test_pi6_units.py::test_token_counter_and_aggregate` (new) and `::test_client_bundle_is_import_free_and_exports_mount` | 2 pass | — | — |
| `scripts/real_host_update.py` (new): rc.1 → rc.2 through the shipped update scripts, unload/reload by browser connection only, second plugin stays active, run data kept, no restart | PASS | PASS | PASS |
| `scripts/real_host_ui.py` with the new counter checks (empty state, live/complete figures equal to the server's `status` totals, all-projects sum), fixture usage only | PASS (30 checks) | PASS | PASS |

## Results (pi.7, executed 18 November 2026, cloud workspace, 2 CPUs, Node 22.23.4, Python 3.11.18)

| Suite | SDK 1.0.2 (host 0.96.1 tree) | SDK 1.1.0 (0.115.0 tree) | SDK 1.5.2 (0.120.0 tree) |
|---|---|---|---|
| Original core regressions (unchanged files) | 611 pass | 611 pass | 611 pass |
| Original plugin tests (model selection 16, Pi integration 13, export 14, workspace 4) | 47 pass | 47 pass | 47 pass |
| `test_pi6_units.py` | 87 pass | 87 pass | 87 pass |
| `test_pi6_integration.py` | 22 pass | 22 pass | 22 pass |
| `test_pi7_scheduler.py` (Sequential/Round Robin, journal, restart) | 17 pass | 17 pass | 17 pass |
| `test_pi6_upgrade.py` (real pi.5 records) | 1 pass | 1 skip¹ | 1 skip¹ |
| **pytest total** | **785 pass, 0 fail, 0 skip** | **784 pass, 0 fail, 1 skip** | **784 pass, 0 fail, 1 skip** |
| `tests/host/mock_host.test.mjs` (host's own `createMockHost`) | 2 pass | 2 pass | 2 pass |
| `scripts/real_host_smoke.py` (real Pi Web UI, installs the plugin ZIP) | PASS | PASS | PASS |
| `scripts/real_host_ui.py` (real host + headless Chrome, plugin ZIP) | PASS | PASS | PASS |

The pytest and host results above were re-run from the **extracted source ZIP** and the
**plugin ZIP** themselves, not only from the development tree.

¹ pi.5 itself refuses every SDK except 1.0.2, so genuine pi.5 records exist only there.

Before any change, the untouched pi.5 source passed **658/658** against SDK 1.0.2.
That matches its recorded acceptance count.

## Behaviour → test map

| # | Required behaviour | Where |
|---|---|---|
| 1 | Registration, activation, deactivation, reload | mock_host.test.mjs; real_host_smoke (WS discovery, `plugins_reload`, host restart) |
| 2 | Version compatibility + diagnostics | units: `test_compatibility_pairs_and_diagnostics`, `test_worker_refuses_unverified_sdk…`; mock host refusal test |
| 3 | Workflow import/validation | units: manifest tests; integration import tests; original library tests |
| 4 | Model assignment (fake catalogue) | original `test_model_selection.py`; UI bulk assignment |
| 5 | READY without execution | integration `test_prepare_and_reopen_never_run_inference`; smoke; UI |
| 6 | Exact prompt delivery | integration `test_exact_prompt_in_both_protocols`; units `test_attempt_prompt_preserves…` |
| 7 | Fresh session + tool isolation | integration chain test (distinct sessions, one user message, exact tool list), `test_tool_allowlist_validation_and_isolation` (MCP not started) |
| 8 | Sequential multi-cartridge | integration chain test |
| 9 | Handover commit + integrity | integration chain test; units committed/tamper tests |
| 10 | Missing/corrupt/mismatched results | integration: missing publication, forged result, duplicate keys, corrupted commit |
| 11 | Recovery eligibility, five-attempt limit | units decide_retry matrix; integration ceiling + Continue + configured limit |
| 12 | Timeout, confirmed/unconfirmed cancellation | integration timeout; Antiloop abort evidence; original STOP tests |
| 13 | Antiloop warnings/stops/false positives | units (events); integration loop, off, parallel, frozen |
| 14 | STOP preserves prior results | integration `test_stop_preserves_prior_results_and_is_not_resumable` |
| 15 | Interruption + durable recovery | integration `test_process_interruption_is_not_resumed_automatically`; units reconcile |
| 16 | Tokens with retries/missing usage | units token tests; integration token test |
| 17 | JSONL + results ZIP | integration chain test; original export tests |
| 18 | Run selection after close/reopen | UI (reload + fresh profile); integration view_state |
| 19 | Legacy run compatibility | integration `test_legacy_pi5_run…`; upgrade test |
| 20 | Install/update data preservation | upgrade test (byte-for-byte, rollback read) |
| — | Export redaction of the ledger | integration `test_results_zip_ledger_is_redacted` |
| — | rc1 scheduler (Sequential, Round Robin, journal, retention, queued STOP, restoration) | `test_pi7_scheduler.py`; `real_host_ui.py` (queue, Round Robin order, restoration priority) |

## Not executed

No live model run. No test against the owner's running containers. No Firefox
run. The UI test used Chrome only. No Node 24 run. No power-loss or disk-full tests.
