# How Magazine executes, checks and retains work

This guide describes the repository's **0.1.0-pi.8-ui.4** implementation,
including the 24 November 2026 guarded restart-recovery repair. It explains the
mechanisms and their limits. It does not turn historical tests into guarantees
about every provider, model, filesystem or future Pi version.

For the interface, use the [seven-tab visual guide](VISUAL-GUIDE.md).
For a shorter introduction, return to the [front page](../README.md).

## Contents

- [The execution model](#the-execution-model)
- [Workflow import and validation](#workflow-import-and-validation)
- [Models and tools](#models-and-tools)
- [Preparation, Start and run states](#preparation-start-and-run-states)
- [Committed results and handovers](#committed-results-and-handovers)
- [Retries and recovery](#retries-and-recovery)
- [Antiloop](#antiloop)
- [STOP and uncertain termination](#stop-and-uncertain-termination)
- [Scheduling and restart behaviour](#scheduling-and-restart-behaviour)
- [Ledger, chats and token accounting](#ledger-chats-and-token-accounting)
- [Results, JSONL and redaction](#results-jsonl-and-redaction)
- [Deletion and restore](#deletion-and-restore)
- [Storage, host access and compatibility](#storage-host-access-and-compatibility)
- [Evidence and limits](#evidence-and-limits)
- [Implementation map](#implementation-map)

## The execution model

Magazine is deterministic orchestration around model calls. A human or planner
authors an ordered collection of bounded prompts. Magazine owns the queue,
saved run state and decision to advance. The worker handles the current
cartridge, not the choice of the next job or the overall workflow design.

There are three useful identities:

| Object | Meaning |
| --- | --- |
| Workflow/template | Reusable prompts, order and supported declarations. |
| Run | A particular prepared snapshot with saved model/tool/protocol choices and its own history. |
| Attempt | One fresh worker session for one cartridge. A retry is another attempt, not an invisible continuation. |

Pi's native plugin loads the browser client and routes requests to Magazine's
Python service. The existing core owns execution state, claims and STOP control.
The Pi adapter adds the scheduler, bounded attempt executor, SDK worker,
committed-result protocol and attempt ledger. The browser is not the execution
loop: switching tabs does not drive cartridge progression.

Each attempt creates a fresh saved Pi SDK session. Normal global/project context
discovery is disabled by the worker's resource/settings configuration; tools
and optional reviewed extensions are explicitly selected. The original cartridge
prompt is retained. In committed-result mode the delivered prompt appends the
required handover/file information and completion instructions. Both original
and delivered prompt hashes are recorded.

Fresh context is **not** filesystem or OS isolation. Attempts can still see
whatever their selected tools and the Pi process's permissions allow. The
conversation boundary reduces incidental chat carry-over; it does not make
untrusted code safe.

## Workflow import and validation

ZIP import has no separate Magazine administrator credential. It still enforces
the package contract and safe file handling:

- Bounded archive/member sizes and supported document/schema fields.
- Valid cartridge identifiers, nonempty prompts and a defined order.
- Exactly one prompt source per cartridge where the extended manifest requires it.
- Safe relative paths, rejection of traversal, links/special entries and collisions.
- Duplicate/case-conflicting destination checks and an import publication lock.
- Dependency declarations that refer only to an earlier cartridge. A declared
  input file must correspond to that earlier cartridge's declared output.
- Publication of the complete staged workflow without silently replacing an
  existing workflow. The retained original Input ZIP is separate from the active
  library entry.

The extended `magazine.yaml` supports model/job-type routing keys, handovers,
declared artifact inputs/outputs, recovery policy and loop policy. `constraints`
and `artifacts.review` are accepted compatibility fields but are **not enforced
as a general policy engine**; the parser records ignored fields. Writing a
restriction into a manifest does not create an OS sandbox or a new validator.

On the demonstrated Docker Desktop Windows-backed mount, atomic directory
publication returned `EINVAL` for `renameat2(RENAME_NOREPLACE)`. The repair keeps
the no-overwrite contract and uses verified Linux-backed internal storage. It
does not silently fall back to an ordinary overwrite-capable rename. Import
diagnostics distinguish unsupported operations, conflicts and permission/storage
problems. See [Linux storage](LINUX-STORAGE.md) before moving data.

## Models and tools

### Exact model selection

The catalogue comes from the host's configured Pi SDK/model registry. Magazine
does not install models, edit `models.json`, choose a named default LLM or change
provider context/reasoning settings. Compatible physical models follow the same
execution path; virtual routers are excluded by the existing execution contract.

The UI supports a workflow-wide model, per-cartridge overrides and bulk setup
assignment. The routing layer also retains explicit alias mappings for workflow
compatibility. For new preparation, precedence is:

1. Explicit cartridge assignment.
2. Supplied job-type alias mapping.
3. An explicit available host ID in the cartridge's routing key.
4. The user-selected workflow default.

Concrete override IDs take precedence over identically named aliases. Explicit
invalid assignments fail rather than falling through to a different model.
Preparation and Start validate availability. The worker checks that the assigned
provider/model identity is the one actually used. Stored run assignments do not
change when the operator edits a different workflow's setup.

### Tools are capabilities, not a sandbox

Only selected tools from the configured allowlist are exposed. No tools are
selected by default. Tool IDs must be plain supported names: SDK wildcard or
addition/removal patterns and the reserved publisher name are refused. The
worker verifies the actual active set for every attempt. Its controlled
resource-loading path does not start MCP servers from ambient configuration.

Committed-result attempts receive the plugin's own `magazine_publish_result`
tool in addition to permitted task tools. That tool writes only the designated
attempt result using controller-supplied identities. It is not a general file
manager or a tool that grants authority to reconfigure Pi.

Shell/edit tools still run as Pi's OS user. They do not inherit the open chat's
private permission preset, and Magazine's allowlist does not restrict all the
paths a shell command can reach. Use container/filesystem controls for jobs
that need such isolation.

### Optional planner suggestions

The Set up brief contains cartridge prompts/order, requirements and available
model/tool information. Building/copying it requires the review acknowledgement.
Magazine makes no automatic external AI call. Sharing the brief is an operator
action and may disclose task content.

Pasted suggestions or `model-assignments.json` are validated as a whole against
the workflow identity, exact cartridge order and live catalogue. Duplicate
keys/IDs, unknown fields, unresolved models and oversized plans are refused.
An invalid optional sidecar warns without making a valid workflow unusable.
Validate and Apply are different actions; manual overrides remain possible
before preparing a new run. A planner's rationale is advice, not measured proof
that the model will perform the task well.

## Preparation, Start and run states

Preparation saves a READY snapshot. Loading a template, viewing history,
validating a plan, preparing or restoring trash does not start inference.
**Start workflow** is the explicit execution admission action.

| State or display | Meaning and next action |
| --- | --- |
| READY | Prepared but not executing. Review, then Start when wanted. |
| QUEUED | Admitted to the scheduler and waiting for its slot. A never-started queued run can remain READY in the core. |
| RUNNING | The run has entered execution; it can be working or waiting at a scheduling boundary. |
| STOP REQUESTED | Cancellation/termination is in progress or being confirmed; do not treat it as safely stopped yet. |
| HALTED | Progression stopped on failure, uncertainty or a model HALT. Inspect evidence before explicit recovery. |
| STOPPED | An already-started run was stopped. It is not a paused run to resume. |
| COMPLETED | All cartridges passed the applicable execution/publication contract. It is not a general correctness certificate. |

Core state, execution claims and control records are authoritative for execution.
The attempt ledger explains what happened but does not replace the core state
machine. Claims/fencing prevent stale execution owners from simply advancing
the same run. The scheduler adds the instance-wide execution slot.

## Committed results and handovers

### Two different completion contracts

`result_file_v1` is the recommended committed-result mode. A text-only DONE reply
cannot satisfy its publication and dependency checks. `legacy_text` and retained
pi.5 records preserve their earlier text-result behaviour; they do not gain
committed-file guarantees retroactively. Text-only mode uses one attempt per
explicit Start. Older runs remain readable without rewriting their history.

### What a committed-result attempt must establish

Before a result-file cartridge advances, the executor verifies the applicable
facts in this order:

1. **Inputs:** re-read declared producer handovers and verify committed identities
   and hashes. Copy declared file inputs only after comparing their recorded
   length and SHA-256 with the actual source bytes.
2. **Worker identity:** the response/session must belong to the expected attempt
   and selected model. The session must have a saved final answer with normal
   completion, not an output-limit/error finish.
3. **Required outputs and closing signal:** declared output files must exist as
   acceptable regular files. The final answer must end with the supported DONE
   object. A final HALT object stops the attempt without automatic retry.
4. **Publisher evidence:** read this attempt's strict UTF-8 JSON result. Its byte
   length and SHA-256 must match the latest receipt emitted by that attempt's
   Magazine publisher tool. A file merely appearing at the expected path is
   insufficient.
5. **Envelope and declarations:** validate schema and exact run, cartridge,
   attempt and session identities; validate the payload/profile when declared;
   require all outgoing handover selectors.
6. **Commit:** archive the saved session, copy the result into Magazine-controlled
   handover storage and write the commit marker. The marker binds identities,
   result hash and archived-session hash. Save the committed ledger state.
7. **Read back:** re-read the committed payload, marker and archived session and
   verify their recorded bytes/hashes/identities before returning success to the
   sequencing core. A consumer re-verifies committed evidence before using it.

Strict JSON rejects duplicate keys and non-finite constants. The publisher's
serialized result is bounded to 1 MiB; session archival is bounded to 32 MiB.
These checks have classified failure paths rather than truncating a result into
something that appears valid.

### Value handovers

A consumer declares an earlier producer and a JSON pointer, for example:

```yaml
handover:
  - from: inspect
    select: /result
    as: requirements
```

Magazine resolves that pointer from the producer's **committed payload**, after
checking the committed record. It supplies the named value to the consumer's
fresh prompt. It does not paste every earlier conversation into the next chat.
Pointers can select dictionary keys or list elements using the supported pointer
rules. Missing values fail clearly rather than becoming an invented summary.

### File handovers

An earlier step declares an output path; a later step declares that producer/path
as an input:

```yaml
# Under the producer cartridge:
artifacts:
  outputs: [sample.txt]

# Under a later consumer cartridge:
artifacts:
  inputs:
    - from: build
      path: sample.txt
```

These are field examples, not a complete importable manifest. Paths are relative
to the Magazine-managed cartridge output/input areas, not arbitrary host source
paths. The full workflow must define the producer before the consumer.

For a declared input, Magazine finds the recorded successful output, opens the
actual regular file without following links, streams a copy while hashing it,
compares length/hash, then publishes the consumer's input copy. If the source is
missing or changed, the copy is not delivered as a verified input and progression
fails. Unverified failed-attempt candidate files are never silently promoted to
trusted handovers.

### What “verified” does not mean

The publisher's `verified`, `checks`, `files` and `note` fields are the worker's
recorded claims. The publisher validates their supported shape; **it does not
execute the listed checks**. Even a profile requiring nonempty checks for a true
verified flag cannot prove those checks were honestly or adequately performed.

Output presence, a valid receipt and matching hashes establish identity and
publication integrity. They do not prove that a program is bug-free, a file's
contents are correct, or the user's task has been fully satisfied. Read actual
tool output and run task-specific independent tests. These mechanisms are not a
cryptographic trust boundary against an adversarial worker with shell/write
tools running under the same OS authority as Magazine.

## Retries and recovery

Recovery is bounded, recorded and local to the current cartridge; it is not an
unlimited autonomous replanner.

New result-file workflows default to **up to five total attempts per cartridge**
and **1,800 seconds per attempt**. The validated policy can reduce the attempt
allowance or specify a time allowance within its supported range. An explicitly
configured smaller `timeoutSeconds` remains an upper cap. A slow provider is
not automatically assigned a different model or a larger context window.

Every retry gets a fresh session, a new attempt identity and its own full time
allowance. Existing attempts remain in the ledger and retained chats. Five is a
hard total-attempt ceiling, not five new attempts every time the user presses
recovery.

The retry decision distinguishes eligible completion/publication failures from
hard integrity/storage/control failures. Missing publication, a truncated reply,
missing DONE/output and eligible worker failures can enter the supported
recovery path. Explicit model HALT, STOP, cancellation, exhausted allowances,
hard identity/model/tool integrity failures and unsafe termination do not become
unbounded retries.

For an admitted retry, the executor passes compact failure context and bounded
candidate-file metadata, not the whole previous chat. A `retry_safe` repair path
can repair the task in its designated output area. Otherwise the recovery prompt
is verification/publication-only: inspect useful existing candidates, recheck
them and publish the observed result, or HALT if edits are required. This prompt
boundary is not an OS-enforced write ban for a shell-capable worker.

Repeated non-completion failure fingerprints without verified progress can
stop retries early. Recoverable completion omissions have a distinct policy;
the hard ceiling still applies. Local storage retries are separately bounded
and do not consume fresh model attempts. Permission, read-only storage or
no-space errors fail rather than being retried indefinitely.

After HALTED, inspect Ledger and Chats. **Prepare recovery** is the explicit core
recovery operation; **Start** is still separate. Successful earlier checkpoints
are preserved and checked, not blindly rerun. Recovery is not a way to clear STOP
or erase used attempts.

## Antiloop

Antiloop observes structured assistant tool-call batches and completed tool
results forwarded by the worker. It does not infer a loop from slow generation,
reasoning length, silence or repeated words in ordinary text.

The default policy warns at three identical call/result repetitions, stops at
five, stops at three repeated identical errors, and detects a short cycle
repeated three times. History is bounded to 64 representative events. The policy
can be declared within validated limits; the run records whether it is enabled
and when its choice is frozen.

Call IDs are deduplicated. A parallel tool batch contributes one representative
completed event, rather than counting every parallel result as independent
repetition. Signatures cover tool identity, argument hash, result hash and error
state. A recognized successful mutating tool resets repetition history unless
it explicitly reports no change. Identity tracking is bounded too; its limit is
recorded, not presented as unlimited observation.

Confirmed loop evidence triggers abort/termination handling and records the
reason/signatures. It can enter the ordinary eligible retry decision; Antiloop
does not bypass attempt limits. Unconfirmed abort/termination is a hard safety
failure. It is a heuristic for the implemented structured patterns, not a proof
that every possible useless or harmful loop will be detected.

## STOP and uncertain termination

STOP is a persistent control decision, not a temporary pause in a browser tab.

- For a run still waiting for its **first** scheduler turn, **Remove from queue**
  removes admission and leaves it READY.
- For a run that has entered execution, STOP requests cancellation and worker
  termination. A run waiting after an earlier committed cartridge is already an
  entered run, so this STOP is permanent for that run.
- A started STOPPED run cannot be resumed through Prepare recovery. Create a new
  run when further work is wanted, after reviewing existing effects/results.
- Magazine does not equate an abort request with confirmed process termination.
  If it cannot confirm termination, that uncertainty blocks unsafe continuation.

The dispatcher handles the race where a worker exits between a return-code check
and a cleanup signal, so cleanup does not replace a valid result with a spurious
process-not-found error. STOP cannot undo already executed tools, external side
effects or committed files. Review those effects before starting new work.

## Scheduling and restart behaviour

There is one execution slot **per plugin service process**, not a distributed
lock across arbitrary separate Magazine instances.

Sequential runs one project until it completes, stops or halts. Round Robin
rotates FIFO among eligible projects only at a committed cartridge boundary.
Attempts, bounded recovery and commit for the active cartridge finish before
rotation; the scheduler does not preempt a model response. Changing mode applies
at a subsequent boundary, not by cancelling the current job.

Start durably admits the run before dispatch. `scheduler-v1.json` records mode,
current/queued entries and restart checkpoints. Atomic write/read-back failures
block dispatch, and a corrupt journal is retained with a diagnostic. A queued
run that has not entered the core can still be core-READY while the dashboard
shows QUEUED.

On service shutdown, queued/current entries are retained for explicit action.
On startup, retained entries require a new **Start**; an interrupted entered run
may first need **Prepare recovery**. Merely reopening the page does not resume
them. An orphaned ledger attempt is reconciled as interrupted, preserving its
available evidence rather than claiming success or silently continuing it.

Persisted browser/server selection remembers which run was viewed. Selection
generations prevent a late detail response from overwriting a newer choice.
This UI protection is separate from execution fencing. Polling reads status;
it does not invoke Start, Stop or queue reordering.

## Ledger, chats and token accounting

`ledger.json` records attempt identity, exact model, phase/status, timestamps,
errors, retry decisions, publisher/commit evidence, relevant file metadata,
loop observations and available usage. It is saved atomically. Some activity
notes are bounded; it is not an unbounded historical event journal.

The Ledger tab selects one cartridge, preserving all available attempts instead
of hiding detail in multi-step summaries. Chats reads the associated retained
sessions across projects and retry attempts. It is a viewer, not a remote prompt
entry point. Private typed reasoning and assistant thinking sections are omitted
from that viewer. Tool text remains untrusted display data, not executable UI
instructions.

History reads use bounded pages and managed server-resolved session identities.
Client-supplied arbitrary paths or owner IDs cannot choose a source file. Large
or unsupported records fail clearly, and partial final JSONL lines wait for
completion. Generation/cursor checks detect replaced or truncated sources.

Token totals come from persisted Pi usage, not estimates based on message length.
For the supported accounting contract:

```text
total = input + output + cache read + cache write
```

Reasoning usage, where reported, is a subset of output, not another amount to
add. All-zero provider usage is treated as unavailable. Incomplete/unreported
attempts remain marked incomplete; they are not silently counted as zero-cost
successful work. Run totals and all-project totals are reported aggregates, not
a price calculator, context-limit guarantee or comparative model benchmark.

## Results, JSONL and redaction

The Results tab distinguishes the committed payload from the model's final
reply. Existing per-cartridge/canonical exports remain available. The result ZIP
also carries the Markdown/JSON ledger and available committed handovers under
their defined export layout. Finished Work export has its own publication
status; failed export is not evidence that inference never happened.

**Download Run JSONL** exports the selected retained run as one UTF-8 JSONL file:
`magazine-run-<run_id>.jsonl`. Each line is a JSON object. Export envelopes contain
the run, cartridge association where established, export sequence, kind, source,
available timestamp and the complete retained record subject to redaction.
Retained prompts, responses, tools/results, errors and failed/recovered sessions
are not replaced with summaries. Protocol, ledger and handover records are
included where present.

Ordering preserves original session line order; sequential sessions use their
recorded start timestamps with deterministic tie-breaking. The exported
`sequence` is a **download index**, not a fabricated original global event
counter. State/control/claim records are labelled snapshots. Magazine cannot
reconstruct overwritten lifecycle transitions, discarded partial stream chunks,
deleted records or information that the provider never captured. Ambiguous
legacy session-to-cartridge matches retain their identity/candidates rather
than inventing a match.

Completed, halted, stopped and partial runs can expose their retained JSONL. A
running file's incomplete tail is not parsed as a fake complete event. Malformed
committed records, unsafe linked sources or a source changing during snapshot
export cause a clear error/retry outcome rather than a misleading mixed export.
Export does not acquire an execution claim or change sequencing/persistence.

Structured credential fields, bearer credentials, recognized secret assignments,
credential-bearing URL components and known legacy secrets are redacted under
the existing rules. Runtime credential files, provider configuration and the
environment are not export sources. **Redaction cannot reliably recognize an
unlabelled secret in arbitrary tool output.** Inspect downloads before sharing;
the Chats viewer's omission of thinking is not a promise that the broader JSONL
export has identical filtering semantics.

See [the detailed JSONL contract](MODELS-AND-JSONL.md) for source/ordering limits.

## Deletion and restore

Template deletion and saved-run deletion are different operations. The current
confirmation identifies the item and offers **Confirm deletion / Cancel** without
typed IDs. This UI simplification did not remove server checks.

Deleting a template preserves saved-run snapshots and actual user project
files. Deleting a saved run retires its managed records, sessions, ledger,
committed results and that run's Magazine-owned workspace. It keeps the reusable
template, original Input ZIPs, Finished Work exports and actual project files
outside that managed workspace.

Both operations move managed content to recoverable Magazine trash. Active or
queued runs, unresolved scheduler persistence, unconfirmed termination and
attempts still marked running block deletion. The server rechecks immediately
before mutation; a stale preview is not sufficient authority. Durable intent
records make interrupted deletion visible/restorable, and failed moves attempt
rollback. An unsupported cross-filesystem move safely refuses rather than
copying/removing arbitrary project data.

Restore refuses ID/destination conflicts and does not start a run. There is no
permanent purge control. Trash resides with managed state and is not an
independent backup against disk loss, corruption or manual deletion.

## Storage, host access and compatibility

Magazine inherits Pi's host middleware/plugin permission boundary. It creates
no independent authentication service, owner secret, import password or browser
login. The supported shared-operator deployment is not a multi-tenant isolation
system. Legacy owner-labelled directories remain storage partitions in that
same instance, not proof of separate authenticated browser users.

Run state, claims/control, scheduler journal, workflow library, sessions,
handovers, trash and relevant workspace files need persistent, supported storage.
Atomic writes, read-back, no-follow reads and hash checks prevent specific
partial-write, identity and stale-input failures. They do not make filesystem
corruption impossible. Different subsystems have different flush/journaling
behaviour; the recorded process/restart tests are **not a power-loss guarantee**.

The demonstrated import repair uses Linux-backed internal Magazine state while
preserving parent Pi/provider settings. Do not remove the current mount: that
can reveal a stale old directory and look like lost work. Copy/migration must
be idle, scoped, inventory-verified and reversible; see [the migration guide](LINUX-STORAGE.md).
Back up current state, private config and applicable workspace data together.

Activation checks the exact host/SDK pair in
[`compatibility.json`](../magazine-pi/compatibility.json), and the worker checks
the resolved SDK again. The shipped table lists 0.96.1/1.0.2, 0.115.0/1.1.0 and
0.120.0/1.5.2 with evidence levels. An exact-pair operator acknowledgement exists
for unverified combinations; it is not evidence that an arbitrary future version
works. Current acceptance on 0.120.0/1.5.2 is recorded separately from historical
table text. Python and compatible storage are installation prerequisites, not
things that an imported workflow is allowed to reconfigure.

## Evidence and limits

The [import repair record](IMPORT-REPAIR.md) records **906 automated passes and
one skipped legacy-SDK case**, native lifecycle checks and the separate real-model
outcomes. The 21 November confirmation change records **14 installed-interface
checks and 67 related backend regressions**. These are dated results, not tests
rerun merely because this documentation was written.

Scripted-provider tests exercise real service/SDK/storage mechanisms with
deterministic model replies. They do not measure LLM quality. Real acceptance
also retained provider/output-limit/CUDA failures; a COMPLETED real run had a
semantic omission documented rather than relabelled as a perfect result.

The screenshots in the visual guide use the real browser client with synthetic
documentation records and generic example model IDs. They demonstrate where
controls are and what recorded data looks like, not that those example jobs
were executed. No real inference or production configuration change is required
to make the documentation images.

The most important boundaries are:

- No model substitution, but model availability and tool reliability still vary.
- Checks establish the recorded contract, not arbitrary semantic correctness.
- Fresh sessions and allowlists are not a hostile-code sandbox.
- STOP cannot reverse external side effects.
- Restart retention is not unattended resumption or proven power-loss durability.
- JSONL exports retained evidence, not missing or overwritten history.
- Shared operator access is not per-user data isolation.

## Implementation map

These are the principal source locations behind the explanations above. Follow
the named functions for the exact version rather than treating prose as a new
execution specification.

| Mechanism | Source |
| --- | --- |
| Native plugin, activation/SDK gate and route boundary | [`index.mjs`](../magazine-pi/index.mjs), [`compatibility.json`](../magazine-pi/compatibility.json) |
| UI tabs, controls, read-only chats and stale-response protection | [`client/entry.mjs`](../magazine-pi/client/entry.mjs) |
| Run operations and workspace routing | [`service.py`](../magazine-pi/service.py) |
| Manifest, policy, closing-object and pointer rules | [`protocol.py`](../magazine-pi/protocol.py): `parse_manifest`, `recovery_policy`, `judge`, `select_pointer` |
| Model selection precedence | [`model_selection.py`](../magazine-pi/model_selection.py) |
| Fresh attempts, checks and bounded recovery | [`executor.py`](../magazine-pi/executor.py): `run_cartridge`, `one_attempt`, `effective_timeout` |
| SDK session, active-tool checks and publisher receipts | [`worker.mjs`](../magazine-pi/worker.mjs) |
| Worker lifecycle, timeout and termination | [`pi_dispatch.py`](../magazine-pi/pi_dispatch.py) |
| Verified inputs, commit/archive hashes and handover validation | [`handover.py`](../magazine-pi/handover.py): `prepare_files`, `validate_envelope`, `commit_attempt_result`, `committed_payload` |
| Attempt record and retry admission | [`ledger.py`](../magazine-pi/ledger.py): `decide_retry`, `save`, `summary` |
| Structured tool-loop detection | [`antiloop.py`](../magazine-pi/antiloop.py): `LoopPolice` |
| Execution-slot journal, rotation and restart retention | [`scheduler.py`](../magazine-pi/scheduler.py) |
| Retained chats, deletion and restore guards | [`ui_support.py`](../magazine-pi/ui_support.py) |
| Usage completeness and aggregation | [`tokens.py`](../magazine-pi/tokens.py) |
| Whole-run JSONL, source checks and redaction | [`run_export.py`](../magazine-pi/run_export.py) |
| Input ZIP/Finished Work publication helpers | [`beta5_helpers.py`](../magazine-pi/beta5_helpers.py) |
| Existing sequencing, state, claims and STOP/recovery | [`magazine/`](../magazine-pi/magazine/) |

## Interrupted execution reconciliation in ui.4

See [restart recovery](RESTART-RECOVERY.md) for the explicit non-executing action,
PID-incarnation extinction checks, surviving-worker refusal, claim fencing,
retryable HALTED persistence, STOP settlement and deletion receipt. Start on
an orphaned RUNNING run still refuses inference. Active-worker STOP retains
its existing cancellation path. Provider cancellation and external operation
completion are separate from owner extinction and are not invented.

The [validation record](RESTART-RECOVERY-ACCEPTANCE.md) reports the current suite.
