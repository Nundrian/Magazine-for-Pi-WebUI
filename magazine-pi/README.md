# Magazine for Pi WebUI — 0.1.0-pi.10.5 reviewed candidate

Native Pi plugin ID `magazine-pi`. Run bounded cartridges in fresh saved worker
sessions, using explicit models and tools, committed results and durable records.

0.1.0-pi.9 confines every worker with Linux Landlock and seccomp, verifies
Magazine's own files after every attempt, keeps Stop available when run state is
damaged, adds an explicit Repair state action and restore-point management, and
halts at once on deterministic model incompatibilities. See
[PI9-RELIABILITY.md](docs/PI9-RELIABILITY.md).

The interface follows the owner's Open WebUI layout. Choose **Workflows → Load your workflow → Set up → Prepare
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
correctness. Network access and signals are not confined (see PI9-RELIABILITY.md).

Requires Linux, Node and Python 3.11 with requirements.txt. Recommended tested pair: Pi WebUI 0.120.0 / SDK 1.5.2. Other inherited allowed pairs are 0.96.1/1.0.2 and 0.115.0/1.1.0; they have not been reaccepted for this candidate. Unknown pairs are refused.

pi.10.5 adds workflow and cartridge reasoning, bulk and Frontier model assignments, native compaction, corrected usage accounting, refreshed Manage controls and workspace-local recoverable trash. Truncated SDK responses are classified using the terminal event and saved transcript.

The final isolated acceptance on 27 November 2026 passed 1,066 Linux regression tests (one legacy skip), 21 JavaScript/native-host tests and Firefox interface tests. Three real-model workflows committed all 28 cartridges (8/8, 8/8 and 12/12) using Qwen and Ornith 9B models. Tests covered sequential and round-robin scheduling, restart/recovery, native compaction, model/reasoning selection and deletion/restoration. A maximum of one upstream inference request was observed during the round-robin runs. Earlier failed attempts and partial usage were retained, not counted as successful attempts. The legacy pi.5 upgrade test requires its historical archive and SDK 1.0.2. Other host/SDK pairs, private external integrations and full project acceptance were not re-certified. Production installation is a separate step.

Read [pi.10.5 corrective notes](docs/PI105-CORRECTIVE.md) before upgrade or rollback.

Ollama workflow selectors now follow the installed chat-model library automatically,
without rewriting configuration or saved assignments. See [automatic models](docs/AUTOMATIC-MODELS.md).
