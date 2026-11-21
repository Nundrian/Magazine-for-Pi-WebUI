# Engineering report — Magazine for Pi Web UI 0.1.0-pi.6-rc.1 (pi.7 scheduler addendum: PI7-SCHEDULER.md)

17 November 2026. Offline release candidate. **Not installed. Not published. No model was used.**

## 1. Sources and identities

| Item | Identity |
|---|---|
| Starting point | Magazine for Pi Web UI 0.1.0-pi.5-rc.1 (owner's original project folder, left unchanged); released plugin ZIP SHA-256 `9f91eeec9058d8c9698ee3214543a566ad36fa27faed0417a37e353fd498c9e6` (matches its SHA256SUMS) |
| Open WebUI reference | github.com/Nundrian/Magazine `09a8706e5f01fe6d8db0b8941ccf0d772b374005` (still HEAD at the end of the work); `magazine_simple_reliability3.py` SHA-256 `28a9f317…730b` (166,397 bytes, matches its published record) |
| Pi Web UI upstream | github.com/xing-shuyin/pi-web-ui `8e5345d6` (0.121.0); tags v0.96.1 `bbcbec4b`, v0.115.0 `33b943cc`, v0.120.0 `3ddf35f9` |
| Installed hosts (read-only) | Pi Magazine 0.96.1 + SDK 1.0.2; production source 0.115.0 (`7c9d300`) + SDK 1.1.0 — see COMPATIBILITY.md |
| Clean development tree | separate allowlisted copy (private runtime, evidence, venvs, browser profiles and backups excluded) |

## 2. Design in one paragraph

The 29 core engine modules, `beta5_helpers.py` and `model_selection.py` are **byte-identical**
to pi.5. The core already calls one injected `dispatch(prompt, model)` per cartridge
and fence-persists HALTED if it raises. pi.6 places a new **CartridgeExecutor** behind
that boundary. It turns each dispatch into a bounded chain of fresh, independently
saved Pi sessions under the Magazine Simple reliability.3 rules. It commits and
re-verifies handovers, applies Antiloop to Pi's structured events, records every
attempt in `ledger.json`, and returns the verified final text to the core. STOP,
claims, recovery, crash fencing, results and Finished Work remain the core's
existing, tested mechanisms. Between attempts the executor uses the core's own
`persist_stop_if_requested`/`StopRequested` path.

## 3. Changes and why

| File | Change | Reason |
|---|---|---|
| `protocol.py` (new) | Magazine Simple manifest superset (handover, artifacts, retry_safe, handover_mode, result_profile, recovery, loop_police); `recovery_policy`, `loop_policy`, reply judgement, pointers | §7–9 parity; strict v1 core schema unchanged (it receives only id/job_type/prompt) |
| `handover.py` (new) | result_file_v1 storage: strict JSON, durable writes with read-back, envelope/identity/selector validation, session archive, commit marker, re-verification, verified file inputs, candidate snapshots | §7 |
| `executor.py` (new) | attempt loop, retry admission, timeouts, Antiloop abort, STOP boundary between attempts, restart reconciliation | §6, §8, §9 |
| `antiloop.py` (new) | LoopPolice over Pi `message_end`/`tool_execution_end` events | §9 |
| `tokens.py`, `ledger.py` (new) | usage from persisted sessions; per-attempt/cartridge/run totals; ledger, activity, LEDGER.md | §10 |
| `worker.mjs` | SDK gate from `compatibility.json`; structured outcome instead of throwing on non-stop endings; event forwarding (no text/reasoning); `magazine_publish_result` custom tool; plain tool-name enforcement; package resolution fix | §4, §7, §9 |
| `pi_dispatch.py` | per-attempt timeout, event callback, `terminate()`; `__call__` keeps the pi.5 contract | §8 |
| `service.py` | protocol records, Antiloop preference/freeze, executor wiring, ceiling check at Start, display states (STOPPED/STOP_REQUESTED), ledger/view-state actions, redacted ledger in results ZIP, terminal-run export settling | §6, §8, §11 |
| `index.mjs` | exact host+SDK pair check with diagnostics; optional `timeoutSeconds`; optional exact override | §4 |
| `run_export.py` | ledger/protocol/handover snapshots in JSONL; exact session→cartridge association from the ledger; changed-source name in the retry message | §10, §14 |
| `client/entry.mjs` | rebuilt view (see §11 of the brief); still import-free | §11 |
| `compatibility.json` (new) | verified pairs | §4 |

The full per-file SHA comparison is in `evidence/source-comparison.json`:
46 files unchanged, 7 modified, 7 new, 0 removed. Function-level provenance is in
`magazine-pi/reused-components.json` → `pi6_adaptation`.

## 4. Feature-parity matrix (against reliability.3)

**1. Already present in pi.5 (kept unchanged).** These include native registration, the
Python service bridge, and fresh SDK workers with an exact model and no fallback.
Explicit tool allowlists and no inherited context/extensions/skills are kept, as are
READY then explicit Start, durable state and claims, and STOP with confirmed or
unconfirmed cancellation. Recovery to READY, lost-runtime auto-halt with no automatic
resume, project ZIP library, results ZIP, Finished Work, run JSONL, and per-cartridge
or shared model routing are also unchanged.

**2. Missing from Pi, now implemented.**

| Behaviour | Status |
|---|---|
| result_file_v1 value handovers (selectors) and file handovers (inputs/outputs with SHA-256) | implemented, tested |
| Attempt identity, attempt-scoped publication, strict validation, commit + marker + read-back, re-verification before every consumer | implemented, tested |
| DONE/HALT closing-line protocol | implemented (verbatim judgement) |
| Five total attempts, 1,800 s per fresh attempt, smaller explicit limits respected, hard ceiling across Continue | implemented, tested |
| reliability.3 admission: repeated completion omissions keep retrying; NO_PROGRESS, HALT, STOP, integrity, identity, permanent and unconfirmed-stop failures stop | implemented, tested (decision matrix + live fake runs) |
| Retry decision recorded per attempt (reason, remaining allowance, fingerprint) | implemented |
| Antiloop (3 warn / 5 stop / 3 errors / cycles / 64 history), per-workflow switch, default on, frozen at Start, evidence recorded | implemented, tested |
| Token accounting per attempt/cartridge/run, known/partial/unavailable, no double counting, retries included | implemented, tested |
| Ledger + activity, LEDGER.md and ledger.json in exports | implemented |
| Candidate-file snapshots for recovery (never promoted) | implemented |
| UI: token summary, progress table, attempt history, activity, bulk assignment, collapsible cartridges, run filter/grouping, persistent selection | implemented, browser-tested |

**3. Implemented differently in Pi.**

| reliability.3 | pi.6 | Why |
|---|---|---|
| `publish_result.py` run through the Terminal | `magazine_publish_result` Pi custom tool, present only in result_file_v1 attempts | Pi workers may have no shell; the tool is trusted code with fixed identities |
| Result identity `chat_id` | `session_id` (Pi session header) | Pi's unit of saved conversation |
| Chat archive JSON | archived copy of the saved session JSONL (original kept) | same purpose |
| **Added check:** result must equal the publisher's own receipt | `RESULT_RECEIPT_MISMATCH` (hard) | rejects results written by other means |
| REST polling of saved chats | SDK events forwarded by the worker | native event source |
| Stop confirmed by task API | SDK abort acknowledgement, else process-group termination confirmed by exit | Pi worker is a process |
| "Continue from this step" | Prepare recovery → Start (core recovery) | existing tested mechanism; ceiling enforced at Start |
| Usage from Open WebUI message | Pi persisted per-round usage; zero placeholders = unavailable | Pi schema |
| Work folders with chmod sealing | `<workspace>/magazine-runs/<run>/<cartridge>/`; no chmod sealing | Pi tools run as the same OS user, so modes would not protect; hashes are authoritative |
| Default protocol for any new run | Native view: result_file_v1 (default) or "final text" choice. API clients naming no protocol keep pi.5 text semantics for plain v1 manifests | preserves existing API contract |
| Owner-scoped preferences | per-workflow preference in the shared single-operator workspace | Pi 0.96.1 has no user identities |

**4. Cannot be verified without real inference.** These are listed in §9.

**5. Deliberately deferred in pi.6.** These are the Round Robin and persistent queue scheduler
(now ported in pi.7 from 0.3.0-scheduler.rc1; see PI7-SCHEDULER.md) and Tandem/parallel cartridges. Also deferred: the run queue that holds
a second Start until the first finishes, deleting runs or workflows from the view,
terminal selection, the Sidebar launcher, the legacy `require_done` valve, and chat
links. Each needs its own verified scheduling or handover guarantees, or is Open
WebUI-specific.

## 5. Regression results

Exact counts and commands are in TESTING.md. In short: unchanged pi.5 baseline 658/658.
pi.6 has 768/768 on SDK 1.0.2, and 767 + 1 justified skip on 1.1.0 and on 1.5.2.
Host lifecycle 2/2 ×3, real-host smoke 3/3 and real-host browser 3/3. All of these
are offline with the scripted provider.

Defects found by the tests and fixed:
1. The client bundle had a syntax error, giving a blank "Plugin view failed to load". Found by the real-browser test. A `node --check` and import-free guard is now in the unit suite.
2. SDK package resolution through `exports` silently failed. `require.resolve.paths` is now used.
3. A JSONL export could race the automatic Finished Work write of a just-COMPLETED run. Downloads now wait, bounded, for the terminal run's own bookkeeping.
4. The ledger in the results ZIP was not credential-redacted.
5. Simple manifests prepared through the API defaulted to text mode.

Test-driver mistakes are not counted as product defects. These were a port typo, a
hidden-element wait, mobile navigation, and wrong expectations.

## 6. Data preservation

- The original project folder was only read: a tar of an allowlist, and lock and
  Docker files read.
- Real pi.5 data was created by the released pi.5 ZIP and opened by pi.6. Every
  pre-existing file kept its hash. Starting a READY pi.5 run kept pi.5 semantics,
  with no `protocol.json` written and one attempt. pi.5 could then still read
  everything (rollback).
- Running a real host, reloading the plugin and restarting the host kept runs and the
  server-side view selection.

## 7. Security review

- **Publication capability.** The model gets one write capability, limited to its own
  attempt's `publish/result.json`. It cannot choose identities. It is strict JSON,
  1 MiB, refuses duplicate keys, writes atomically, verifies by read-back and issues
  a receipt. Results that do not match the receipt are rejected.
- **Not a sandbox.** Selected tools (`bash`, `write`, …) run with the Pi server's OS
  authority. A model with such tools can read or modify anything that user can,
  including Magazine's data directory. Handover checks therefore detect accidental or
  partial changes and non-publisher writes. **They do not defend against a deliberately
  adversarial agent with shell access.** Use container or OS isolation for untrusted work.
- **Tool names.** Only plain names are accepted. `*`, `+x`, `-x` and the reserved
  publisher name are refused, because SDK ≥1.1.0 gives patterns meaning. The exact
  active tool set is verified per attempt. MCP servers configured in the agent dir or
  the project were shown not to start.
- **Paths.** Cartridge IDs map to safe single components. Declared paths are
  relative and contained. Reads refuse links and non-regular files. Inputs are copied
  by hash.
- **Exports.** JSONL and the ledger in the results ZIP pass through the existing
  credential redaction. Arbitrary unlabelled secrets in model text cannot be
  recognised, so treat downloads as sensitive.
- **Access.** There is no new login or key. Pi's middleware protects
  `/plugins-api/magazine-pi/*`. Server files are not served (checked on real hosts).
- **Release scan.** See the release-scan report delivered with the package.

## 8. Resource use

All work ran in an isolated cloud workspace (2 CPUs, `nice 10`). The owner's computer
was used only for file listing, one tar of the source allowlist, and reading three
version files. Its shell has no route to Docker, Ollama or the GPU. No model,
provider, GPU, Pi container or browser profile of the owner was touched.

One worker process starts in about 0.7 s, peaks around 133 MB RSS and uses about 0.9 s
of CPU. Each full pytest suite takes about 2.5 minutes. (pi.6 allowed simultaneous Starts to run
concurrently; pi.7 adds the rc1 scheduler, so only one cartridge executes at a time.)

## 9. Open issues and what needs real-model acceptance

1. Whether local models reliably call `magazine_publish_result` and end with the DONE
   line. This decides how often the retry policy is needed.
2. Whether real providers report streaming usage through Pi. Ollama's OpenAI endpoint
   may omit it, and Magazine would then show "unavailable", correctly.
3. SDK abort acknowledgement times with a real local model during long generation and
   during tool execution.
4. Antiloop on real tool patterns, especially volatile outputs and edit tools that
   report success without change.
5. Publish-only recovery is an instruction, not an enforced restriction. The OWUI
   real-world record already shows a model editing anyway.
6. Live runs on 0.115.0/1.1.0 and 0.120.0/1.5.2, and Node 24 with pi.6.
7. The production 0.115.0 image has no Python. Running Magazine there needs a separate
   image decision.

Suggested first live check, once authorised, on the Pi Magazine instance: one
three-cartridge result_file_v1 workflow with a value and a file handover, one
deliberate DONE omission, one STOP during the second cartridge, then exports.
