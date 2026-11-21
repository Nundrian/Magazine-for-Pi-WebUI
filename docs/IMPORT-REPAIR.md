# Workflow import repair — 0.1.0-pi.8-ui.3

Follow-up, 21 November: workflow and saved-work deletion now use Confirm deletion / Cancel without typed IDs. This client-only change passed 14 actual-interface checks and 67 related backend regressions; no service restart or inference was needed. The 20 November archive/parity evidence below describes that earlier release snapshot.

Verified on 20 November 2026. Import/runtime acceptance and model answer quality
are separate. Original workflow ZIPs, prompts, private config and full execution
evidence remain local and are excluded from public source and packages.

## Cause and correction

The installed ui.2 importer reproduced HTTP 503 with the original smoke ZIP.
Its library was `/app/.pi-web/magazine/library`, writable by UID/GID 1000 with
mode 0700, on a Windows Docker Desktop `9p`/DrvFS bind mount. A disposable
sibling-directory probe returned **renameat2(RENAME_NOREPLACE): -1, errno 22 /
EINVAL**. Directory fsync succeeded. The same syscall succeeded in Linux
`/tmp`. This established the actual cause independently of the export comment.

The repair keeps atomic directory publication. No ordinary rename or overwrite
fallback was added. Export's flat-file, manifest-last hard-link protocol does
not safely generalize to nested imports without changing all workflow readers.
The smaller correction uses a Docker-managed Linux volume for Magazine's entire
internal state at the same container path. Parent Pi data and providers remain
on their existing mount.

All state was copied to a new empty volume while only the selected Pi host was
stopped, synchronized and compared by file SHA-256/bytes, type, UID/GID,
permissions and empty directories. The installation had seven state entries,
no saved runs/workflows and an empty queue; existing settings were copied rather
than replaced by a fresh database. Old state remains intact. An external Compose
volume declaration refuses a missing volume instead of silently initializing an
empty replacement. The mounted inventory matched before plugin activation.
The effective mount is ext4 and atomic no-overwrite publication succeeds.
See [migration and rollback](LINUX-STORAGE.md).

## Changes and safeguards

| File | Change |
|---|---|
| `magazine-pi/beta5_helpers.py` | Captured errno, browser-safe error categories and server-only diagnostics; export fallback uses captured unsupported error rather than stale ctypes errno |
| `magazine-pi/index.mjs` | Line-buffer specific filesystem diagnostics into native logs; keep other stderr generic |
| `magazine-pi/pi_dispatch.py` | Handle process exit between returncode check and SIGTERM/SIGKILL without replacing a valid result with ProcessLookupError; consume reader exceptions |
| `service.py`, `manifest.json` | ui.2 → ui.3 version |
| `reused-components.json` | Record original/current helper hash and modification provenance |
| `scripts/migrate_storage.py` | Refuse active state, links/special entries and nonempty copy destination; preserve/verify state without retiring source or switching mounts |
| New publication/exit-race tests, release builder, docs | Fault regressions, packaging and migration/rollback instructions |

ZIP validation, limits, extraction, fd/no-follow reads, identity checks,
case-insensitive conflicts, import locks, model selection, committed results,
deletion/restore and all 29 engine files are unchanged. Post-publication directory
fsync failure reports a published but uncertain durability outcome, never success.
A killed pre-publication process may leave a hidden stage; discovery excludes it.
Unknown orphan stages are not automatically removed without identity evidence.

## Automated checks and review

**906 passed, 1 skipped**, zero failures/errors, in 223.92 seconds on Python 3.11 /
Node 22.23.5 / actual SDK 1.5.2. The skip requires genuine pi.5 records created
using SDK 1.0.2, absent here. Pi's native mock-host lifecycle checks passed **2/2**.

The 46 publication/migration checks cover valid nested imports; EINVAL, ENOSYS,
EOPNOTSUPP and missing syscall; permission/I/O failures; existing empty/content/
case-colliding/symlink destinations; duplicate names/IDs; concurrent processes;
death before/after publication; file/directory fsync; failed cleanup and identity
substitution; malicious ZIP entries; schema/resource limits; repeated attempts;
discovery; export ordering/stale errno; and safe migration failure. Three exit-race
checks reproduce both cleanup signals and preserve original worker errors.
Existing export, deletion/restore, model-selection, scheduler, committed-result,
security and recovery tests remain green.

The first full run exposed the cleanup race in two existing SDK integration
tests. Its third failure was missing PYTHONPATH for an inherited subprocess test.
Both were resolved before the passing run. No security assertion was weakened.
A separate read-only review of changed runtime code, publication callers,
reader visibility, migration invariants, failure paths and release inventory
found no remaining blocking code issue. This was a second review pass by the
implementing agent, not an external human security audit.

## Actual Pi acceptance

Host/SDK remained **0.120.0 / 1.5.2**. Magazine was updated **ui.2 → ui.3** via
native uninstall/install of only Magazine. The mount change required recreation
of only this idle Pi instance. Other containers and plugin identities were
preserved. Provider files, token, private plugin config, catalogue, model weights
and inference/context settings were unchanged.

Both original ZIPs imported: **2 smoke cartridges** and **142 continuation
cartridges**. Every prompt and order matched the archives. Refresh and explicit
preview/load worked. Duplicate imports returned 409 and preserved originals.
A smoke READY run persisted across reopen and restart without preparation
starting inference. The full continuation was never prepared or started.

Native API checks verified shared, individual, bulk and explicitly applied JSON
assignments, exact catalogue IDs, invalid/stale/unavailable rejection, validation
without application, persisted assignments, and reversible deletion/restoration
of new unstarted fixtures. Valid/invalid `model-assignments.json` sidecar imports
are covered by the full automated suite. The UI client is byte-identical to ui.2;
this acceptance used supported native APIs, not a claimed new browser test.

Results, full ledgers and whole-run JSONL for all four real runs were identical
after a final idle restart. Both workflows persisted and duplicate rejection was
repeated. Completed Finished Work remained EXPORTED. The selected view opens
the completed smoke run.

## Genuine local-model results

The actual project marker was externally verified and the existing project was
mounted read-only. Only native bash plus Magazine's publisher were enabled.
No model was downloaded, substituted or reconfigured. Ollama was **0.40.16**;
initial inspection found no loaded models/client work.

| Explicit assignment | Actual outcome |
|---|---|
| Qwen3.6 on both | HALTED after first commit: second-cartridge output-limit and incomplete-stream/template errors; 4 retained attempts |
| Bonsai 2 on both | HALTED after first commit: output limits and `no user query found in messages`; 9 attempts |
| Native Qwen3-Coder on both | **COMPLETED**, 2 committed results, 3 attempts: initial Ollama CUDA initialization failure followed by successful cartridges |
| Qwen3.6 first / native Coder second | First result committed; HALTED on repeated Coder CUDA initialization errors; 3 attempts; no substitution |

The completed run's retained-byte audit confirmed distinct successful saved
sessions, one actual user task per session, recorded models matching assignments,
real bash and successful native publisher calls, exact result/session hashes and
commit identities, and the first committed note in the second actual prompt/chat.
Tool outputs, final responses and failed attempts remain in Chats. Results expose
committed payloads rather than just DONE text. ZIP/whole-run JSONL downloads passed.
Duration and reported usage were retained: **39,757 reported aggregate tokens
including cache reads**; one failed attempt has no usage, so accounting is partial.

**Content qualification:** Coder computed an app.py hash where the first prompt
requested the marker hash, and corrected one failed shell-path quoting attempt
in the second cartridge. Exact marker bytes were independently checked externally;
the omission remains visible in the original result. The mixed-model run was a
bounded attempt to close it: its first stage supplied the marker hash, but its
second failed in Ollama. A completed runner contract does not establish perfect
adherence to every prompt detail. Model/provider reliability remains a limitation.

The host project evolved during acceptance outside Pi's read-only mount. The
evidence does not claim whole-tree byte identity. Retained smoke commands were
inspection commands, and writable Pi mounts do not expose the real project.
No launcher, Epic/Fab operation, live-account action or full continuation ran.

## Evidence and limits

The local repair directory retains filesystem probes, original HTTP failure,
JUnit XML, migration inventory, installation/model checks, import previews,
four real runs, saved-session/commit audits, ZIP/JSONL downloads, restart comparison,
checksums and rollback sources. Public packages contain no private runtime data.

No physical power-loss test, exhaustive filesystem matrix, fresh-image complete
installation, new UI browser suite or external security audit is claimed. Fault
injection establishes tested failure behavior. SDK pairs other than 1.5.2 were
not re-run. Failed model runs and the content omission are not reported as passes.
