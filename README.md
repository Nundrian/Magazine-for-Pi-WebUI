# Magazine for Pi WebUI

**0.1.0-pi.8-ui.3** repairs Docker Desktop workflow import using verified
Linux-backed internal storage, preserving atomic no-overwrite publication.
It adds actionable filesystem diagnostics and fixes a demonstrated worker-exit
cleanup race. The ui.2 interface and all 29 core engine modules are retained.

Magazine runs prewritten bounded cartridges in fresh saved Pi sessions. Load a
workflow, inspect prompts, choose exact available models/tools, prepare READY,
then explicitly Start. Preparing, previewing and restoring work do not start
inference. Missing models are rejected without silent substitution.

For result-file workflows, Magazine checks native publisher receipts, identities,
saved-session/result hashes and declared handover pointers before committing and
advancing. COMPLETED establishes the execution/publication contract; inspect
actual tool outputs and deliverables for task correctness. Local acceptance
found both a completed model run and model/provider failures, all documented.

Current tested deployment: **Pi WebUI 0.120.0, SDK 1.5.2, Node 22.23.5,
Python 3.11, Docker Desktop Linux containers**. Existing exact compatibility
pairs remain in `magazine-pi/compatibility.json`; this repair's full suite used
SDK 1.5.2. The separate Windows Pi CLI is not the target.

- [Installation](docs/INSTALL.md)
- [Linux storage, verified migration and rollback](docs/LINUX-STORAGE.md)
- [Repair and actual local-model acceptance](docs/IMPORT-REPAIR.md)
- [Operator controls](docs/UI8-OPERATOR-GUIDE.md)
- [Security and tool authority](docs/SECURITY.md)
- [Tests and historical evidence](docs/TESTING.md)

The plugin is `magazine-pi/`. Build allowlisted ZIPs with
`python scripts/build_release.py`. Archives exclude private config, models,
credentials, sessions, runs and workspaces. Run tests with the documented
PYTHONPATH and actual host SDK path. Automated providers are deterministic
loopback fixtures, separate from real local-model acceptance.

This repository contains the complete source. Earlier reports are historical;
the import repair report records current checks. No Open WebUI service is required
or modified. Magazine for Open WebUI is a separate implementation. MIT licensed.

Update only at confirmed idle, preserve state/config and keep rollback copies.
After new work, removing the Linux mount would reveal stale Windows records.
