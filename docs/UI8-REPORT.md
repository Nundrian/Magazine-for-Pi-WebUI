# Candidate verification — 0.1.0-pi.8-ui.1

## Scope and provenance

This is an uninstalled native `magazine-pi` candidate, derived from the supplied
rc.3 WIP source ZIP with SHA-256
`e152f3b3985e9d5d6ce28f6914d783676eff96158938d4131ae23062c9e23653`.
The previously accepted rc.3 publisher/prompt, committed-result display,
declared-model preselection and empty/live token fixes were preserved before
adding the UI features. The original archive was not overwritten. The release
builder verifies the original hashes of all 29 engine modules and beta.5 helper.
Executor/worker, scheduler policy, timeout, attempt ceiling, handover protocol,
token receipts, Antiloop, reasoning and provider configuration were not redesigned.

Changed implementation: `client/entry.mjs`, `service.py`, new `ui_support.py`,
candidate version, focused tests, browser acceptance and release documentation.
The service adds exact plan validation/briefs, pure chat discovery and bounded
session reads, inactive-work deletion/trash/restore and a post-catalogue Start
recheck. It saves plans only in new prepared runs. Existing exports are unchanged.

## Interface and host integration decision

The installed host's native session switch resumes/creates a runtime and uses
its ordinary session index. No supported, authenticated, non-intrusive read-only
integration for all managed Magazine worker sessions was established. The
candidate therefore implements the requested integrated fallback: persistent
full Magazine history, exact-attempt navigation, message rendering and live
incremental polling. It does not invent native session URLs or reindex/resume
workers to display them.

The historical [ledger reference](https://github.com/Nundrian/Magazine/tree/main/docs/guide)
and [interface image](https://github.com/Nundrian/Magazine/blob/main/docs/images/magazine-interface-beta4.png)
were used as visual references only. FreeFirst was attempted first and returned
connector HTTP 429; Firecrawl supplied the fallback reference access. No model
recommendation research or model benchmarking was needed for this UI candidate.

The before capture showed the native vertical layout and per-cartridge rows;
there was no demonstrated ten-item grouping in that baseline. The candidate
retains the detailed record while selecting exactly one individual cartridge.
Seven panels replace the vertical wall without replacing the execution engine.

## Focused automated verification

Final focused pytest run: **80 passed**. Evidence: `evidence/pi8-final-focused.xml`.
It contains all 67 new-feature cases plus 13 directly affected unit/integration
cases. These cover:

- Strict plan JSON, duplicate/unknown/stale assignments, exact catalogue identity,
  optional default, sidecar import warnings, explicit validation and new-run
  persistence/manual override without rewriting old records.
- Multiple projects/retries, failed/missing/empty/legacy chats, full chronology,
  private-thinking omission, credential redaction, bounded pagination, partial
  tails, rotation/truncate-regrowth, and rejection of unsafe paths/symlinks,
  hard links and mismatched session identities.
- Inactive deletion, active/queue/termination guards, concurrent queue and Start
  races, injected move failures, rollback, actual-project preservation, template
  independence, restore collision refusal and interrupted-delete recovery.
- rc.3 committed result text, model preselection, empty/live token presentation,
  prompt prefix, import-free ESM/syntax, 200-cartridge selection, sequential value
  and file handovers, verified saved results and STOP preserving prior results.

These tests use synthetic retained data and scripted/fake providers. They are
not local LLM reliability measurements. Previously accepted complete suites were
not rerun. The pytest environment emits an inherited fixture-loop-scope
deprecation warning; it does not affect these passing tests.

## Isolated real-host browser acceptance

An independent tester authored the new feature and browser cases and exercised
the baseline/new backend paths. The final expanded browser checks were completed
by the primary agent. `scripts/pi8_ui_acceptance.py` reuses `real_host_ui.py`'s
native plugin-opening helper and `real_host_smoke.py`'s actual host lifecycle,
with a loopback scripted provider and fresh temporary storage. It is never
pointed at the owner's installed host. The old `real_host_ui.py` main remains the
historical rc.3 layout test; use the new script for this candidate's panels.

Host **0.96.1**, SDK **1.0.2**, Linux/Docker: Firefox and Chromium each passed
44 checks, including
145 individual choices, first/middle/last, five failed attempts, pending work,
exact-attempt full history, switching old/live chats while the worker progresses,
plan consent/Validate/Apply/override, committed results, delete/restore, keyboard
navigation, reload persistence, retained rationale, cleared stale errors and
390 × 844 layout. Both reports explicitly assert the intentionally refused
invalid-plan HTTP request. Final reports:
`evidence/pi8-firefox-release/independent-ui.json` and
`evidence/pi8-chromium-release/independent-ui.json`.

Reproduce in an isolated prepared host with the browser test dependencies:

```sh
python scripts/pi8_ui_acceptance.py --host-root /path/to/isolated/pi --browser firefox --out evidence/pi8-firefox
python scripts/pi8_ui_acceptance.py --host-root /path/to/isolated/pi --browser chromium --out evidence/pi8-chromium
```

Use separate ports or run browsers sequentially. Do not use these scripts against
an existing production data directory. Before/after PNGs and JSON reports are
retained outside the public ZIPs. The baseline capture used synthetic work on
the real isolated host. Final UI screenshots likewise contain fixture data.

## Corrections and remaining verification limits

Early isolated checks found and repaired a detached-results mount reference,
chat-list refresh/deep-link races and a mobile sticky header that occupied too
much space. Backend review added a Start/delete await-boundary recheck and
durable interrupted-delete intent/restore tests. Failed intermediate reports
were retained rather than presented as acceptance.

The host PWA service worker produced unrelated lazy-asset cache errors in an
ephemeral host. Final plugin browser contexts block service-worker interception;
normal host PWA caching remains untested. Chromium also emits a resource-error
console message for the deliberately invalid plan's HTTP 400: the final test
verifies exactly that response and fails on any other HTTP/console error.

No actual local LLM was invoked for this change, even though the owner later
authorised test inference. Real model behaviour was unnecessary to verify these
features. No installed plugin was updated, no user run was started/resumed, and
no production service/provider/model/permission setting was changed. Production
idle installation, real-model execution and the other inherited compatibility
pairs remain untested for this UI candidate. The inherited compatibility table
is an activation allowlist, not new acceptance evidence.

The read-only viewer intentionally bounds large records and displays text/tool
activity rather than binary images or private reasoning. Host access is shared
single-operator authority, not a new multi-account security layer. Recoverable
deletion uses same-filesystem renames; an external workspace on a different
filesystem safely refuses and rolls back. See the operator guide for backup,
restore and the authorised idle-host acceptance/rollback checklist.

## Release gates

The deterministic builder verifies original core/helper hashes, CRC and source
parity. Two successive builds must produce identical ZIP SHA-256 hashes.
`scripts/scan_public_release.py` scans the exact extracted source ZIP and both
archives for forbidden runtime/private paths, supplied private identity values
and common credential/private-key patterns. The scan is bounded evidence, not
an exhaustive guarantee for every possible secret format. Reports, screenshots,
private configuration and runtime records are excluded from both archives.
`dist/release-manifest.json`, `dist/SHA256SUMS.txt` and the external release
verification reports record the concrete results.
