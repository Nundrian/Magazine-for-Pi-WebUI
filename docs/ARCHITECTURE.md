# Architectural assessment — 14 October 2026

Historical assessment below. In pi.5-rc.1 the owner/import-key wrapper was
removed at the user's direction; engine and storage formats are unchanged.
Current single-operator workspace access and legacy retention are documented
in INSTALL.md and SECURITY.md. Host configuration is outside the plugin boundary.

The authoritative behavioural reference is the frozen Magazine 0.1.0-beta.5
candidate `Magazine-Public-Beta5-RC-20261014` (private reference, not required at runtime).
Both release archive hashes were read and match its provenance: full archive
`90d8b88bf98b0dd8ea817a7b57666823d28c633aa584e051d037f088e2183a07`,
Function-only archive `166e865673beb41d5b23aae223ebc188e5477c810281037b51cc8632830295dc`.
The isolated reference under this project's `reference/` was extracted from the
full allowlisted archive. Private backups and credentials were not copied.

Beta.5 records 611 core, 244 Function and 134 UI/sidebar regressions. Its fresh
native install/restart and Windows storage checks used Open WebUI 0.11.3.
Live Bonsai inference and STOP evidence is inherited from beta.4. COMPLETED
means execution and preserved responses, with external review still required.
Current baseline supersedes the September design ideas: automatic semantic
verification, output chaining and reconstructed chat exports are absent.

## Installed target

The inspected Linux Docker installation runs `pi-web-ui` 0.96.1 from
https://github.com/xing-shuyin/pi-web-ui with global Pi Coding Agent 1.0.2.
The separate Windows Pi CLI is 0.87.1 and is not the target runtime.
Installed package/SDK/server contracts were copied for inspection. Host plugin
API version is 2: a directory with `manifest.json`, `index.mjs` and
`client/entry.mjs` adds a native tab and routes under `/plugins-api/<id>/`.
See https://github.com/xing-shuyin/pi-web-ui#plugins for upstream guidance.
The installed compiled source, not current upstream main, governs adaptation.

`host.llm.complete` has no tools or saved conversation. `host.chat/chatWait`
does not expose complete model/tool/context selection. Neither satisfies the
Magazine worker contract. Pi's public `createAgentSession` SDK does: explicit
model, tool allowlist, resource loader and fresh persistent SessionManager.

## Smallest adaptation

Reuse all 29 canonical Python core files byte-for-byte. Extract neutral
library, storage, result, STOP and recovery helpers verbatim from the Function;
record exact source segments and hashes in `evidence/reused-components.json`.
The Python process remains the only Magazine execution engine. A thin plugin
starts it over stdin/stdout; a Pi adapter starts a fresh SDK worker per cartridge.
No HTTP daemon, database, Open WebUI, or ASTER runtime dependency is needed.
Linux is retained because the core ownership identity and beta.5 safe library
publication rely on `/proc` and descriptor-relative filesystem operations.

The native tab retains import/preview, routing, tool selection, Prepare READY,
explicit Start, progress, STOP, explicit recovery, previous work and export.
Workers receive only the cartridge, selected tools and bounded system prompt.
Global/project instructions, skills, prompt templates and extensions are disabled;
operator-configured worker extensions can be explicitly enabled as a later
capability only after their compatibility is evidenced.

## Ownership boundary

Pi Web UI has shared-token access and no Open WebUI-style account identities.
Magazine issues durable random owner keys, scopes every run request to a
server-derived owner hash, and uses a separate server-side import key for shared
library changes. This preserves Magazine API owner separation across restarts.
All Pi host operators with filesystem/terminal access retain host authority;
Magazine does not turn the shared host into a secure multi-tenant service.

## Verification boundaries

Reproduce the original tests only against the isolated copy. Test the real Pi
SDK against a deterministic local provider separately from live configured LLM
inference. Test active abort, no output inheritance, exact saved sessions,
model routing, restart recovery and owner separation. Fresh saved sessions are
kept as audit evidence; canonical exported results remain the beta.5 text format.
Record unavailable or uncertain host capabilities explicitly.
