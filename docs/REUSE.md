# Reused and modified components

| Component | Treatment | Evidence |
|---|---|---|
| 29 canonical Magazine Python modules | Copied byte-for-byte from frozen Beta 5 | `evidence/reused-components.json` core hashes |
| Validation, state, ownership fencing, sequencing, result durability, STOP and recovery | Run through the original core | 611 original core tests and new SDK integration tests |
| Beta 5 shared-library validation/publication, ZIP retention, Finished Work exports, result/status/STOP/recovery helpers | 72 source segments extracted verbatim | Recorded original line and normalized-text hash for each segment |
| Host data root and metadata writing | Bound to Pi data storage; metadata writes use core atomic writer | `service.py`; restart/integrity tests |
| Open WebUI model/tool discovery, authentication and routes | Pi SDK catalogue and host-protected native plugin routes; pi.5 removes Magazine credentials | Real installed host/SDK tests |
| Open WebUI fresh-chat dispatch and chat deletion | Replaced by one fresh Pi SDK worker process per cartridge; actual saved JSONL kept for audit | `worker.mjs`, `pi_dispatch.py`, retained test transcripts |
| Open WebUI HTML/front-end sidebar patch | Replaced by a native plugin view with the same preparation/execution workflow | Native browser acceptance |
| Cartridge tool surface | Explicit operator allowlist plus frozen per-run subset; optional explicit Pi extensions | Real write/read/extension tool tests |

The Python core is the only Magazine execution engine. Node owns the native
host connection and invokes Pi's existing SDK; it does not sequence cartridges,
write canonical state, or invent recovery transitions. A private stdin/stdout
process boundary accommodates the existing Python core without a web service,
database, or parallel queue implementation.

The extracted helper functions still contain some Open WebUI historical comments
and error strings. Their source is intentionally preserved; no Open WebUI module
is required at runtime. The generated helper module uses injected neutral core
modules and Pi storage roots. `scripts/prepare_reference.py` documents extraction
from the isolated release copy. The installable plugin already includes all
reused files and does not need the original project or reference folder.

The Pi permission boundary is explicitly documented in `SECURITY.md`.
Magazine's model/tool selection and Pi host access are retained; private
Pi Web UI chat permission wrappers are not transplanted into a second engine.
Automatic output chaining, unattended repair, virtual routing, semantic quality
certification and an ASTER dependency were not introduced.
