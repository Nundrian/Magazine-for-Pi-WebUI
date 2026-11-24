# Interrupted executions (pi.8-ui.4)

After a service or container restart Magazine never resumes inference by itself.
An interrupted run can retain a RUNNING state even when its saved attempt is
already marked interrupted. Select the run and choose **Reconcile interrupted
run**. This operation starts no inference, performs no provider requests and
does not launch another queued workflow.

Reconciliation requires positive extinction evidence for the recorded Linux
PID/start-tick incarnation, a different runtime generation, no active local
task, and no surviving Magazine SDK worker visible in the PID namespace.
Missing claims, unreadable process evidence, a live process or conflicting
locks refuse recovery with diagnostics. Legacy worker records have no PID;
therefore any visible Magazine SDK worker conservatively blocks this action.

The existing lock acquisition and ownership fence revalidate the full execution
claim. Eligible RUNNING state becomes HALTED, preserving cartridge queues,
completed results, sessions and execution attempt identity. Attempts are marked
interrupted, stale active-worker phase becomes interrupted and only that run's
retained scheduler entry is removed. Durable stages are retryable, including
a crash after the HALTED write and before claim retirement. Repeated successful
reconciliation leaves the core revision and execution identity unchanged.

Process extinction is **not** confirmation of inference cancellation or external
tool completion. The UI reports cancellation as unconfirmed and external
operations as unknown. A pending STOP is settled as NOT_APPLIED/lost-owner,
preserving any actual recorded cancellation evidence. STOP remains permanent;
prepare a new run if needed. Reconciled runs without STOP can use Prepare
recovery and then a separate, explicit Start. Review external work first.

Start on an orphaned RUNNING run reconciles it and refuses execution with 409.
Stop on that run returns a distinct reconciled result, never a fabricated
successful cancellation. Stop for genuinely active workers keeps the existing
durable request, cancellation and task-termination path.

Manage / delete workflows can move reconciled saved work into Magazine trash
after its normal preview and confirmation. Deletion requires a matching durable
reconciliation receipt and rechecks extinction and worker absence. Actual user
project files, workflow templates and unrelated runs are preserved. Trash
restoration keeps the run HALTED and never restarts it.

The host UI's MCP connections are separate from Magazine workers. Worker tools
come from the configured allowlist and explicit worker extensions. A successful
host MCP connection alone does not establish worker tool access; inspect the
worker catalogue and saved attempt active_tools. This repair does not widen
worker tools, change model mappings or alter credentials.
