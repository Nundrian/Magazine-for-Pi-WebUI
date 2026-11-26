# Changelog — Magazine for Pi WebUI

## 0.1.0-pi.10.4 corrective candidate

- Folder delivery now leaves cleanup to the publication routine once it consumes the staging directory. Exports report success, identical exports are reused, and conflicting destinations keep their contents.

## 0.1.0-pi.10.3 corrective candidate

- Project checkpoints validate the report during the attempt and apply it only after Magazine verifies the published handover, output and completion.
- A trusted transaction journal ties every checkpoint to the run and attempt. Explicit recovery replays that attempt across interrupted writes.
- External services default to one authenticated read-only probe. Project operations require declared project IDs. Job lookups use receipts from the same cartridge.
- Finished Work keeps its canonical snapshot. Delivered copies redact credentials and provide an explicit browser fallback when folder delivery fails.
- See [compatibility and recovery notes](docs/PI103-CORRECTIVE.md) before starting a run prepared by pi.10.2.

## 0.1.0-pi.10.2 — 25 November 2026

- **Export Finished Work now reaches you.** It used to write only into Magazine's own Docker volume
  (`finished-work`), invisible from Windows, and said only "Finished Work export checked". The canonical copy is
  still written there first; then, by default, the export is downloaded through your browser as
  `Magazine-<workflow>-<run>.zip` (Firefox saves it in Downloads, or asks, per its own setting).
  **Manage → Finished Work exports** can switch to saving into a folder instead. The folder must be inside a
  folder mounted into Pi that Magazine may write (the workspace, `workerWritePaths` or a new `exportRoots`
  config list) and never inside Magazine/Pi data; it is shown with its Windows path. Exports are never
  overwritten: an identical re-export is reused, a different one gets a numbered folder.

## 0.1.0-pi.10.1 — 25 November 2026

Fixes found in live use of pi.10 on the owner's PC.

- **Test connection reported "Working" without contacting the service.** The Manage action passed its requirement
  in the wrong shape, so the preflight saw nothing declared and returned success. It now calls the protected API
  and reports "No check was performed" as a failure if, for any reason, nothing ran. (Start, Prepare recovery and
  Recheck dependencies were not affected: Start correctly refused with HTTP 401 for a wrong key.)
- **Connect tests the key at once** and keeps a dated Working / Not working line per service.
- **The Connector's other keys are refused by name** in the Unreal Connector row (link, browser-sync, operator,
  Resource Kit keys); the link key had been stored as the API key.

## 0.1.0-pi.10 — 25 November 2026

Lets a workflow use Unreal Connector and 3D Resource Kit safely, checks those dependencies before any model
runs, and makes project-local checkpoints deterministic. Workflows that declare none of this are unchanged.

- **Host tools through a trusted broker (`host_tools.py`).** A workflow may declare
  `requires: {services: {unreal_connector: {...}, resource_kit: {...}}}`. Its workers then get one tool,
  `magazine_host`, that forwards an allowlisted operation over the worker's private channel to the Magazine
  service. The service attaches the key, calls the fixed host (`host.docker.internal:8765/8766`), masks the key in
  every reply, and records operation, status and job/approval/plan identifiers in `host-calls/<cartridge>.jsonl`.
  Undeclared operations, project IDs outside the declared list, arbitrary URLs and `open_project_in_unreal` are
  refused. Keys live in Magazine's data folder (`host-services.json`, 0600), which workers cannot read; the
  Resource Kit key written by the Kit's own Pi pairing is reused automatically.
- **Dependency preflight.** Start, Prepare recovery and a queued run's admission call each declared service's
  protected API (not just its public schema): reachability, key accepted, optional minimum API version,
  capabilities, registered projects, key scopes and "no Unreal already running". Any failure refuses the run
  before a model is contacted, with the reason. The run view shows the last check and a **Recheck dependencies**
  button. Manage has a **Host connections** panel (connect, test, forget; keys are never shown again).
- **Deterministic project checkpoints (`checkpoint.py`).** With `project_checkpoint: {root: ...}` a step writes its
  report and calls `magazine_checkpoint`. Magazine checks the exact report bytes (JSON, matching `step_id`,
  status DONE), that the predecessor is exactly the previous step and that its report still matches the recorded
  checksum; then it backs up the old state and writes the new one atomically with read-back. Its own record
  (in the run folder) detects any later edit of the state file. Repeats of identical bytes are idempotent; a
  different report for a committed step, a skipped predecessor, or a non-DONE report is refused. With
  `required: true` (default) a cartridge's DONE is accepted only after its checkpoint commit
  (`CHECKPOINT_NOT_COMMITTED` otherwise). Runs using these features are recorded as protocol version 2, so an
  older Magazine refuses to resume them rather than skipping the checks.
- **Recovery attempts see earlier external operations.** Job, approval and plan identifiers from earlier attempts
  of the same cartridge are listed in the recovery prompt so the model queries them instead of resubmitting.
- **Pi sign-in (HTTP 401) handling.** A plain-text 401 from Pi on a read is retried once after a pause, so a
  transient rejection no longer leaves a stale "Sign in to Pi again" warning. A persistent one says whether Pi
  cleared a stale sign-in or received none, and the warning clears itself when requests succeed again. A
  state-changing action is never repeated automatically; its refusal says it may not have been carried out.

Tests: 25 new Python (20 unit, 5 end-to-end with the real Pi SDK, a sandboxed worker, a scripted provider and a
loopback Unreal Connector fixture) and 5 new client tests; the Nebula Swamp c007/c008 files are a regression
fixture.

## 0.1.0-pi.9.1 — 24 November 2026

Fixes a failure found when 0.1.0-pi.9 was installed on Docker Desktop for Windows.
The simulated tests had not caught it.

- **Workers could not start on Windows-backed folders.** Docker Desktop mounts
  Windows folders as drvfs/9p (`cache=5`). On these mounts a directory gets a new
  inode once nothing holds it open, so a Landlock rule on a directory inside such a
  mount stopped matching once the launcher closed the grant's descriptor. In the
  installed container the plugin lives at `/app/.pi-web/plugins/magazine-pi`, so
  every worker, including catalogue discovery, was refused its own `worker.mjs`.
  It exited with "Pi worker exited without a result". Grants at a mount root, such
  as `/workspace`, were not affected.

  The launcher now keeps each grant's descriptor open (close-on-exec, so the worker
  does not inherit it) for as long as it supervises the worker. Each rule therefore
  stays on the inode that later lookups reach. Nothing is granted beyond the
  existing policy. Confirmed in the installed container: with the descriptor closed,
  reading and writing a 9p subfolder was denied; with it held open, both worked.
- **A worker that dies before reporting now says why.** Its stderr goes to a file in
  its private folder, which the worker cannot replace. Its exit code or signal and
  its last error line are added to "Pi worker exited without a result":
  - credentials are masked (Authorization headers, URL user info, quoted values,
    common token shapes);
  - control characters are removed;
  - the detail is shown, but it never changes how the failure is classified.

  The file is discarded with the folder.
- The launcher raises its soft open-file limit when a policy has many grants, since
  each pinned grant holds a descriptor. If the hard limit is too low, it refuses
  clearly.
- An independent review reproduced the 9p failure with an inode-unstable FUSE
  emulation and confirmed:
  - the fix works, including for the nested tool domain;
  - pinned descriptors give a worker no access beyond its policy.

  Its minor findings are fixed in this release.

## 0.1.0-pi.9 — 24 November 2026

Repairs the Nebula Swamp incident of 24 November 2026, in which a worker model
rewrote Magazine's run state, Stop failed and the view broke
(`r.done is undefined`).

### Security and isolation
- Every worker, including catalogue discovery, now starts through `sandbox.py`.
  Linux Landlock (ABI 3 or newer) and a seccomp filter apply to the worker.
  - The worker can write only to the workspace, its own attempt folders, private
    scratch folders, specific device files and `workerWritePaths`.
  - It can neither read nor write Magazine's state, Pi's data directory or the agent
    directory. It receives copies of the model configuration.
  - Each grant is verified on the opened descriptor, which defeats symbolic links
    swapped in after validation.
- Model shell commands run in a nested Landlock "tool domain". They cannot reach
  the worker's channel to Magazine, or the attempt's session and publication folders.
- seccomp refuses UNIX sockets and datagram socket pairs, which closes Pi's control
  socket and the host token it hands out. It also refuses io_uring and
  foreign-architecture system calls.
- Workers get an allow-listed environment; `PI_WEB_*` is never passed.
- The launcher is a child subreaper: no descendant outlives its worker.
- New configuration keys: `workerSandbox` (default `required`), `workerWritePaths`,
  `workerReadPaths`, `workerEnv`. A worker that cannot be confined is not started.
- **Not covered:** network access (needs ABI 4) and signals (needs ABI 6). `chmod`
  and `utimes` are not governed by Landlock; Magazine detects and repairs
  permission changes itself.
- Each attempt's session now lives in `sessions/<attempt-id>/`. Older flat sessions
  are still read and exported.

### Reliability
- **Controller-integrity fence.** Magazine's own files and their permissions are
  sealed before each worker and verified after it exits. Any change, or a file left
  beside the run state, rejects the attempt with `CONTROLLER_STATE_MODIFIED` (no
  retry). Magazine restores its own bytes and permissions, quarantines the foreign
  bytes and halts the run normally. The service also restores owner access on every
  request and logs it to `integrity-events.jsonl`.
- **Dependable Stop.** A run whose state cannot be read is shown as STATE DAMAGED.
  Stop cancels execution first (abort, then process-group termination), records
  `run/emergency-stop.json` and reports the observed outcome. While a task is
  active, it falls back to the same path whenever durable Stop refuses or fails.
- **Repair state.** An explicit action with a preview. It quarantines the damaged
  file and always rejects the most recent attempt, plus any attempt that ran while
  the state changed. It accepts only cartridges whose commits still verify, writes
  HALTED and leaves a `repair.json` receipt. It never starts work.
- **Deterministic model failures halt at once.** Architecture and tool-support
  errors, missing models and load failures are recorded as `MODEL_INCOMPATIBLE`.
  An unreachable provider or insufficient memory (`PROVIDER_UNAVAILABLE`) gets one
  fresh retry, then halts.
- **Optional `required_paths` preflight.** Start and Prepare recovery are refused
  while a declared project folder is not mounted. The Set up preview points out
  Windows paths named in prompts.
- Result-file attempts now carry a controller-boundary statement. A final JSON object
  with status DONE or HALT satisfies Magazine's closing rule.

### Interface
- The view stays usable when a run is damaged, incomplete or from an older server,
  which fixes `r.done is undefined`. Stop stays available for damaged runs; Repair
  state appears when needed.
- **Manage → Restore points** lists each restore point's run or template, progress,
  creation time, reason and size, and whether it is protected. You can restore one,
  delete one, delete a selection, or delete all eligible items after an in-page
  confirmation. Deletion reclaims storage; nothing is deleted automatically.
  Restoring no longer leaves an empty trash folder.
- **Manage → Worker isolation** shows the confinement in force.

### Unchanged
- The 29 core engine modules and the bundled beta.5 helper keep their recorded hashes.
- The five-attempt ceiling, Antiloop, scheduler, ledger and export formats are unchanged.
- No data migration is required.
