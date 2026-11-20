# Native interface styling — pi.3-rc.1

Historical styling acceptance below. Pi.5-rc.1 retains these native theme styles
while intentionally removing credential controls and exposing direct ZIP import.
Current behavior and focused browser results are documented in SIMPLIFICATION.md.

This candidate refines Magazine's presentation, not its execution or workflow.
The styling is based on the installed Pi Web UI 0.96.1 stylesheet and theme
loader, not assumptions about other similarly named products.

The view now uses native --text/--text-dim, --bg-elev/--bg-elev2, --border-soft,
--accent/--accent-soft, semantic error/STOP colors, --sans and --mono tokens.
Controls follow Pi's 8px radii and compact 13px typography. Sections use light
dividers instead of enclosing card borders. Reduced margins/padding, wrapping
action buttons, bounded fields and visible keyboard focus retain a compact
desktop view and usable narrow layouts. No icon library or CSS dependency is added.
Every selector is scoped to .mag; no host stylesheet/theme is changed.

Every existing DOM control, label, disclosure and event handler remains exactly
as in validated pi.2. The release verifier strips only the one embedded style
block and requires the remainder of the client file to match. All other runtime
code/dependency files must remain byte-identical, including the 29 original core
modules. No authentication, tool permissions, API, worker, ownership or data
format changes are permitted by this styling acceptance.

## Tests and evidence

- Eight complete native UI cases pass across Firefox/Chromium × dark/Ayu Light
  × desktop/mobile. Test-host snapshots retain actual SDK JSONL, canonical states
  and results. The provider is deterministic, not a live LLM quality test.
- All controls are exercised, including import, model/tool selection, READY,
  Start, active STOP, explicit failure recovery, history, export and actual downloads.
- Native CLI upgrade from pi.2 preserves configuration and existing READY data.
  Release checks prove behavior and non-client runtime identity separately.
- Final checks use extracted public source and plugin ZIPs, not private helpers.
  Staged GitHub source and both final ZIPs are scanned for excluded private paths,
  known supplied private credentials and common private-key/token patterns.
- Previous accepted pi.2 archives/checksums and the 622-test evidence are retained.
  No full backend regression or live Bonsai rerun is justified by CSS-only changes.

Screenshots and detailed reports stay in the local development evidence tree,
not in public archives. Current artifact checksums are in SHA256SUMS.txt and
release-manifest.json outside the ZIPs, avoiding self-referencing builds.

## Limits and publication

Accepted target remains Linux Pi Web UI 0.96.1 / SDK 1.0.2. The tests cover the
native dark palette and built-in Ayu Light, not every custom theme. Mobile checks
use browser viewport emulation, not physical phone/touch hardware. Safari, other
engines, remote TLS/proxy deployment and a separate bare-metal distribution are
not newly certified. Native host accent/disabled styling is retained; no complete
WCAG contrast or assistive-technology certification is claimed.

This is a public release candidate, not an automatic production upgrade or
authorization to publish. The unchanged everyday installation remains intact.
Publish only reviewed staged public source, candidate plugin/source ZIPs and
their manifest/checksums, never the complete development tree or private evidence.
Run the provided final release/credential checks against the exact publication
files; an automated secret scan does not guarantee detection of every format.
