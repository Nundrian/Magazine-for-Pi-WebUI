# Scheduler port — Magazine for Pi Web UI 0.1.0-pi.7-rc.2

18 November 2026. This ports Magazine Simple **0.3.0-scheduler.rc1** to the Pi plugin, on
top of 0.1.0-pi.6-rc.1. The rc1 package was verified against its supplied checksum
(`b2acaad4…1864`). Its `baseline/magazine_simple.py` is reliability.3 (`09a8706`) exactly,
apart from CRLF line endings. Offline only. **No model was used.**

## What rc1 adds, and how Pi does it

| rc1 behaviour | Pi implementation |
|---|---|
| One cartridge executes at a time; Sequential default | `scheduler.py`: one execution slot per plugin process. **New in Pi:** pi.5/pi.6 let simultaneous Starts run concurrently. |
| Round Robin rotates only at a committed cartridge boundary, FIFO, only when another project is eligible | The executor asks the scheduler at the start of every cartridge after the first. By then the core has durably committed the previous cartridge, and its attempts, recovery and handover commit have finished. A run that yields keeps its core RUNNING state and waits. |
| Mode change persisted at once, observed at the next boundary, never cancels | `set_scheduler`. Tested by changing mode in the middle of an attempt. |
| Durable admission before dispatch | Start validates, writes the journal, then launches if the slot is free; otherwise it answers `queued` with a position. A queued run that has never started stays **READY** in the unchanged core and enters it only when it gets the slot. |
| `scheduler-v1.json`: schema 1, mode, current, ordered queue, checkpoints; no credentials | Same file and fields in Magazine's data root (`run_id`, `owner`, `from_index`, `prepared`), plus bounded `notes` explaining why a run could not start. Written atomically with read-back. |
| Corrupt or unwritable journal blocks dispatch and is kept | Same. The view shows the error, and Start is refused with 409. |
| After a restart, retained entries need explicit, authenticated recovery | Pi has no per-user credentials, so recovery is an explicit **Start** that keeps the retained position. A run that was inside the core is first auto-halted by Start, then needs **Prepare recovery** (the existing core path). Nothing resumes by itself. Other eligible projects can progress meanwhile. |
| STOP on a queued project | A run that has never started is **removed from the queue and stays READY** (same as rc1). A run waiting after a committed cartridge becomes **STOPPED**, through the core's own STOP path, and stays stopped after refresh. |
| Failed, HALT or setup-failed runs release the slot | The slot is released when the run's task ends, for any reason. |
| Plugin shutdown | The queue is frozen into the journal as retained entries. Nothing is dispatched while shutting down. |
| Project summary: status, finished/total, current cartridge and attempt, model, reported tokens, queue position, recovery required | `runs` action and the new dashboard table (filter by text and status, sticky header, scrolling). |
| Restoration order: remembered run if running/queued → another running run → remembered run in any state → no invented selection | `chooseRun` in the view, using the server-side view state (browser storage is only a fallback). Viewing and polling are reads only. |
| Late responses cannot overwrite a newer selection | Every detail request carries a selection generation; stale answers are dropped. |
| Reported token counter (rc1 "Reported tokens": input / output / total) | rc.2: a counter block in **3. Selected project** (Input, Output and Total tokens in large figures, cache read/write when reported, and a status line: live while running, waiting, unavailable or incomplete), plus an **all projects** counter above the dashboard summing the exact reported counts. It updates from the ledger each time a model reply ends (polled every 1.5 s; the dashboard every 3 s). Nothing is estimated: unreported usage is shown as — and counted as unreported. |
| Six-column ledger with expandable, lazily loaded attempt details, status filter | Cartridge, status, model, attempts, reported tokens with completeness, duration. Each row expands (with `aria-expanded`) to every attempt's full record. The exported Markdown ledger is unchanged. |
| Shared mode is administrator-only | Pi 0.96.1 has no user roles. The mode is a shared operator setting behind Pi's own access control. |

Not ported: the Open WebUI sidebar launcher, the browser theme selector (Pi supplies the theme),
chat links, and the `confirm_inactive` dialog. Pi's own lost-runtime fencing and the separate
Start → Prepare recovery → Start steps already require explicit action, and a Pi worker
exits when its service ends.

## Unchanged

The 29 core modules, `beta5_helpers.py` and `model_selection.py` are still byte-identical to pi.5.
Handovers, retry policy, Antiloop, token accounting, exports and the pi.5 legacy semantics are as
in pi.6.

## Tests (offline)

`tests/test_pi7_scheduler.py` has 17 tests. They cover: journal round-trip and retention
without dispatch, 7 corrupt-journal cases, mode validation, Sequential order (A1-A3 before
B1-B2), Round Robin order across three projects of unequal length (A1 B1 C1 A2 B2 C2 A3, each
cartridge exactly one committed attempt), a mid-attempt mode change, STOP of a never-started
and of a rotated project, slot release after HALT, a hard process kill with retention (no
inference after restart, order kept through recovery, committed cartridges never repeated),
graceful-shutdown retention with removal, and a corrupt journal at service level. In every
test at most one provider request was in flight. The real-browser test also drives Round Robin,
queueing and restoration through the UI.

Full counts are in TESTING.md.

## Limits

These are single-process scheduling only (as in rc1), and process-kill durability, not
power loss. They also need live-model acceptance: real models, rotation under real generation
times, and a long real workflow in the dashboard. Rolling back to pi.6 or pi.5 does not
understand the journal. Roll back only with an empty queue, and keep `scheduler-v1.json`.
