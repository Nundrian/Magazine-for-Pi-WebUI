# Restart-recovery validation — 0.1.0-pi.8-ui.4

Validated on 24 November 2026 with Pi WebUI 0.120.0 / Pi SDK 1.5.2.

| Check | Result |
|---|---|
| Baseline regression suite | 906 passed, 1 skipped |
| Repaired regression suite | 929 passed, 1 skipped |
| Targeted reconciliation plus scheduler-order regression | 24 passed |
| Native plugin lifecycle | 2 passed |
| Two real restarts of a disposable Docker fixture | HALTED persisted; no inference dispatch |
| Chromium desktop/mobile fixture | Explicit action and uncertain-owner diagnostics passed |
| Installed native Pi interface | HALTED and normal deletion preview/confirmation verified; deletion cancelled |
| Native CLI package installation, CRC, source parity and reproducible builds | Passed |

The legacy pi.5 upgrade case was skipped because its old package and SDK 1.0.2
were unavailable. Automated inference used scripted loopback responses inside
network-isolated containers; no local LLM or Unreal Engine was launched.

Tests cover SIGKILL, container restart, PID reuse, stale and live locks,
ambiguous ownership, surviving workers, active cancellation, interrupted STOP,
partial writes, repeated reconciliation, scheduler persistence, duplicate
prevention, deletion, trash restoration and preservation of unrelated work.
The actual interrupted run was reconciled without starting it and remains saved.

Only `recovery_action.py` changed among the 29 bundled core files; its previous
hash and current fingerprint are recorded in `reused-components.json`.
Process extinction does not establish inference cancellation or external tool
completion. The repair reports those uncertainties explicitly.

The host's MCP connections are separate from the worker tool surface. The real
worker catalogue did not expose native Resource Kit or Unreal Connector tools.
A fixed probe through the SDK's built-in bash tool authenticated to Resource Kit
and reached Unreal Connector liveness; no engine operation was requested.

See [operator recovery behaviour](RESTART-RECOVERY.md). Runtime/private backups,
credentials, run records and workspace files are excluded from the repository.
