# Host access, files and worker permissions

Magazine is a plugin, not an authentication service. Pi's existing middleware
protects its UI, WebSocket and `/plugins-api/magazine-pi/` routes. Magazine neither
creates nor changes host tokens, cookies, launchers, network bindings or models.
With a tokenless host, it uses the ordinary local workspace without credentials.
With Pi's shared host token, existing Pi authentication still applies.

The supported host, Pi Web UI 0.96.1, has shared operator authority, not individual
authenticated user identities. Magazine uses the instance's shared workspace;
browser IDs and client-supplied owner/user/path fields never select identities.
Legacy owner partitions are retained and accessed in place within this instance.
This intentionally removes their former browser-capability separation. It does
not expose data from another instance. Unrelated users requiring isolation must
not share this single-operator data root. Other host versions are refused; no
multi-account or reverse-proxy identity compatibility is claimed.

Old owner markers and import credentials are left untouched for rollback but
are not created, checked, requested or required. Known retired import secrets
remain redacted from JSONL. Old owner headers from API clients are ignored;
clients should drop registration/key management and use ordinary host access.

ZIP validation is unchanged: bounded archive/member sizes, path/link/collision
rejection, canonical schemas, safe extraction, immutable publication and separate
storage roots. Import requires only a valid project ZIP, not administrator access.
Inputs and model output remain untrusted content, never authority to configure Pi.

Workers use explicit selected models, fresh saved SDK sessions and only selected
allowed tools. No model-specific default or silent substitution exists. Global/
project context discovery remains disabled; reviewed worker extensions are optional.
SDK tool selection is a capability allowlist, not an OS sandbox. Tools run with
the Pi process's OS authority and do not inherit the current chat's private
permission preset, UI-plugin guards or approval rules. No tools/extensions are
exposed by default. Existing operator allowlists remain unchanged. Use suitable
host filesystem/container permissions for untrusted jobs, not Magazine keys.

JSONL is a read-only export of retained sessions and canonical records, not a
fabricated lifecycle journal. Existing credential-field/bearer/assignment/URL
redaction, snapshot consistency checks and rejection of unsafe linked sources
are preserved. No runtime credential/config file is an export source. Unlabeled
secrets in arbitrary output cannot be recognized reliably; treat downloads as
sensitive. Canonical per-cartridge exports and results ZIPs are unchanged.

All 29 engine modules retain their recorded hashes. pi.8-ui.3 changes the helper
publication diagnostics and makes export fallback depend on the captured syscall
error; both original and current helper hashes are recorded. Neither
installation nor this access simplification bypasses Pi's native plugin grants
or changes operating-system permissions. Public release excludes private runtime
data and credentials. GitHub publication requires a separate user decision.


## pi.6 additions (0.1.0-pi.6-rc.1)

- result_file_v1 attempts get one extra tool, `magazine_publish_result`. It is Magazine's
  own code in the worker. It can write only that attempt's `publish/result.json` under
  Magazine's data directory, with identities Magazine supplies. It validates strict JSON,
  1 MiB at most, writes atomically, verifies by read-back and issues a receipt. Magazine
  accepts a result only if it matches that receipt, the expected run/cartridge/attempt/session
  identities and every declared selector. It then commits a copy, archives the session and
  writes a commit marker, and re-verifies all three before any dependent cartridge.
- These checks detect accidental, partial or out-of-band writes. **They are not
  protection against an adversarial model that has shell or write tools**, because those
  tools run as the same OS user as Magazine. A tool allowlist is not a sandbox.
- Tool IDs must be plain names: SDK pattern syntax (`*`, `+x`, `-x`) and the reserved
  publisher name are refused. The active tool set is verified for every attempt, and MCP
  servers are not started.
- Version gate: activation is refused on any host/SDK pair not listed in
  `compatibility.json`, unless the operator acknowledges that exact pair.
- The results ZIP's ledger is credential-redacted like the JSONL export.
- The view keeps its last selection server-side (`view-state.json`, run/project IDs only).
