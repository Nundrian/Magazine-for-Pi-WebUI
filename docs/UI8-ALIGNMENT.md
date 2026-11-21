# Open WebUI alignment — 0.1.0-pi.8-ui.2

This UI release aligns the native Pi Magazine presentation and workflow journey
with the owner's Open WebUI examples. It retains the seven panels and the
individual-cartridge ledger explicitly requested by the owner.

## Reference and visible changes

The source reference is the owner's [0.3.0 annotated walkthrough](https://github.com/Nundrian/Magazine/blob/main/docs/images/magazine-simple-0.3.0-walkthrough.webp)
and the [workflow/setup/run/history illustrations](https://github.com/Nundrian/Magazine/tree/main/docs/images).
The walkthrough combines actual owner screenshots; the SVG panel guides are
illustrations. Their labels are visual references, not executable instructions
or evidence of Pi API compatibility.

- Muted blue panels, white body text, blue primary buttons, section borders,
  readable spacing and larger controls replace the inherited cyan appearance.
  Appearance offers Magazine dark, Magazine light and Follow Pi theme. The
  choice persists in the browser and affects only this plugin.
- The selected-run header is smaller, has a progress bar and separates run
  identity, reported tokens and actions. It is hidden until a run is selected.
  Run details and export help remain expandable; STOP gating is unchanged.
- Workflows uses an explicit **Load your workflow** button. Merely selecting a
  different workflow invalidates the old unprepared setup and cannot Prepare it.
  Imports and previously loaded selections remain restorable without inference.
- Setup groups model choice, ordered cartridges/prompts, bulk assignment, the
  optional planner and tools/protection. Long workflow step lists can collapse;
  individual model selection and Apply suggestions open them for editing.
- **Prepare workflow** opens the saved READY run in Overview. **Start workflow**
  is a separate action. **Previous work → View previous work** explicitly opens
  an earlier run without starting it. Names, dates, statuses and run IDs identify
  saved choices, including otherwise identical prepared workflows.

Explicit panel navigation starts at its heading; polling does not reset scroll.
Initial read-only discovery tolerates a brief native plugin route-activation
delay. Prepare, Start, Stop and other mutations are never automatically retried.
The invalid JSON parser message from a plain-text startup 404 is replaced by a
bounded read retry or an actionable availability/sign-in message.

## Ledger and runtime preservation

The ledger's renderer, facts, individual selector, Previous/Next, status filter,
attempt expansion, technical JSON and exact-attempt Chats links are retained.
No grouped-ten or multi-cartridge ledger was introduced. New colours and spacing
apply to the existing detail format. Full retained Magazine history, live polling,
private-thinking omission, Results and recoverable deletion are unchanged.

Only `client/entry.mjs`, the manifest/version and the service version marker
change in runtime code. The execution engine, scheduler, worker, 1,800-second
attempt default, five-attempt ceiling, handover verification, prompts, model
provider configuration, tools and reasoning/context settings are unchanged.
The release builder verifies the original 29 engine modules and beta.5 helper.

## Verification

Host 0.120.0 / SDK 1.5.2 was exercised in a disposable real Pi host with synthetic
workflow records and a loopback scripted provider. No real local LLM was invoked.
The before UI passes its original 44 browser checks on this host.

The aligned UI passes **56 checks in each of Chromium and Firefox**, covering
the new flow and appearance plus the retained ledger/history/results/manage
journey. Tests include first/middle/last of 145 cartridges, five failed attempts,
an unstarted cartridge, full multi-project history, switching live and old chats,
keyboard access, small viewports, refresh persistence, exact model-plan Apply,
Prepare without Start, and a refused Start being sent exactly once.

An injected plain-text initial 404 is expected and must recover before the
journey continues. Invalid-plan HTTP 400 and the explicit Start-refusal probe
are verified expected failures; other HTTP/console errors fail the test.
The isolated contexts block host service-worker interception, as in the prior
candidate test. Normal installed-host loading is checked separately at deployment.

Eight affected unit cases pass: import-free ESM/mount, Node syntax for client,
index and worker, 200-cartridge selection, committed-result text, declared-model
selection and empty/live token presentation. The unchanged backend's complete
suite was not rerun. `UI8-REPORT.md` is the historical ui.1 verification record.

Before/after fixture captures and exact browser reports are retained outside the
public ZIPs in `evidence/before-firefox`, `evidence/release-chromium-final` and
`evidence/release-firefox-verified`. Final visual review corrected the transition from a
long setup page to Overview; the regression checks the actual page heading,
rather than merely the sticky run header. Scoped text-shadow removal also keeps
the reference appearance independent of Pi's decorative theme effects.
One parallel Firefox pass encountered a host reload `NS_ERROR_FAILURE`; the
unchanged assertions passed on the sequential rerun. Intermediate failures are
retained with their diagnostics. The build verifies ZIP reproducibility, CRC,
source parity and public-content scanning. Live-model reliability remains outside
this UI change's acceptance.

## Installation and rollback

Install only at an idle Magazine checkpoint, using the native Pi installer and
the existing data directory. Back up the installed plugin/config and retained
Magazine data first. Keep the existing provider configuration, mounts and other
plugins. The separately supplied installation kit describes Python and the
Docker data layout; no private configuration is in either release ZIP.

For this deployment, update/unload only `magazine-pi`, not all Pi plugins. No
container recreation or other-service restart is needed. Reopen Magazine and
check version 0.1.0-pi.8-ui.2, the new explicit loading control, all panels and
existing model IDs. Do not start a real workflow just to check styling.

Rollback uses the exact backed-up ui.1 plugin and its config through the same
Magazine-only procedure, at idle time. Data and saved runs require no migration.
The deployment report records the actual update and final installed checks.
