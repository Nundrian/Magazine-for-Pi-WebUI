# Magazine for Pi WebUI 0.120.0 (Pi SDK 1.5.2)

> [!IMPORTANT]
> **Use Pi WebUI 0.120.0 with Pi SDK 1.5.2.** This is the recommended tested pair for the current candidate. Pi 0.122.0 / SDK 1.6.1 failed the confined-worker integrity check in local testing. Do not bypass these safeguards or assume a newer host/SDK is compatible. See [current installation notes](docs/PI105-CORRECTIVE.md).

**Give your AI one clear job at a time. Keep the results and see what happened.**

Magazine is a general-purpose workflow runner for Pi WebUI. It takes a task that you have split into small,
ordered steps and runs them using the models you choose. Each step is called a
**cartridge**. Each attempt gets a fresh, saved conversation, so it does not have
to carry the whole previous chat into the next job.

Use cartridges for software, research, documents, data processing and other staged work.
External application connections are optional. See the [pi.10.5 corrective notes](docs/PI105-CORRECTIVE.md) for current execution settings and rollout requirements.

It is designed to make larger tasks more manageable for smaller models, while
remaining usable with larger models too. It is a prompt sequencer, not an AI
that invents your plan or a promise that a model will get everything right.

![Magazine Overview with arrows explaining the selected run, scheduling and saved work](docs/images/overview-annotated.jpg)

*Screenshots show the real Magazine client with safe example records. The models,
results and token figures shown are illustrations, not a live-model benchmark.*

## How you use it

1. Open **Plugins → Magazine** in Pi WebUI.
2. Import a workflow ZIP, or load a valid JSON workflow.
3. Read the steps. Choose a model and only the tools the work needs.
4. Choose **Prepare workflow**. This saves a **READY** run; nothing executes yet.
5. Review it, then choose **Start workflow**.
6. Read the results, inspect the chats and download the records you need.

Workers run inside a kernel-enforced sandbox: a model can change your project
workspace but cannot open or rewrite Magazine's own records or Pi's configuration;
permission changes are detected and repaired. Network access and signals are not
confined ([details and limits](docs/PI9-RELIABILITY.md)).

Use one compatible Pi model throughout, or assign different models to individual
steps. Choose models and reasoning levels manually, in bulk or through a reviewed
Frontier assignment plan (schema 1.0 or 1.1). New runs freeze execution settings
at Prepare; native SDK compaction can operate within a cartridge session. Magazine does not require a particular LLM and does not silently replace
your chosen model. There is no separate Magazine login, owner key or import key.
Pi's own access controls still apply.

## What each tab does

| Tab | What you use it for |
| --- | --- |
| [Overview](docs/VISUAL-GUIDE.md#overview) | Find saved runs, see progress and manage the shared execution queue. |
| [Workflows](docs/VISUAL-GUIDE.md#workflows) | Import and load reusable workflow templates. |
| [Set up](docs/VISUAL-GUIDE.md#set-up) | Review prompts, choose models and tools, then prepare a run. |
| [Ledger](docs/VISUAL-GUIDE.md#ledger) | Inspect each cartridge's attempts, timings, errors and recorded checks. |
| [Chats](docs/VISUAL-GUIDE.md#chats) | Read the actual retained worker conversations and tool records. |
| [Results](docs/VISUAL-GUIDE.md#results) | Inspect committed results and download ZIP or complete-run JSONL exports. |
| [Manage](docs/VISUAL-GUIDE.md#manage) | Manage templates and saved runs separately; restore deleted records from trash. |

**[Read the guide to all seven tabs →](docs/VISUAL-GUIDE.md)**

## What the checks are for

For committed-result workflows, Magazine checks that the saved result belongs to
the right run, step, attempt and session. It checks the publisher's receipt,
required outputs and declared handovers before moving on. A handover is the
specific result or file that a later step needs from an earlier one.

Missing or changed evidence stops progression. Eligible failures can receive
bounded retries; STOP and explicit recovery remain under your control. The
ledger and chats keep the available evidence so you can inspect a problem.

**These checks do not prove that the answer is correct.** A matching file hash
proves that the recorded bytes match, not that the file does the right thing.
Review the deliverables and use real task-specific tests.

**[Technical guide: execution, checks, handovers and safeguards →](docs/TECHNICAL-GUIDE.md)**

## What's new in 0.1.0-pi.10.5

- **Model and reasoning controls:** automatic Ollama chat-model discovery, individual and bulk assignment, workflow and cartridge reasoning overrides, explicit inheritance of Pi settings, and reusable setup assignments.
- **Execution and recovery:** sequential or round-robin scheduling, guarded duplicate execution, explicit recovery without starting inference, and persistent results, chats and handovers.
- **Interface fixes:** stale allocation responses cannot overwrite newer choices; Manage refreshes completed run status and deletion controls without disturbing focus or confirmations.
- **Runtime fixes:** truncated SDK responses are correctly classified, native compaction events reach the ledger, and reported compaction tokens are counted once. Missing usage remains unknown rather than being reported as zero.
- **Safe file management:** workspace-local recoverable trash works across Windows-backed workspaces and Linux-managed state. Existing checkpoint and non-overwriting Finished Work export repairs are retained.

## Development status — 27 November 2026

Current source: **0.1.0-pi.10.5 (reviewed candidate)**, published on main. This supersedes pi.10.4. See [the full changelog](CHANGELOG.md) and [current corrective notes](docs/PI105-CORRECTIVE.md).

The final isolated acceptance on 27 November 2026 passed 1,066 Linux regression tests (one legacy skip), 21 JavaScript/native-host tests and Firefox interface tests. Three real-model workflows committed all 28 cartridges (8/8, 8/8 and 12/12) using Qwen and Ornith 9B models. Tests covered sequential and round-robin scheduling, restart/recovery, native compaction, model/reasoning selection and deletion/restoration. A maximum of one upstream inference request was observed during the round-robin runs. Earlier failed attempts and partial usage were retained, not counted as successful attempts. The legacy pi.5 upgrade test requires its historical archive and SDK 1.0.2. Other host/SDK pairs, private external integrations and full project acceptance were not re-certified. Production installation is a separate step.

**[Development roadmap and outstanding work](TODO.md)**.

## Install and compatibility

Current source version: **0.1.0-pi.10.5**: kernel-enforced worker isolation,
controller-integrity checks, dependable Stop and state repair, restore-point
management, automatic Ollama model discovery and guarded interrupted-run
reconciliation. See the [changelog](CHANGELOG.md). The tested Docker Desktop deployment uses
**Pi WebUI 0.120.0, SDK 1.5.2, Node 22.23.5 and Python 3.11**.
This is the WebUI plugin, not the separate Pi CLI or Magazine for Open WebUI.

Start with [current installation notes](docs/PI105-CORRECTIVE.md), then the [installation procedure](docs/INSTALL.md). Docker Desktop users should also read
[Linux storage and safe migration](docs/LINUX-STORAGE.md): Magazine's internal
state needs the supported atomic file operations. Keep backups and update only
when Magazine is idle. Never remove a current data mount and assume the older
directory underneath it contains your latest work.

This repository contains the complete source. To obtain the current plugin, clone the repository and stage its `magazine-pi/` directory using the existing supported installation procedure. See the [GitHub source instructions](docs/INSTALL.md#obtain-the-plugin-from-github). No separate GitHub Release is required. Documentation screenshots are not plugin runtime assets.

## More detail

- [Technical guide and source map](docs/TECHNICAL-GUIDE.md)
- [Automatic Ollama model discovery](docs/AUTOMATIC-MODELS.md)
- [Restart recovery and reconciliation](docs/RESTART-RECOVERY.md)
- [Restart-repair validation: 929 passed, 1 legacy skip](docs/RESTART-RECOVERY-ACCEPTANCE.md)
- [Earlier import repair and real-model qualifications](docs/IMPORT-REPAIR.md)
- [Test methods and historical results](docs/TESTING.md)
- [Security and tool authority](docs/SECURITY.md)
- [Model selection and JSONL format](docs/MODELS-AND-JSONL.md)

MIT licensed. No Open WebUI service is required or changed by this plugin.
