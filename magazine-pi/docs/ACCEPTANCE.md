# Acceptance — Magazine for Pi Web UI

## pi.7-rc.2 token counter — 19 November 2026

Adds the rc1 "Reported tokens" counter (per selected project and across all projects); no engine,
scheduler or ledger format change. Offline suites and real-host UI checks (now including the counter
against the server's totals) are recorded in TESTING.md.

## pi.7-rc.1 offline acceptance — 18 November 2026

Scheduler port of 0.3.0-scheduler.rc1; offline only, no model. Counts are in TESTING.md and design in PI7-SCHEDULER.md.

## pi.6-rc.1 offline acceptance — 17 November 2026

Offline only. Real Pi SDK 1.0.2/1.1.0/1.5.2 and real Pi Web UI 0.96.1/0.115.0/0.120.0 hosts
were used with a scripted loopback provider and headless Chrome. **No model, no live
acceptance, not installed.** Counts and commands are in TESTING.md, decisions in PI6-REPORT.md.
Earlier sections below are historical and describe pi.5.

## pi.5-rc.1 access simplification — 18 October 2026

The new wrapper removes Magazine credentials and reads legacy partitions in
place. Focused execution, ZIP, upgrade, UI, restart and host-preservation results
are recorded in SIMPLIFICATION.md. Reports below are historical; their owner-key
requirements do not describe the current interface.

## Model selection and whole-run export candidate — 16 October 2026

New candidate: generic selected default with optional cartridge overrides,
backward-compatible explicit alias maps, and owner-scoped **Download Run JSONL**
in the existing native controls. No execution/persistence journal was added.
All 29 core modules and the extracted helper retain their original hashes.
The accepted pi.3-rc.1 archives and original 622-test report remain untouched.

The final full suite passes **654 tests**: the 611 inherited core tests, 13
actual pinned-SDK integration tests, and 30 model-selection/JSONL unit cases.
It covers exact host-ID routing, default inheritance, alias/override precedence,
invalid references, source-record completeness, tool calls/results, failed and
recovered attempts, active STOP, persistence/restart, Unicode/CRLF, owner
separation, credential redaction and unsafe/changing source rejection.
The first attempt omitted the documented PYTHONPATH for a subprocess test
(649 PASS, one import failure); the corrected 650-test run and final expanded
654-test run both pass. All attempts are retained separately.

The final packaged native-host matrix passes all **8 cases**: Firefox 157 and
Chromium 156, dark/Ayu Light, desktop/mobile. Every case exercises the actual
one-click JSONL download in READY, completed multi-cartridge, active-STOP partial,
halted error and recovered states, with shared-default inheritance and an
individual override. Host/owner rejection checks and previous-version READY
data/configuration preservation pass. The fixture made 56 deterministic provider
requests; no live LLM was used. Evidence: `evidence/model-agnostic/`.

Ordering, retention, redaction and ambiguous legacy-session limits are explicit
in MODELS-AND-JSONL.md. Historical live-model evidence below is historical
acceptance evidence, never an installed model/default requirement.

## Previous pi.3-rc.1 styling acceptance (preserved)

14 October 2026. This styling release candidate changes only the embedded
client CSS relative to accepted pi.2. Exact markup/JavaScript identity and
non-client runtime identity are release gates. pi.2 remains the previous
validated build; its portable authentication results below are preserved.
There is no new execution/authentication/API/data-format implementation.
The authoritative Open WebUI project remains untouched; no ASTER dependency.

## Native interface refinement

Eight full native UI cases PASS: Firefox 157 and Chromium 156, each in Pi's
native dark and Ayu Light themes, on desktop 1440×1000 and mobile 390×844 layouts.
Evidence is retained in `evidence/native-style/matrix-3/native-ui-results.json`
(one completed case) and `matrix-5/native-ui-results.json` (seven cases). Passing
cases were retained rather than repeated after test-driver corrections.

Every original control is present and exercised: owner show/hide/use, refresh,
trusted ZIP upload/retention, library selection/preview, invalid/valid JSON,
prompt disclosure, shared and per-cartridge models, authorized tool selection,
Prepare READY without inference, explicit Start, progress, active STOP preserving
prior results and preventing the next cartridge, permanent STOP disabling recovery,
separate failure/recovery to READY and explicit Start, history/page-reload
preservation, Finished Work export and actual ZIP download/hash verification.
Actual SDK sessions/results are retained. The HTTP provider is a deterministic
fixture, not a new live LLM test: eight cases used 56 provider requests.

Pi.2-to-candidate CLI update preserves configuration and existing READY data.
Computed styles match the actual host font/theme tokens, control radius and
divider treatment. Empty, READY and COMPLETED controls fit the tested viewport
without horizontal overflow. Screenshots cover empty/READY/COMPLETED/STOP in
both native palettes; mobile uses the host's existing overflow navigation.
No production service or browser preferences were modified for this refinement.

The final public source/plugin package smoke, reproducible build/inventory
checks and exact staged GitHub-source/ZIP credential scan are recorded under
`evidence/native-style/`. All 35 non-client code/dependency files remain identical
to pi.2; the client's single style block is the only runtime-file difference.
The accepted 622-test report and earlier live/STOP/authentication evidence remain
unchanged and were not rerun. See UI-STYLING.md for limits and publication scope.

## Preserved evidence

| Verification | Result | Local evidence |
|---|---|---|
| Original Beta 5 isolated baseline | 611 core + 244 Function + 134 JS = 989 PASS | baseline XML/logs |
| Standalone core + actual Pi SDK regression suite | 611 + 11 = 622 PASS; not repeated | pi-final.xml |
| pi.1 release inventory, source parity, CRC, checksums, reproducibility and packaged native smoke | PASS; original ZIPs retained | release-finalisation/ |
| Live Ollama Ornith 9B text, real read tool and streaming STOP | 3 scenarios PASS | live-llm.json and saved sessions |
| Live Bonsai 2 27B container endpoint/API, two isolated sequential sessions, routing/results, active STOP blocking next cartridge | PASS | bonsai-verification/bonsai-report.json, request/SSE/session evidence |
| Everyday installation: READY/Start, genuine Bonsai workflow, export, recreation/preservation | PASS | production-installation/installation-result.json, everyday-acceptance.json |
| 29 canonical core files and extracted helper identity | Byte-identical | reused-components.json and release checks |

The 622 report SHA-256 is
`4d35ae6c2d901e2d11b48c10c7d8f33f8c68538e5b7ac621f08543eb66352d00`.
The accepted pi.1 plugin SHA-256 remains
`6ba5767d5714151a5acff4fc7e30fa47b8dbb92e5de22e8cec49e38d26efddf1`.

Earlier reports recorded an unavailable Bonsai endpoint and no production
installation. Those observations were true then; later live and everyday
installation evidence supersedes them. Earlier evidence is preserved, not rewritten.

## Portable native authentication and installation

`evidence/portable-auth/matrix-6/portable-auth-results.json` records four passing
cases: tokenless fresh install, tokenless upgrade, token-authenticated fresh
install and token-authenticated upgrade. Each uses the real Pi 0.96.1 CLI/server,
SDK 1.0.2, packaged plugin and separate Linux test storage. The test image has
no production mounts or external networking. Its deterministic local provider
is not a live LLM; 12 provider requests cover six two-cartridge workflows.

Both actual Firefox and Chromium browser processes close and relaunch persistent
profiles. The native Magazine view/API opens at the ordinary URL after browser
restart and after host restart. Host-cookie attributes/one-year expiry are
checked, and token rotation rejects/clears old cookies before native re-sign-in.
Unauthenticated protected API requests return 401 on token-configured hosts;
tokenless hosts impose no new host login. Public health checks do not issue
credentials. The plugin never implements its own host authentication service.

The workflows verify READY without inference, explicit Start, model-a then
model-b, exactly one fresh saved session per cartridge, no inherited context or
tools, canonical saved responses, COMPLETED, result ZIP hashes and ownership
boundaries. Updates preserve all existing Magazine data, operator settings,
models, auth configuration and unrelated plugin data. Pi itself initializes
models-store.json/provider-keys.json on a fresh host; the test records that native
initialization separately. Its installer preserves existing config.json, verified
in both update cases. New packaged execution also passes after upgrade.

Initial test attempts contained a Docker entrypoint mistake, native-menu locator
mistakes and an overly strict first-start data assertion. They are retained
under portable-auth/matrix-2 through matrix-5. These were test-driver issues,
not observed plugin compatibility defects. No runtime changes were made to fix them.

The operator confirmed ordinary access after closing/reopening their everyday
Firefox using one-time native Pi sign-in. Personal shortcuts are private local
conveniences, not release requirements or plugin contents. Final packaging and
everyday recreation checks are recorded separately under portable-auth/;
the release manifest and SHA256SUMS.txt identify final artifact bytes.

## Supported scope and remaining limits

- Target: Linux xing-shuyin/pi-web-ui 0.96.1, SDK 1.0.2, Node 24, Python 3.11.
  Other hosts/SDK versions, native Windows/macOS, proxy prefixes and a separate
  bare-metal distro/service-manager install are not certified. The optional
  generic Compose example is illustrative; native CLI/container installation is tested.
- Browser persistence is profile/hostname specific. Private windows, clearing
  site data, cookie expiry or token rotation require native sign-in again.
  A real one-year wall-clock wait is not claimed; emitted expiry is verified.
  Other browser engines and remote HTTPS/proxy deployments are not freshly tested.
- Pi's native frontend stores a browser token and may use credential-bearing
  resource/WebSocket URLs. Protect bootstrap links/logs, use HTTPS for remote
  access, and do not confuse SameSite/HttpOnly with network encryption.
- Existing automatic owner capabilities scope Magazine runs. Pi has shared
  operator authority, not hostile multi-tenant isolation. Tools run with OS
  permissions and do not inherit private chat permission/approval wrappers.
  Examples start with no tools or worker extensions authorized.
- Shared-library storage requires Linux atomic no-replace rename; Windows NTFS
  bind-mounted library storage is unsupported. Preserve canonical data on a
  Linux volume. Exports contain canonical responses/manifest/ZIP, not arbitrary
  tool files; actual session JSONL is retained separately.
- Arbitrary UI-only tools/MCP/OAuth/providers, large-scale concurrency, semantic
  correctness and provider-side cancellation acknowledgement are not certified.
  STOP and recovery remain distinct. COMPLETED means execution and saved responses,
  not successful real-world work. No automatic output chaining, repair, planning
  or model substitution is added.

Release archives exclude credentials, personal helpers, runtime config, backups,
reference sources, dependency environments, conversations and raw private evidence.
Publication is a separate operator decision; these checks do not publish to GitHub.
