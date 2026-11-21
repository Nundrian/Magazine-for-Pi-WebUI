# Magazine access simplification — 0.1.0-pi.5-rc.1

## Scope and cause

Magazine's wrapper added browser-specific owner capabilities and an administrator
ZIP-import key even though supported Pi 0.96.1 already has shared operator access.
These were not requirements of cartridge execution, safe ZIP handling or storage.
The correction removes those wrapper/UI barriers, not the execution engine.

ASTER v1.0's Read First and Core workflow were applied. The controlling evidence
is the pinned host middleware/plugin-route implementation and the existing
Magazine source. No host rewrite, extra dependency or speculative identity
header scheme is introduced.

## Changes

- `index.mjs`: ordinary host-protected requests, no owner/import headers or key
  checks; default writable Magazine workspace; no tools by default. Existing
  workspace/interpreter/tool settings remain honored.
- `service.py`: no credential generation or registration. New runs use `local`;
  legacy storage partitions remain in place and are listed/read using original
  metadata. Duplicate run IDs fail clearly. ZIP validation and run controls use
  the unchanged helpers. Retired credentials stay on disk for rollback, unused.
- `client/entry.mjs`: no owner section, password fields or administrator panel;
  direct ZIP file selection and Import, unchanged model/run/export controls.
- Manifest/docs/tests/packaging updated. Public archives exclude host deployment,
  launcher and private host-restoration procedures.

All 29 engine modules, `beta5_helpers.py`, `worker.mjs`, `pi_dispatch.py`,
`model_selection.py` and `run_export.py` are unchanged from pi.4-rc.1.

## Compatibility boundary

The pinned native host has no distinct authenticated accounts. Its local
workspace is shared by users who already have access to that Pi instance.
Historical browser-owner separation is intentionally removed within that
workspace. No records are moved/deleted; old owner markers/import-key remain
available for rollback. There is no automatic cross-instance data discovery.
No multi-account host support is claimed: other host versions remain refused.
See INSTALL.md for migration/rollback and SECURITY.md for authority boundaries.

Old API owner headers are ignored. Clients should omit registration/key handling.
Existing prepared model maps, prompts, results and state files remain unchanged.
Old plugin versions cannot enumerate new `runs/local/` through their keyed UI;
keep those directories for reopening with the new plugin.

## Verification

- **PASS:** 47 focused SDK/access/model/export tests, including invalid ZIPs,
  direct import without a key, exact routing, tool calls, STOP, failure recovery,
  legacy retention, conflicting run IDs, path rejection and credential redaction.
- **PASS:** all 611 unchanged engine regressions, run once because the access
  boundary affects all retained run operations. Total: **658 tests**.
- **PASS:** real supported CLI upgrade from pi.4-rc.1 and keyless access to seeded
  legacy READY and COMPLETED runs/results/JSONL, with original data bytes intact.
- **PASS:** packaged native Firefox dark/desktop and Chromium light/mobile
  acceptance, with tokenless and native-token hosts respectively. ZIP import,
  preview, models/overrides/tools, explicit READY/Start, STOP, recovery, history,
  one-click JSONL in interrupted/recovered states, result ZIPs and Pi restart.
- **PASS:** installation/activation without custom Magazine config, no generated
  owner/import credentials, default workspace, no tools by default, small run.
- **PASS:** live existing Ollama model selected explicitly by test argument;
  two fresh cartridges returned `MAGAZINE_TEST_ONE` and `42`. Whole-run JSONL
  contains 22 records; results survived isolated Pi restart. No model default
  was added to Magazine. Live model ID/digest and transcripts are local evidence,
  not a bundled model dependency.
- **PASS:** secret-free before/after fingerprints of all five existing running
  containers, everyday host files/launcher, installed plugin and agent model/
  authentication/settings files match. Production Pi was not restarted or updated.
- **PASS:** release inventory, engine/helper hashes, source parity, ZIP CRC,
  checksums and identical repeated builds. Exact public-source/ZIP scanning
  excludes private paths and detects neither supplied known credentials nor
  common private-key/token patterns. This is not an exhaustive secret guarantee.

Evidence: local `evidence/plugin-simplification/` reports, JUnit XML, browser
screenshots, saved SDK transcripts and isolated-host logs. Evidence/runtime data
is not shipped. The two initial browser attempts failed only the test driver's
whole-agent-directory equality assertion: Pi legitimately creates models-store
and provider-keys files. Comparing the original files separately passes. The
first no-config attempt used the test image's system Python without dependencies;
putting the already provisioned test venv on PATH passes. These attempts remain
recorded, not counted as acceptance passes. Visual inspection also caught the
inherited administrator empty-state message; the wrapper now replaces it and
the focused view assertion verifies its removal. Frozen helpers remain intact.

## Remaining limits and deployment status

This candidate is **not installed into the user's everyday Pi** and is not
published. Restart/persistence tests used real Pi hosts with disposable storage;
the production host and its installed plugin remain untouched. A production
plugin-only update can use the supported installer after backing up its data.
Individual authenticated-account isolation is not claimed for a host that has
only shared operator identity. Platform/version and Python dependencies remain
as documented. No other architecture or functionality was replaced.
