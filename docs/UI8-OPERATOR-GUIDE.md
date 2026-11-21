# Magazine 0.1.0-pi.8-ui.2 operator guide

For this release, see [UI8-ALIGNMENT.md](UI8-ALIGNMENT.md): choose a workflow,
click **Load your workflow**, review models/tools, then **Prepare workflow**.
Preparation opens its READY run in Overview; **Start workflow** remains explicit.
**Previous work → View previous work** opens an earlier saved run. The ledger
format and complete Chats view described below are retained. ui.2 has isolated
UI acceptance on host 0.120.0 / SDK 1.5.2; the older ui.1 acceptance/deployment
notes below are historical, not current installation status.

This is a separately packaged, uninstalled candidate for native plugin
`magazine-pi`. It does not replace Magazine Simple or change Open WebUI.

## Find your work

Open **Plugins → Magazine**. The persistent header shows the selected run,
status, current cartridge, progress, reported tokens and duration. Start, Stop,
Prepare recovery and Refresh status remain available there. Expand Run details
for exact models and execution evidence. At smaller widths the header uses
compact totals and the navigation wraps.

Use **Overview** to select saved work and inspect scheduling. **Workflows** imports
or previews templates. **Set up** assigns models/tools and prepares a new run.
**Ledger**, **Chats**, **Results** and **Manage** each open a single primary panel.
Opening a panel or restoring a selection never starts work.

## Optional GPT/Claude suggestions

In **Set up**, expand **GPT/Claude suggestions**. Manual shared, independent and
bulk assignment remain available without a plan.

1. Review the sensitivity notice and consent before building/copying the brief.
   The brief contains complete cartridge prompts, order, requirements, actual
   Pi model IDs and available tools. Edit or remove private content before Copy.
   Magazine makes no automatic external AI request.
2. Ask your chosen planner for the JSON contract shown in the brief. Paste its
   response, or import a workflow ZIP containing `model-assignments.json`.
3. Choose Validate. Inspect every before/after assignment and rationale. Choose
   **Apply suggestions** explicitly; validation alone changes no models. You may
   then change individual assignments manually.
4. Choose **Prepare workflow**, review the saved run, then choose **Start**.

The schema is `1.0`, with the exact `workflow_name`, ordered `cartridge_ids` and
`cartridge_models`. Optional `default_model` covers omitted assignments; optional
`rationale` maps known IDs to short strings. Duplicate keys/IDs, unknown fields,
stale identity/order, unresolved models and JSON above 128 KiB are rejected as a
whole. Model strings must exactly match Pi's live catalogue, including provider
prefixes. An Ollama tag absent from Pi is unavailable, with no substitution.
An invalid optional sidecar warns but does not invalidate an otherwise valid
workflow import. Sidecar validation and explicit Apply are separate operations.
The final mapping and manual overrides are stored only in the newly prepared run.

The brief distinguishes model-card claims from local measured reliability. It
identifies Qwen3-Coder-30B-A3B as non-thinking and flags earlier local tool-call
failures. The historical 145-step allocation is not a comparative benchmark.
This UI release does not change providers, reasoning settings or context sizes.

## One cartridge at a time

In **Ledger**, use **Cartridge** to select any individual numbered ID, or use
Previous/Next and the status filter. Exactly one cartridge record appears.
It retains status, exact model, attempt count, reported input/output/cache/total
tokens, completeness warnings, timestamps and recorded duration. Each attempt
has its own expandable errors, retry/HALT decision and technical JSON. Results
shows verified committed deliverables separately from a final DONE reply.
Your explicit cartridge selection survives polling and page reloads.

Choose **View in Chats** on an attempt to select that exact conversation while
keeping the rest of history available. The header's **Open current chat** opens
the active/recent attempt for the selected run. At small widths use Chats in the
navigation to reach the same history panel.

## Full Magazine chat history

**Chats** has a persistent searchable list across retained projects, runs and
retry attempts. Filter by run, model or status. Entries identify run, cartridge,
attempt and model; running sessions have an activity indicator. The scope is
explicitly **Magazine chats**, not unrelated ordinary Pi chats.

Select an old or live chat to read chronological user/assistant messages, visible
tool calls/results/errors, code blocks and final output. Optional visible JSON
is sanitized too. Private typed reasoning and assistant `<think>` sections are
omitted; existing credential redaction also applies. The original retained
session is not rewritten by this viewer. Existing whole-run JSONL export keeps
its historical semantics and should be reviewed before sharing.

A created active session appears before completion or archival. Bounded polling
adds complete records without a writer lock. The Live/Updating label and update
time show activity. Auto-follow can be switched off, and scrolling back stops
automatic following. Switch chats while the worker continues normally. There is
no prompt input, model change or worker-control action in this read-only viewer.
History is rediscovered from retained records after a restart. Missing, empty,
not-yet-created and legacy/unassociated sessions have explicit states.

Each response reads at most 512 KiB / 100 records, with a 4 MiB single-record and
32 MiB conversation limit. Larger/unsupported records fail clearly rather than
reading without bounds. Partial final JSONL lines wait for completion; cursor
generation detects replacement/truncation. Only server-resolved managed session
files with matching identities are readable; client owner/path values are refused.
The pinned host uses shared single-operator authority. Legacy owner directories
are storage labels, not proof of separate browser accounts.

## Delete workflow / delete saved run

Open **Manage**. Workflow templates and saved runs are separate lists with View,
export/download where applicable, and clearly labelled Delete actions. Review
the preview and choose Confirm deletion, or Cancel to keep it. No typing is required. There is no Delete all.

**Delete template** removes only the imported template from the active list;
prepared run snapshots remain. A live dependency blocks deletion.
**Delete saved run** removes the run snapshot, ledger, attempt/session records,
committed results and that run's Magazine-owned `magazine-runs/<run-id>` workspace
from the active lists. It keeps the imported template, Input ZIPs, Finished Work
exports and actual user project files such as `/projects/3D kit`.

Both operations move managed directories into recoverable **Magazine trash**.
Choose **Restore** there to return saved work without starting it. Restore
refuses to overwrite an existing ID. No permanent purge is provided.
Active/queued runs, uncertain scheduler persistence, unconfirmed termination or
an attempt still marked running block deletion, including a final check after
preview. A durable intent makes interrupted deletion visible/restorable; failed
moves roll back. If an external workspace and Magazine trash are on different
filesystems, deletion safely refuses and rolls back rather than copying/removing
project data. Back up/export before deletion when the records matter.

## Idle-host installation and rollback checklist

The delivered ZIPs have not been installed in the owner's host. Installation is
a separate idle-host action. No container restart, Plugins → Reload, provider
edit or updater CMD was performed for this candidate.

1. Obtain the owner's installation decision and confirm no running/queued worker,
   uncertain STOP or pending recovery. Record the installed host/SDK and plugin
   versions. New UI acceptance covers host 0.96.1 / SDK 1.0.2; inherited other
   allowed pairs have not been reaccepted for this UI.
2. Verify `SHA256SUMS.txt`. Back up the installed `plugins/magazine-pi` including
   `config.json`, the entire `<PI_WEB_DATA_DIR>/magazine` tree (including scheduler,
   library, sessions and trash), and any externally configured Magazine workspace.
   Preserve mounts, credentials, other plugins and original project files.
3. Extract `Magazine-Pi-0.1.0-pi.8-ui.1-plugin.zip` into a temporary directory.
   Use the host's established Magazine-only uninstall/install procedure from
   INSTALL.md at idle time. Do not invoke a global plugin reload. Preserve the
   existing `config.json`, especially workspace/agent paths and `allowedTools`;
   the archive intentionally contains no private config.
4. Confirm version `0.1.0-pi.8-ui.1`. Before inference, open an old run/results and
   its complete chat history; use first/middle/last ledger choices and a failed
   retry. Confirm refresh preserves selection and does not start anything.
5. On a disposable imported workflow, test plan Validate → Apply → manual override
   → Prepare without Start. Confirm absent catalogue models are refused. Test
   confirmation-button deletion and Restore of disposable inactive work, keeping its template
   and actual project file. Confirm an active/queued deletion is refused.
6. If the owner wants production execution acceptance, use a separately approved
   small run. Open its live chat, switch to old history and back, verify visible
   updates, normal progress, STOP/recovery and committed results. This behaviour
   passed with the real isolated SDK and a scripted provider; actual local-model
   behaviour is not yet established for this UI release.
7. Check Firefox desktop/mobile controls and normal host authentication. Host PWA
   service-worker caching was blocked in isolated browser tests and needs a normal
   browser check on any authorised installation.

For rollback, return to an idle/empty-queue state, save new work, and reinstall the
exact previously backed-up rc.3 plugin/config using the same Magazine-only
procedure. Keep runtime data and trash; the old UI cannot restore trash, so restore
needed items with this candidate first. Restore a runtime backup only as an
explicit coordinated rollback because it discards subsequent changes. Do not
roll back to pi.5 and start committed-result runs. Keep Magazine Simple untouched.
