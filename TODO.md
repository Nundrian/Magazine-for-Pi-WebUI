# Development roadmap and outstanding work

Updated 27 November 2026.

## Current baseline — pi.10.5

Public source: **0.1.0-pi.10.5** (reviewed candidate). See [CHANGELOG.md](CHANGELOG.md) and [corrective notes](docs/PI105-CORRECTIVE.md).

The final isolated acceptance on 27 November 2026 passed 1,066 Linux regression tests (one legacy skip), 21 JavaScript/native-host tests and Firefox interface tests. Three real-model workflows committed all 28 cartridges (8/8, 8/8 and 12/12) using Qwen and Ornith 9B models. Tests covered sequential and round-robin scheduling, restart/recovery, native compaction, model/reasoning selection and deletion/restoration. A maximum of one upstream inference request was observed during the round-robin runs. Earlier failed attempts and partial usage were retained, not counted as successful attempts. The legacy pi.5 upgrade test requires its historical archive and SDK 1.0.2. Other host/SDK pairs, private external integrations and full project acceptance were not re-certified. Production installation is a separate step.

- [x] Publish reviewed pi.10.5 source without rewriting repository history.
- [x] Correct truncated response classification, Manage polling, native compaction event forwarding and compaction token accounting.
- [x] Complete the three isolated real-model acceptance workflows and Firefox checks.
- [ ] Authorised upgrade of the existing installation; verify preserved state, configuration, catalogue and integrations, with rollback available.
- [ ] Real project acceptance and verification of optional external services when available.
- [ ] Investigate historical MCP EIO if it recurs; no causal fix is established.

## Historical pi.9 / pi.9.1 audit checklist

The following retained checklist records the 24 November audit baseline and longer-term engineering tasks. Unchecked historical items are not automatically closed by pi.10.5 acceptance; consult the current baseline above for the narrower verified results.

## Recovered source archive — independent checks

- [x] Recovered the exact pi.9.1 source and plugin archives. Both SHA-256 values match the local release report; both ZIP CRC checks pass.
- [x] Verified byte-for-byte parity for all 79 plugin files against the 194-file source archive.
- [x] Independently ran restore-point tests: 10 passed.
- [ ] Repeat the sandbox tests in a Landlock-capable target environment. An independent partial test run passed 14 tests before stopping at a sandbox test because Landlock is unavailable in the review environment. This does not reproduce or invalidate the reported Docker Desktop results.
- [x] Source comparison and publication: the pi.9.1 commits were merged with the
  newer documentation on main without rewriting history. Every file in the
  verified source archive matches the published tree byte for byte; private
  configuration, runtime data, transcripts, backups and restore points are
  excluded.
- [ ] Independent code audit of the published source by a reviewer outside the
  original development session (see P0).

## P0 — Before another unattended production run

- [x] Import the authoritative pi.9.1 source, compare with current main, preserve
  newer work, verify package/source parity and scan for secrets before
  publishing. Done 24 November 2026; the public source version is now pi.9.1.
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
  queue; verify the source commit and package checksums; a separate tagged release is optional.
- [ ] Keep the repository free of runtime data, transcripts, credentials,
  private configuration, backups and project-specific deployment paths.

## Completed in pi.9 / pi.9.1 (present in the public source)

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
