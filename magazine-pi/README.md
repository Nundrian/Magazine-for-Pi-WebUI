# Magazine for Pi WebUI — 0.1.0-pi.8-ui.2

Native Pi plugin ID `magazine-pi`. Run bounded cartridges in fresh saved worker
sessions, using explicit models and tools, committed results and durable records.

This release aligns the appearance and loading/running journey with the owner's
Open WebUI interface. Choose **Workflows → Load your workflow → Set up → Prepare
workflow**, review the READY run, then **Start workflow**. Previous work has its
own selector and View button. Preparing, viewing and restoring never start work.

The individual-cartridge ledger format is retained: one selected ID, its full
summary, all retry attempts, expandable evidence and links into the full Chats
history. Chats includes old and live Magazine conversations, read-only. Results
shows verified handovers separately from the final reply. Manage separates
workflow-template deletion from saved-run deletion with recoverable trash.

Read [installation](docs/INSTALL.md), [alignment and checks](docs/UI8-ALIGNMENT.md),
[operator guide](docs/UI8-OPERATOR-GUIDE.md) and [security](docs/SECURITY.md).

The runtime preserves the 29 core modules and bundled beta.5 helper, the five
attempt ceiling, 1,800 seconds per fresh attempt by default, Antiloop, explicit
STOP/recovery and sequential or Round Robin scheduling. A stopped started run
is not resumable. COMPLETED records verified execution/results, not real-world
correctness. A tool allowlist is not an OS sandbox.

Requires Linux, Node and Python 3.11 with requirements.txt. Current UI acceptance
covers host 0.120.0 / SDK 1.5.2 with scripted providers and the installed interface.
Other inherited allowed pairs are 0.96.1/1.0.2 and 0.115.0/1.1.0; they have not
been reaccepted for this alignment release. Unknown pairs are refused. No real
local LLM inference or comparative model benchmark was used for this UI update.
