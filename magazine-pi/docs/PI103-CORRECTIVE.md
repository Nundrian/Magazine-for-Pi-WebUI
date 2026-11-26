# Magazine for Pi WebUI 0.1.0-pi.10.3 candidate

Magazine runs a workflow as a sequence of cartridges, each in a fresh Pi session.
Ordinary workflows can omit both **requires** and **project_checkpoint**. They
use the existing model routing, handovers, queues, ledger and recovery.

## Checkpoints

Use **project_checkpoint** with **result_file_v1** handovers. The checkpoint
folder must already exist inside an authorised worker write location. A new
workflow starts with no checkpoint state file. Write a regular JSON report with
the cartridge's step_id and status DONE, call **magazine_checkpoint**,
publish the result, then return DONE normally. The tool call validates the
report. Magazine changes the project state only after it verifies the published
result and saved conversation. Reports and state files are limited to 1 MiB each.

If a service interruption occurs during publication, reconcile the interrupted
run, prepare recovery, then start it explicitly. Magazine attempts to finish
the same validated attempt. If the journal, report, state or handover cannot
be verified, Magazine refuses to advance. Keep the retained files and review the
reported cause before attempting recovery.

Existing pi.10.2 project checkpoint history lacks the evidence required for
automatic migration. Preserve the old run and its results. To carry accepted
work forward, independently review the exact state file and baseline cartridge
report. Prepare a continuation with **baseline_step** and **baseline_sha256**
set to the previous cartridge ID and the SHA-256 of the reviewed state file.
The baseline report must match the hash recorded in that state. Start the
continuation at the next verified cartridge.

## Optional host connections

Omitting the operation list exposes only the service's read-only connection
probe. Declare project IDs for project operations and choose individual
operations. Job and operation IDs must come from receipts for the same cartridge.
Register or create projects in the owning application before declaring them in
Magazine. Mutations continue to use the owning application's authorisation.
If a request might have started work, review its recorded receipt before any
resubmission.

## Finished Work

Magazine keeps the canonical results and its own export snapshot. Copies sent
to the browser or chosen folder redact detectable credentials. An identical
folder export is reused. When folder delivery fails, choose **Download ZIP**.
The browser decides where to save a requested download.

## Installation and rollback

This archive is a test candidate. Before any production installation, validate
it with the supported Linux Pi WebUI and SDK, required Landlock confinement,
Firefox, and the existing mounted project and export folders in a disposable
test run.

Once those checks pass and the owner approves installation, verify both ZIPs
against the supplied SHA-256 checksums. Back up the current Magazine plugin,
its configuration and run data, including the matching project checkpoint
files, with the Magazine queue idle. Install the plugin ZIP using the existing
Pi WebUI plugin installation procedure. Verify the displayed version, then
exercise a small disposable workflow before resuming normal use.

To roll back, use an idle queue and reinstall the backed-up plugin. Keep all
candidate run data and project snapshots. Runs started under extended protocol
3 require pi.10.3 or a compatible successor. Use the backed-up run and project
snapshots together if resuming an older extended run. Restore them only with
owner approval because restoration can discard work done after the backup.
