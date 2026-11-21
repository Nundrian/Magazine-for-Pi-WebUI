# Compatibility report — Magazine 0.1.0-pi.6-rc.1 (17 November 2026)

## Versions inspected

| What | Version / identity | How established |
|---|---|---|
| Upstream Pi Web UI | `main` at `8e5345d6b01da5e3a05ea535c5ffd9b752066fe7` = 0.121.0 (tag `v0.121.0` = `6142308`) | git clone, 17 Nov 2026 |
| Latest Pi Web UI on npm | 0.120.0 (0.121.0 not yet published) | `npm view pi-web-ui version` |
| Owner's dedicated Magazine host | Pi Web UI **0.96.1**, SDK **1.0.2** (pi-ai/agent-core 1.0.2), Node 24.22.3, Python 3.11 venv | read-only: that image's lock file and Dockerfile (paths in the owner-only environment note) |
| Owner's production Pi source | package **0.115.0**, git `7c9d300` = `v0.115.0` + 2 commits (no plugin-contract change), lock pins SDK **1.1.0**, pi-mcp 1.4.0 | read-only: the production source checkout |
| Production runtime image | upstream Dockerfile: Node 22, runtime stage **without python3**, `USER node` | read-only Dockerfile inspection |
| Pi SDK on npm | 1.5.2 latest; a fresh `npm i pi-web-ui@0.120.0` resolves SDK 1.5.2 (optional dependency `>=1.1.0`) | npm |

The running containers were **not** queried. Docker is not reachable from this
session, and the brief rules out touching them. The production container's exact
running build therefore remains an inference from its source tree.

## Contracts compared (0.96.1 → 0.115.0 → 0.121.0)

| Contract | Finding | Effect on Magazine |
|---|---|---|
| Plugin API | `PLUGIN_API_VERSION = 2` in all three; `host.dir`, `host.dataDir`, `host.route`, `host.log`, `host.effect` unchanged | none |
| Plugin routes | `/plugins-api/:id/*`; JSON body parser 10 MiB; 0.121 adds an optional `clientId` workspace context | binary `/upload` streaming still works (verified on real hosts) |
| Static client | only `client/*` served; 0.115+ adds an import-boundary checker | Magazine's client has no imports (test-enforced); real hosts serve it and do not serve server files |
| Manifest `engines` | honoured by 0.115+, unreliable in 0.96.1 | Magazine keeps its own exact host+SDK check |
| `createMockHost` | in `plugin-sdk/index.mjs` (0.96.1, 0.115.0), `plugin-sdk/testing.mjs` (0.120+) | lifecycle tests use the host's own harness |
| SDK `createAgentSession` | `tools`, `noTools`, `customTools`, `modelRuntime`, `sessionManager` stable | publisher passed as `customTools` |
| SDK `tools` allowlist | 1.1.0+ accepts `*` patterns and `+name`/`-name` | Magazine now refuses non-plain tool names and verifies the exact active set |
| MCP (1.1.0+) | MCP tools come from a CLI-level extension; plain allowlists keep MCP registered for codemode/tool search | worker loads no extensions; a test with `mcp.json` in agentDir and project proved no server starts; active set verified |
| Events | `message_end`, `tool_execution_end` (`toolCallId`, `toolName`, `result`, `isError`) identical | Antiloop source |
| Usage | `{input, output, cacheRead, cacheWrite, totalTokens, reasoning?}`; input excludes cache; total = sum; zeros when the provider reports nothing | token accounting rules |
| Session JSONL | header `{type:"session", id}`, `message` entries with `id` | session identity and dedupe |
| Package exports | `@earendil-works/pi-coding-agent/package.json` is not exported | resolution by `require.resolve.paths`, not `require.resolve` (fixed in index.mjs and worker.mjs) |
| Old adapter settings | in-memory settings disable compaction, retry and cache warming; no context files, skills, templates or extensions | still accepted by 1.0.2/1.1.0/1.5.2; unchanged (no reason found to change) |

## Version gate behaviour

- `index.mjs` finds the running `pi-web-ui` package from the host entry path and
  the SDK that the host resolves. `MAGAZINE_PI_SDK` must agree with it. Any pair
  outside `compatibility.json` is refused before anything is created.
- `worker.mjs` refuses an SDK that differs from the activation-time version or is
  not in the table.
- An operator may acknowledge one exact unverified pair (`compatibilityOverride`).

## Remaining uncertainties

- 0.121.0 is not on npm, so it is refused. Its plugin contract appears unchanged from
  0.120.0 in source, but it was not run.
- Node 24 (Pi Magazine image) was not re-run here (container has Node 22). The worker
  uses no Node-24-only API. The pi.5 acceptance ran on Node 24.
- 0.115.0/1.1.0 and 0.120.0/1.5.2 have no live-model acceptance.
- The production 0.115.0 image lacks Python, so Magazine cannot run there without an
  image change. Not attempted.
