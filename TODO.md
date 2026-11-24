# Development roadmap and outstanding work

Updated 24 November 2026.

This is the active checklist for Magazine for Pi WebUI. It distinguishes the
public source tree from the locally installed development build. Priorities are
proposed from the 24 November reliability audit and deployment report; checked
items below describe reported local work, not functionality guaranteed by the
current public checkout.

## Release baseline

- Public source at the time of this update: **0.1.0-pi.8-ui.5**.
- Latest reported local build: **0.1.0-pi.9.1** (Pi WebUI 0.120.0, SDK 1.5.2).
- Local pi.9.1 validation reported 984 Python tests passed, one skipped, 13/13
  Node tests passed, package verification and live non-inference boundary probes.
- Production model execution under the new sandbox has not yet been validated.

## Recovered source archive — independent checks

- [x] Recovered the exact pi.9.1 source and plugin archives. Both SHA-256 values match the local release report; both ZIP CRC checks pass.
- [x] Verified byte-for-byte parity for all 79 plugin files against the 194-file source archive.
- [x] Independently ran restore-point tests: 10 passed.
- [ ] Repeat the sandbox tests in a Landlock-capable target environment. An independent partial test run passed 14 tests before stopping at a sandbox test because Landlock is unavailable in the review environment. This does not reproduce or invalidate the reported Docker Desktop results.
- [ ] Finish the source comparison, independent code audit and actual publication. Archive verification alone is not a completed source synchronisation.

## P0 — Before another unattended production run

- [ ] Import the authoritative pi.9.1 source, compare with current main, preserve
  newer work, review the diff, verify package/source checksums and scan for
  secrets and personal deployment details before publishing. Do not mark the
  public source version as pi.9.1 until the actual code is present.
- [ ] Independently review worker confinement, trusted handover, controller
  integrity, fail-closed recovery and emergency cancellation in the actual
  source. Check file descriptor inheritance, allowed paths and alternate
  execution mechanisms; verify the controller cannot accept a false completion.
- [ ] Review credential access and transcript redaction. Rotate any exposed
  integration credentials locally and update the corresponding configuration;
  never publish private logs or tokens.
- [ ] Run a small disposable acceptance workflow with a known compatible model
  after owner approval. Verify tools, legitimate workspace writes, trusted
  result publication, DONE/HALT handling, retries, cancellation, restart,
  reconciliation and ledger/chat consistency. Start with one model and no
  unrelated production workload.
- [ ] Correct explicit project-root mapping for workflows that require an
  external host directory. Use a verified bind mount, narrowly scoped worker
  write permission and required-path preflight. Refuse an incorrect or missing
  path; never silently substitute a directory.
- [ ] Audit workflow templates for ambiguous state-file paths and contradictory
  final-response instructions. Project evidence must remain distinct from
  controller records. Audit all cartridges of affected workflows before
  recovery; preserve the original run and reject unverified completion claims.

## P1 — Isolation and reliability hardening

- [ ] Evaluate distinct worker identities, per-project access grants and
  lightweight process/network isolation. Do not claim a complete sandbox:
  Landlock ABI 3 does not provide the newer network or signal restrictions;
  metadata changes are detected rather than fully prevented. Prefer a practical
  enforceable boundary without unnecessary services; assess separate containers
  if required. Document the residual risks and validate on Docker Desktop.
- [ ] Verify legitimate authorised integrations continue working under
  confinement, including credential mediation, without giving the model
  unrestricted access to private configuration.
- [ ] Harden path normalisation and Windows case/alias handling, including
  mount boundaries. Test actual drvfs/9p as well as ext4/overlay.
- [ ] Bound worker stderr/log growth with useful, redacted diagnostics.
- [ ] Review scheduling and transaction races, round-robin and sequential mode,
  duplicate dispatch, retry ceilings, cancellation and interruption between
  publication and commit. Keep tests isolated so parallel suites cannot affect
  each other's worker state.
- [ ] Verify restore-point protection for live runs, recovery dependencies and
  interrupted deletion; test individual/bulk deletion, actual storage reclaim,
  and safe restore without automatic execution or automatic deletion.
- [ ] Confirm model discovery handles downloads, removals, outages and embedding
  models; preserve saved assignments. Distinguish catalogue availability from
  backend compatibility; verify deterministic incompatibility halts without
  needless retry.
- [ ] Audit all seven UI tabs, complete exports, live chat, per-cartridge ledger,
  elapsed time and token/cache accounting on malformed and interrupted runs.
- [ ] Investigate the skipped legacy test and document the reason; maintain
  reproducible acceptance instructions and versioned test results.

## P2 — Follow-up and release readiness

- [ ] Review the equivalent Open WebUI worker/controller boundary, particularly
  in-process server tools. Keep this a separate project and do not change that
  installation as part of a Pi release.
- [ ] Update installation, security, technical and visual guides to match
  shipped code; document required paths, sandbox limitations, emergency Stop,
  state repair and restore-point management.
- [ ] Verify install/upgrade/rollback with preserved configuration, history and
  queue; publish a tagged release and checksums only after review and acceptance.
- [ ] Keep the repository free of runtime data, transcripts, credentials,
  private configuration, backups and project-specific deployment paths.

## Reported completed locally in pi.9 / pi.9.1 (pending source synchronisation)

- [x] Recovered a damaged run to HALTED with no falsely accepted cartridges;
  preserved evidence and chats; nothing automatically resumed.
- [x] Landlock/seccomp worker confinement and controller-integrity checks.
- [x] Emergency Stop independent of valid persistent run state; explicit repair
  action and damaged-state view.
- [x] Restore-point list, restore, individual/selected/all eligible deletion,
  protection and confirmation; no automatic deletion.
- [x] Immediate halt for deterministic model incompatibility; bounded provider
  retries; optional required-path preflight.
- [x] Preserved automatic Ollama catalogue synchronisation.
- [x] Corrected Docker Desktop 9p grant lifetime issue in pi.9.1 by retaining
  grant descriptors in the supervising launcher (close-on-exec).
- [x] Reported passing non-inference tests and live probes; no production LLM
  or Unreal workload used.

Move a task to completed only when the corresponding evidence is recorded.
Passing simulated tests is not evidence of production inference success.
