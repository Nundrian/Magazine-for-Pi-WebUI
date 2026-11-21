# Development record — 14 October 2026

## Standing plugin/host boundary — 18 October 2026

Magazine is a plugin for Pi Web UI. Installing, updating or removing Magazine
must not change the host's authentication requirements, browser login procedure,
network configuration, model/provider configuration, launcher or core application
code. It must work with the host's existing configuration, authenticated or
token-free. Local host restoration at the user's explicit request is separate
from plugin installation and never authorizes changing Magazine execution code.

The separate local host-restoration report is retained privately in the
development workspace, not shipped as a plugin-installation procedure.

## Access simplification — 18 October 2026

Pi.5 removes owner/import-key creation, checks and UI controls. Native host
access is the boundary; pinned Pi 0.96.1 has no individual account identity.
Legacy partitions are read in place, new records use the instance workspace,
and existing configs remain honored. Full engine/helper hashes are preserved.
Only the plugin wrapper/view, focused tests, docs and packaging change.
No host settings, launcher, authentication, providers, images or networking
are changed. Public archives omit host deployment/launcher examples.

## Earlier development evidence

- Identified the frozen Beta 5 candidate using provenance and both archive
  hashes. Separated its fresh storage/install results from inherited Beta 4
  Bonsai/STOP evidence. Extracted only its allowlisted public source into this
  development folder.
- Identified the actual target container: `xing-shuyin/pi-web-ui` 0.96.1 with
  global and bundled Pi SDK 1.0.2. The Windows CLI 0.87.1 is a separate runtime.
  Read installed plugin API 2, SDK declarations and implementation. Current
  upstream docs corroborated native directory-based plugins; installed source
  governs the implementation.
- Reproduced 989 original regressions in the isolated copy. Corrected the
  Function suite's import path before counting its final pass.
- Reused all 29 core modules and 72 helper source segments. Added native
  plugin routes/view, owner/import capabilities, an atomic metadata writer,
  and a one-worker Pi SDK dispatch adapter. Python remains the only Magazine
  execution engine; the process bridge creates no external service.
- Actual SDK testing exposed inactive-tool catalogue suppression and NTFS
  no-replace rename limitations. Catalogue enumeration now uses a non-inference
  session; runtime workers retain strict selected-tool registries. Library
  tests use Linux storage, matching the accepted deployment, with evidence
  preserved back into the development folder.
- Added concurrent Start and malformed-input coverage. The original schema
  exception type extends `Exception`; it is now mapped explicitly to HTTP 400.
  Conflicting starts map to 409 and cannot create a second execution.
- Native host testing required its normal WebSocket hello/discovery step.
  Its compiled engine checker did not resolve its package version; the plugin
  checks the running executable's resolved package and exact version itself.
- Browser checks found/fixed hidden selector CSS, stale history status,
  misleading READY STOP availability, and missing frozen model/tool summary.
  Binary uploads preserve Beta 5's 8 MiB upload boundary within the Pi host's
  10 MiB JSON limit without modifying the host.
- Real local Ollama testing passed two fresh sessions, actual read-tool output
  and streaming STOP. Bonsai's configured endpoint was unavailable; no Bonsai
  live result is claimed. Full live sessions and state are retained locally.
- Documented SDK tool authority explicitly: worker allowlists do not inherit
  the current Pi chat's private permission wrappers. Added an operator tool
  allowlist and required explicit workspace configuration. No ASTER runtime
  dependency, automatic chaining, repair or model substitution was introduced.
- Final release/testing results are recorded in `ACCEPTANCE.md` and the
  development `evidence/` directory. Production services and the original
  Open WebUI project were not modified or used as writable test targets.
- Release finalisation refreshes both archives from the verified checkpoint,
  including the final UTF-8 extraction and Unicode/CRLF regression changes.
  Full inventory/source parity, checksum agreement and two-build reproducibility
  are checked independently. The rebuilt plugin is installed and smoke-tested
  in a new isolated Pi instance; new evidence is separate from prior reports.
  No runtime implementation expansion or full regression rerun is involved.

- Subsequent genuine Bonsai 2 tests passed container reachability, actual model
  routing, sequential isolated saved sessions/results and streaming STOP. The
  original earlier unavailable-endpoint observation above remains historical.
  Everyday installation then passed live execution and container recreation.
- pi.2 corrects portable native authentication/installation guidance and safe
  example tool defaults. Pi's existing one-year cookie and browser credentials
  suffice; no plugin authentication defect or runtime change was found. Personal
  sign-in helpers and all private deployment material remain excluded.
- Real Firefox/Chromium persistent-process restart tests passed fresh install
  and pi.1-to-pi.2 update with/without host tokens, restart/data preservation,
  native view/API, isolated model routing/export and token rotation. Pi's CLI
  preserves config.json; documentation was corrected to match that evidence.
  Test-driver locator/startup-baseline mistakes were retained as failed attempts,
  not misclassified as plugin defects. The accepted 622-test suite was not repeated.

- pi.3-rc.1 uses the inspected Pi 0.96.1 CSS tokens (--text, --bg-elev, --accent,
  --sans, --mono and native semantic/theme colors). Replaced oversized section
  boxes with dividers, matched 8px host controls and 13px typography, tightened
  spacing and bounded mobile wrapping/focus. Only the client style block changed;
  all existing markup and behavioral JavaScript is retained byte-for-byte.
- Added full native UI regression scenarios, pi.2 upgrade/data preservation,
  scoped styling parity and exact staged-source/ZIP credential scans. Native
  light/dark palettes are loaded by Pi itself. No host/production styling,
  execution, authentication, API, session or data-format changes were made.
