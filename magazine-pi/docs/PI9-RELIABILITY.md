# Magazine 0.1.0-pi.9 — worker isolation, dependable Stop and restore points

This release repairs the failure seen in the Nebula Swamp run on 24 November 2026.
In that run, a worker model rewrote Magazine's own `run/state.json`, Stop failed,
and the browser view broke. This document describes what changed and what it still
cannot do.

## 1. Worker isolation (kernel-enforced)

Pi, Magazine and every worker run as the same unprivileged container user, so file
permissions cannot separate them. Every worker, including the catalogue worker, is
started through `sandbox.py`. The restrictions it applies are irreversible and are
inherited by every program the worker starts.

**Worker domain (Landlock, applied before Node starts).**
* Node and the SDK may write only:
  * the project workspace;
  * this attempt's session folder (`sessions/<attempt-id>/`);
  * this attempt's publication folder;
  * private scratch folders (`HOME`, `TMPDIR` and a copy of the model configuration);
  * a few device files (`/dev/null`, `/dev/zero`, `/dev/full`, `/dev/random`, `/dev/urandom`, `/dev/tty`; not `/dev/shm`);
  * folders listed in `workerWritePaths`.
* The worker can neither read nor write:
  * Magazine's data root;
  * Pi's data directory (plugins, configuration, MCP settings, secrets);
  * the agent directory.

  Writing to `/tmp` is refused.
* Every grant is checked on the opened descriptor against the real path that
  Magazine validated. A grant is refused if it:
  * was swapped for a symbolic link after validation;
  * contains Magazine's or Pi's state;
  * lies inside that state, unless it is one of the attempt's own folders.
* Landlock ABI 3 or newer is required, because older ABIs cannot govern `truncate`.

**Tool domain (nested Landlock layer).** Every shell command a model runs first
re-executes itself inside a nested domain. The Pi SDK's `shellCommandPrefix` does
this, and if the nested domain cannot be entered the command exits instead.
* Tool processes cannot open the worker's `/proc/<pid>/fd`, so they cannot forge
  messages to Magazine.
* They have no write access to the session or publication folders, or to the
  configuration copy.

Only Magazine's own publisher code, running in the worker, can write the attempt's
result file.

**seccomp.** The following are refused:
* creating UNIX-domain sockets;
* datagram `socketpair`;
* io_uring;
* foreign-architecture system calls.

Pi's control socket returns the host access token to any client, so it is unreachable.
Stream `socketpair` (ordinary child-process pipes) is allowed.

**Environment.** Workers receive an allow-listed environment (`PATH`, locale,
proxies, CA bundles, `OLLAMA_HOST`) plus names listed in `workerEnv`.
`PI_WEB_*`, including `PI_WEB_TOKEN`, is never passed.

**Process lifetime.** The launcher is a sandboxed child subreaper. When Node exits,
or Magazine stops the worker, every remaining descendant is killed, including any
that called `setsid`.

The worker reports its confinement, and the ledger records it on each attempt
(`sandbox`). If confinement cannot be established, inference is refused and the
attempt fails with `WORKER_SANDBOX_UNAVAILABLE`.

Configuration (`plugins/magazine-pi/config.json`):

| Key | Default | Meaning |
|---|---|---|
| `workerSandbox` | `"required"` | `"off"` explicitly accepts unconfined workers (not recommended). |
| `workerWritePaths` | `[]` | Extra absolute folders workers may change, such as a mounted project folder. These are refused if they overlap Magazine's or Pi's state. |
| `workerReadPaths` | `[]` | Extra read-only paths a workflow genuinely needs. |
| `workerEnv` | `[]` | Extra environment variable names passed to workers. `PI_WEB_*` is never passed. |

### Verified

* **Installed container:** kernel `6.6.87.2-microsoft-standard-WSL2`, Landlock ABI 3,
  uid 1000, no capabilities.
* **Automated tests:** the real SDK `bash` tool, running Python, Node and shell
  subprocesses, is refused each of these:
  * redirection, symbolic links, `..` traversal, hard links, renames and unlinks
    against controller files;
  * Pi configuration and `mcp.json`;
  * the worker's stdout channel;
  * the session and publication folders;
  * UNIX sockets and datagram socket pairs;
  * `/tmp`.

  A `setsid` background process is killed when the attempt ends.
* **Independent review:** a second, independent audit reproduced each finding as
  uid `nobody` and re-ran it against the fixed code.

* **Windows-backed folders (Docker Desktop, fixed in 0.1.0-pi.9.1).** Docker
  Desktop mounts Windows folders as drvfs/9p with `cache=5`. There, a directory gets
  a new inode once nothing holds it open, and Landlock attaches rules to inodes. The
  launcher therefore holds every grant's descriptor open for the worker's whole life.
  It does so close-on-exec, so the worker does not inherit it. The installed
  container confirmed the failure and the fix:
  * a rule on a 9p subfolder stopped matching once its descriptor closed, and held
    while the descriptor stayed open;
  * a rule at a mount root, such as `/workspace`, was never affected.

### Limits (not claimed as protected)

* **Permissions and timestamps.** No Landlock right governs `chmod`, `chown`
  (same user) or `utimes`, so a worker can still change the permissions or times of
  files it cannot open. Magazine owns those files and repairs this itself:
  * the fence (§2) checks and restores modes after every attempt;
  * the service restores owner access on every request and logs each correction
    to `integrity-events.jsonl`;
  * repair uses ctime, which a worker cannot set back.
* **Network.** Landlock network rules need ABI 4 or newer, and this kernel
  provides ABI 3. Workers can reach any network service, including Pi's HTTP port,
  which Pi's own token protects: keep `PI_WEB_TOKEN` set.
* **Signals.** Before ABI 6, a worker can stop or kill other processes of the same
  user, including Magazine and Pi. The result is lost availability, not silently
  corrupted records, but a stopped service cannot run Stop or the fence until it
  continues. ABI 6 signal scoping is applied automatically when available.
* **Workspace files.** Workers may change anything in the workspace and in
  `workerWritePaths`, including other runs' working folders there. Committed
  results are verified copies held in Magazine's state.
* **The worker's own model configuration copy.** Tools can read it (Ollama's
  `auth.json` is empty), but cannot change it.

## 2. Controller-integrity fence (second line of defence)

Before every worker starts, Magazine records the exact bytes of its own files: run
state, execution claim, protocol, document, metadata and ledger. It also records
their permissions and those of the run folders. After the worker process exits,
Magazine checks them again. This happens on success, failure, timeout and Stop.

If anything changed, or a worker placed a file beside the run state (as the Nebula
Swamp worker did with `state.json.bak.c001`), Magazine:
* moves the foreign bytes to `quarantine/<time>-<attempt>/`, together with a manifest;
* puts its own last written bytes back;
* rejects the attempt with `CONTROLLER_STATE_MODIFIED`, a non-retryable failure;
* halts the run through the normal fenced path.

A syntactically valid DONE never outranks this check.

## 3. Stop never depends on readable state

If the run state cannot be read, the run is shown as **STATE DAMAGED**. Every field
the view needs is still returned, so the browser view no longer breaks. While a
task is active, Stop never takes the reconcile path.

**Stop** stays enabled. On a damaged run, Stop:
* removes the run from the scheduler;
* cancels the local task;
* asks the worker to abort, and terminates its process group if it does not acknowledge;
* records what it observed in `run/emergency-stop.json`, separately from the damaged state.

The response reports the actual outcome: `emergency_stopped`, or `stop_unconfirmed`.
It never claims the run was halted cleanly. The same emergency path is used whenever
the durable Stop path refuses or fails while a task is active, for example when
`execution.json` is unreadable.

## 4. Repair state (explicit, never executes)

For a STATE DAMAGED run, **Repair state** shows a preview first, then rebuilds the
controller state from Magazine's own verified records.

* An invalid state file cannot have been written by Magazine, which validates every
  state it saves. Two attempts are therefore implicated:
  * the most recent attempt, always;
  * any attempt that was running when the file last changed, judged by the newer of
    mtime and ctime.

  Their completion claims and commits are rejected in the ledger.
* A cartridge counts as completed only if its committed handover still verifies, or
  for older protocols its canonical result exists. The cartridge must also not be
  implicated.
* The damaged file and any foreign files are kept in `quarantine/`. The run becomes
  HALTED at the first unaccepted cartridge, with a matching revision. The execution
  claim is retired as `state-repaired`, and a receipt is written to `repair.json`.
* Repair is refused while a local task exists, or while any Magazine worker or
  launcher process exists.
  Nothing is started. Use **Prepare recovery**, then **Start**, when you choose to continue.

## 5. Deterministic model failures stop at once

The following failures halt the run immediately:
* an unsupported or unknown model architecture;
* a model that does not support tools;
* a missing model;
* errors loading the model.

They are recorded as `MODEL_INCOMPATIBLE`. An unreachable provider (for example
Ollama stopped) or insufficient memory for the model is recorded as
`PROVIDER_UNAVAILABLE`. It is retried once in a fresh session; an identical second
failure halts the run (`NO_PROGRESS`). Other errors keep the existing retry rules,
within the five-attempt ceiling.

## 6. Instructions no longer contradict each other

Each result-file attempt now carries a short **controller boundary** statement:
* Magazine's records are not part of the project;
* any state or evidence file the step asks for belongs in the project workspace;
* a missing required path means HALT, not a substitute location.

A final JSON object whose `status` is DONE or HALT satisfies Magazine's closing rule.
A cartridge that asks for a JSON final answer is therefore compatible.

## 7. Required project paths (optional preflight)

A workflow may declare, in `magazine.yaml`:

```yaml
required_paths: ['/projects/NebulaSwamp']
```

**Start** and **Prepare recovery** are refused, before any inference, while a declared
path is not a folder inside the container. Workflows that declare nothing are
unaffected. The Set up preview lists any Windows drive paths named in the prompts, as
information only.

## 8. Restore points (Manage)

Deleted saved runs and workflow templates have always been kept in Magazine's trash.
**Manage → Restore points** now shows, for each one:
* the run or template, with its progress and next cartridge;
* the time it was created and the reason;
* its size;
* whether it can be restored or deleted.

You can restore an item, delete one, delete a selection, or delete all eligible items
after an in-page confirmation. Deletion permanently removes the folder and reclaims
its storage. Before removing anything, Magazine writes a durable marker. It never
follows links and never crosses filesystems. Files that are hard-linked elsewhere stay
with their other copy.

Two kinds of item are protected:
* interrupted deletions, which must be restored first;
* unreadable or unrecognised folders.

A restore point whose run ID is scheduled is also protected. Restoring now removes the
emptied transaction folder, which previously remained as an empty entry. Nothing is
deleted automatically.
