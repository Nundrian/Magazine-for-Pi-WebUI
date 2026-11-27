# Magazine pi.10.5 provisional corrective candidate

The tested source baseline is d2b456a5fcf94c567d682fff3563475fa664f12b. The new
execution policy targets Pi WebUI 0.120.0 and Pi SDK 1.5.2. Earlier supported
SDKs retain their previous session settings. Linux and real-model acceptance
of pi.10.5 is required before a production upgrade.

## Verification continuation

The 27 November continuation preserved the original candidate archives and
recovered prompt. Linux testing on the existing 0.120.0/1.5.2 image passed
1,066 tests, with the legacy pi.5 upgrade test skipped because it requires SDK
1.0.2. All 21 frontend/catalogue and native-host JavaScript tests
passed. Actual Firefox testing used an isolated Pi host and scripted responses.
It covered configuration, JSON, reasoning, native chats, exports, active and
queued deletion protection, Stop, recovery, template/restore-point management,
and file deletion/restoration across Windows 9p workspace and Linux ext4 state.
An actual disposable-container restart preserved committed work; reconciliation,
deletion/restoration, recovery and catalogue refresh made zero provider requests.

Two confirmed defects were corrected during verification: terminal truncated
responses are read from the SDK event and checked against the saved transcript,
and Manage refreshes its existing status/deletion controls after execution ends.
The restart regression fixture now gates its first cartridge until both runs are
admitted, then asserts its committed-state precondition before killing the service.
It retains all no-resume, queue-order and duplicate-commit assertions.

Live verification also exposed a dispatch-channel omission: the worker emitted
native compaction events, but Python discarded them before the executor observer.
The channel now forwards them, and a deterministic regression reproduces the
previous loss. The C08 fixture now requires offset=1 and limit=180 for all six
absolute input paths, since header-only reads did not exercise native compaction.
Saved compaction records also expose usage separately from assistant messages.
Attempt snapshots now include that reported usage exactly once, retain its
component totals, and mark missing compaction usage incomplete rather than zero.

Scripted evidence does not count as completion of Acceptance A, B or C. Their
real-model, native compaction and provider-mapping evidence is recorded separately
in the final verification report. This is a review candidate, not production
deployment authority. Native provider settings determine outgoing reasoning;
the isolated Ollama profile used an explicit Off-to-none mapping. Existing
production model definitions and reasoning mappings were not modified.

## Reasoning and native compaction

Set **Workflow reasoning** to **Inherit Pi setting**, or choose a level supported
by the selected models. Each cartridge can use the workflow reasoning, select a
supported level, or explicitly inherit Pi. Use the workflow steps selection
controls to assign reasoning to several cartridges without changing their models.

Magazine reads the Pi agent's global settings and its per-model thinking preferences.
A fresh session resolves those preferences using Pi SDK's per-model, global, then
built-in default order. This does not use the current chat's session selection.
Only thinking and compaction fields cross the catalogue worker boundary. New runs
freeze their settings at Prepare. Existing saved runs continue with their existing
worker policy. A change to the prepared model's limits or mapping requires a fresh
preparation. Outgoing provider parameters were traced during isolated live tests.

Compaction uses the SDK settings for the selected model, including enabled, reserve
and recent-context values. It works within the current cartridge session. The ledger
records SDK start and end events when emitted. Token totals include the usage
reported in native saved compaction records, with a separate component in attempt
JSON. Missing native usage leaves accounting incomplete. Compaction does not
provide the committed result or replace the run's required file handover.

## Assignment format

Use **Validate suggestions** and **Apply suggestions** to load a plan into setup.
Edit the allocation independently before preparing and choose **Export setup
assignments** to save what is currently shown.

| Field | Meaning |
| --- | --- |
| `schema_version` | `1.0` for models only or `1.1` for models and optional reasoning |
| `workflow_name` | Exact workflow name |
| `cartridge_ids` | Complete ordered array of unique cartridge IDs |
| `cartridge_models` | Cartridge IDs mapped to exact provider-qualified Pi model IDs |
| `default_model` | Optional fallback that covers cartridges missing from the model map |
| `rationale` | Optional map of cartridge IDs to notes |
| `default_thinking_level` | Optional workflow reasoning in 1.1 |
| `cartridge_thinking_levels` | Optional cartridge reasoning map in 1.1 |

The supported vocabulary is inherit, off, minimal, low, medium, high, xhigh and
max. The model and Pi SDK determine which of these are available. The absence of
a cartridge entry uses the workflow default. Explicit inherit goes directly to Pi
regardless of the workflow default. Schema 1.0 leaves any existing reasoning
selections alone. Unknown fields and compaction controls are invalid.

A Frontier brief includes actual catalogue identifiers and SDK capability data.
Suggestions should use an exact supported level for each assigned model. Omit a
recommendation when capabilities are unavailable. Review prompt content before
sharing. Applying a plan configures setup and requires a separate Prepare and Start.

## Candidate upgrade and rollback

Keep using pi.10.4 pending independent review and the outstanding gates. Once
approved, verify the plugin archive hash, confirm the existing Magazine is idle
and save a protected copy of the installed plugin and its local config. Back up
the Magazine state volume consistently while its controller is idle. Keep the
checkpoint local. Preserve the Pi host, SDK, volumes, mounts, credentials and
workspace paths.

Use the existing supported Magazine plugin update procedure against the verified
Pi installation and exact existing container. If it requires a restart, restart
only the verified Pi service during the idle window. Verify the About/version
display, model catalogue and retained runs before using the candidate. This
candidate requires no production image rebuild or container replacement.

For rollback, stop execution, remove any queued work through the supported
controls and confirm the worker has ended. Restore the exact previous plugin
archive and its private config through the same plugin procedure. Restore state
only from a consistent idle checkpoint and preserve the current state before
doing so. Runs prepared under pi.10.5 carry execution settings that older code
does not apply. Do not start those runs with pi.10.4. Restore any retained runs
deleted under pi.10.5 through its Manage view before rolling back, since the older
trash reader cannot restore pi.10.5's workspace-local records.

## Acceptance workflow setup

The separate acceptance bundle includes 8, 8 and 12 cartridge workflows and a
one-cartridge intentional HALT fixture. Model IDs in the B and C fixtures were
found in Pi's local configuration. Verify them and their reasoning/tool capability
through the isolated live catalogue before running. The archive does not install
or load any model by itself.

- A uses manual model selection, a workflow reasoning override and at least one
  explicit per-cartridge inheritance selection. Use sequential scheduling.
- B uses the 1.0 plan and inherits native reasoning. C uses 1.1, including a
  manual override after Apply. Run them in round robin and verify both model
  transitions from worker and session evidence.
- For C, extract the supplied compaction inputs into the isolated workspace.
  Set appropriate native Pi thresholds in that test profile only. Start with a
  small verified context profile, observe the SDK events and adjust the test
  profile if needed. Do not count a successful result as compaction evidence.
- Exercise interruption/recovery on a safe boundary. Use the separate HALT
  fixture for deletion, restore and verifying the managed workspace across mounts.
  Preserve run exports before deleting any completed test run.
- Enable read and write for the three main fixture workflows. Keep unrelated
  integrations and actual project directories outside the test mounts.

