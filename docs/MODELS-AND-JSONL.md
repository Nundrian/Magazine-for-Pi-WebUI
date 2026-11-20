# Model selection and whole-run JSONL

## Models

Magazine is a general-purpose prompt sequencer. It has no preferred, default
installed, required or special-case LLM. Host-compatible physical model IDs are
discovered from Pi's configured SDK catalogue; virtual routers remain excluded
by the existing physical-model execution contract. Small and large models use
the same execution path. Provider configuration stays with the host.

Choose a workflow model. With **Assign a model to each cartridge**, leave a
cartridge on **Use workflow model** or choose an explicit override. IDs are
opaque; Magazine does not trim, rename, infer or silently substitute them.
Preparation and Start validate availability. Execution verifies the same ID.

Existing API clients can keep supplying `magazine`, `model_map`, and `tool_ids`.
Their exact document/map contract remains intact. Any legacy alias is just a
user-defined map key: its spelling never selects a model. Existing prepared
runs and workflow ZIPs need no migration.

New clients may supply `default_model` (a concrete host ID) and
`cartridge_models` (cartridge ID to concrete host ID or explicitly supplied alias).
Precedence is explicit cartridge assignment, supplied job-type alias mapping,
an explicit host ID in the cartridge's routing key, then the selected default.
Concrete override IDs take precedence over identically
named aliases. Invalid explicit mappings/overrides fail; they do not fall back.
Normalization into generic internal keys happens only for new preparations;
saved per-cartridge assignments are visible before Start. Prompts are unchanged.

## One-click whole-run download

**Download Run JSONL**, beside the existing run controls, downloads
`magazine-run-<run_id>.jsonl` through the same Pi-host-protected workspace RPC as
the existing ZIP. Action: `download_jsonl`, data: `{"run_id":"…"}`. No separate
export page, status restriction, new persistent format or execution hook exists.
The button is disabled when no run is selected. Every valid retained run has
exportable preparation/state data, even before its first execution.

Each UTF-8 line is one JSON object containing `run_id`, export `sequence`,
`kind`, `cartridge_id` (or null), `timestamp` (where recorded), `source`, and
the complete original `record` subject to credential redaction. Session rows
also include `source_line`, `session_id`, `model_id`, and `cartridge_candidates`.
Prompts, assistant content (including reasoning if retained), tool arguments,
tool results, error messages, parent IDs and timestamps remain inside `record`.
Content is never summarized or length-limited.

All retained sessions, including failed/stopped/recovered attempts, are included.
Original session line order is authoritative. The sequential sessions are
ordered by their recorded start timestamp (filename breaks ties). Export sequence
is a download index, not a claimed historical global event number. Preparation,
cartridge definitions and canonical response snapshots are also included.
Canonical response strings preserve Unicode and original CRLF.

State, execution claim, STOP control, active worker and Finished Work export
records are current snapshots, clearly distinguished from execution events.
Magazine historically overwrites state/claims rather than retaining a lifecycle
journal: past transitions, previous claims, discarded partial stream chunks and
other uncaptured details cannot be recovered by an exporter. Nothing is invented.
Session headers without a saved prompt, or duplicate prompt/model pairs, may not
uniquely identify a cartridge. Those rows retain their session identity and list
possible cartridge IDs rather than inventing an assignment. All definitions
remain present. Existing retention limits still apply.

A running session may have an unfinished final line. Only its committed complete
lines are exported, with an explicit tail-byte notice; retry later for more.
Snapshot changes during export cause a clear retry error rather than mixed data.
Malformed committed JSONL, missing completed results and unsafe/link sources are
refused. Export never acquires an execution claim or changes run persistence.

Runtime credential files, provider configuration and environment are not export
sources. Structured credential fields, bearer credentials, credential assignments
and URL user-info are redacted; retained legacy import/host secrets are
also removed if echoed. Redaction is marked `[REDACTED]`. Arbitrary unlabeled
secrets cannot be reliably recognized: treat transcripts as sensitive and review
before sharing. Existing OS-level tool authority and Pi host access apply.
The canonical response ZIP and per-cartridge exports are unchanged.


## pi.6 additions

The JSONL export also carries `protocol.json`, `ledger.json` (snapshots) and every committed
handover `result.json`/`commit.json` (`handover_snapshot`). Where the attempt ledger records
a session ID, the session's cartridge association comes from the ledger, not from prompt
matching. Delivered result_file_v1 prompts contain the exact cartridge prompt followed by
the protocol suffix, so prompt matching alone would fail. The results ZIP adds
`magazine-ledger/LEDGER.md`, `ledger.json` and the committed handover files. Token usage is
Pi's persisted per-round usage (`input` excludes cache; total = input + output + cache read
+ cache write; reasoning is a subset of output). All-zero records are provider-unreported
and shown as unavailable.
