"""Per-run attempt ledger: durable record of every worker attempt.

ledger.json is written atomically by the run's single executor (or by
reconciliation when no executor exists). It never drives core run state; the
unchanged core state.json/execution.json/control.json remain authoritative
for READY/RUNNING/HALTED/COMPLETED and STOP.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import tokens
from magazine.durability import atomic_write

LEDGER_VERSION = 1
HARD_CEILING = 5
# reliability.3 completion omissions; Pi equivalents are listed explicitly.
COMPLETION_CODES = {"DONE_MISSING", "OUTPUT_MISSING", "NO_FINAL_ANSWER", "INCOMPLETE_RESPONSE"}


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def path(run_base):
    return Path(run_base) / "ledger.json"


def load(run_base):
    p = path(run_base)
    try:
        value = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict) or value.get("version") != LEDGER_VERSION:
        raise ValueError("Unsupported Magazine ledger")
    return value


def new(run_id, cartridge_ids, protocol_mode):
    return {"version": LEDGER_VERSION, "run_id": run_id, "protocol": protocol_mode,
            "order": list(cartridge_ids),
            "cartridges": {cid: {"status": "pending", "attempts": []} for cid in cartridge_ids},
            "tokens": tokens.total([]), "activity": []}


def recompute(ledger):
    every = []
    for record in ledger["cartridges"].values():
        snaps = [a.get("tokens") for a in record["attempts"]]
        record["tokens"] = tokens.total(snaps)
        every.extend(snaps)
    ledger["tokens"] = tokens.total(every)
    return ledger


def save(run_base, ledger):
    recompute(ledger)
    ledger["updated"] = now_iso()
    atomic_write(str(path(run_base)), (json.dumps(ledger, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))


def note(ledger, text, cid=None, level="info"):
    ledger.setdefault("activity", []).append({"at": now_iso(), "cartridge_id": cid, "level": level, "text": text[:400]})
    ledger["activity"] = ledger["activity"][-200:]


def completion_failure(attempt):
    return attempt.get("code") in COMPLETION_CODES or attempt.get("recovery") == "publish"


def decide_retry(attempt, total_attempts, automatic_remaining, *, repeated=False, stop_requested=False):
    """reliability.3 retry admission (decide_retry), with Pi completion codes."""
    code = attempt.get("code")
    completion = completion_failure(attempt)
    decision = {"attempt_number": total_attempts, "max_total_attempts": HARD_CEILING,
                "automatic_attempts_remaining": max(0, automatic_remaining),
                "fingerprint_repeated": repeated, "recoverable_completion_failure": completion}
    if attempt["status"] == "done":
        action, reason_code, reason = "complete", "RESULT_COMMITTED", "Verified result committed; no retry needed."
    elif stop_requested or attempt["status"] == "stopped":
        action, reason_code, reason = "stop", "OPERATOR_STOP", "Stopped by the operator; no automatic retry."
    elif attempt["status"] == "halted":
        action, reason_code, reason = "stop", "HALT_RECEIVED", "The model returned HALT; no automatic retry."
    elif attempt["status"] == "interrupted":
        action, reason_code, reason = "stop", "CANCELLED", "Execution was interrupted; no automatic retry."
    elif attempt["status"] != "failed" or attempt.get("recovery") not in ("publish", "repair"):
        action, reason_code, reason = "stop", "NON_RETRYABLE_FAILURE", f"Non-retryable failure ({code or attempt['status']}); no automatic retry."
    elif total_attempts >= HARD_CEILING:
        action, reason_code, reason = "stop", "MAX_ATTEMPTS_REACHED", "Five total attempts exhausted; no sixth attempt will start."
    elif automatic_remaining <= 0:
        action, reason_code, reason = "stop", "CONFIGURED_ATTEMPTS_EXHAUSTED", "Configured automatic attempt allowance exhausted."
    elif repeated and not completion:
        action, reason_code, reason = "stop", "NO_PROGRESS", "Repeated failure without verified progress."
    elif completion:
        action, reason_code, reason = "retry", "RECOVERABLE_COMPLETION", "Completion or verified handover is missing; retry in a fresh session with a full time allowance."
    else:
        action, reason_code, reason = "retry", "RECOVERABLE_FAILURE", "Recoverable failure; retry in a fresh session with a full time allowance."
    decision.update(decision=action, reason_code=reason_code, reason=reason)
    return decision


def retry_text(attempt):
    value = attempt.get("retry_decision")
    if value:
        return value["decision"].capitalize() + ": " + value["reason"]
    return {"done": "Finished.", "running": "In progress."}.get(attempt.get("status"), "")


def summary(ledger):
    """Compact per-cartridge view for the native UI (no prompts or replies)."""
    if not ledger:
        return None
    rows = []
    for cid in ledger["order"]:
        record = ledger["cartridges"][cid]
        attempts = record["attempts"]
        elapsed = 0
        for a in attempts:
            try:
                start = _dt.datetime.fromisoformat(a["started"])
                end = _dt.datetime.fromisoformat(a["finished"]) if a.get("finished") else None
            except (KeyError, TypeError, ValueError):
                continue
            if end is not None:
                elapsed += max(0, int((end - start).total_seconds()))
        rows.append({"id": cid, "status": record["status"], "attempts": len(attempts),
                     "model": attempts[-1].get("model") if attempts else None,
                     "elapsed_seconds": elapsed if attempts else None,
                     "active": bool(attempts and attempts[-1].get("status") == "running"),
                     "tokens": record.get("tokens"),
                     "last_code": attempts[-1].get("code") if attempts else None,
                     "last_decision": (attempts[-1].get("retry_decision") or {}).get("reason_code") if attempts else None,
                     "warnings": sum(len((a.get("loop_police") or {}).get("warnings") or []) for a in attempts)})
    return {"protocol": ledger.get("protocol"), "tokens": ledger.get("tokens"), "cartridges": rows,
            "activity": ledger.get("activity", [])[-40:]}


def _fmt_time(value):
    return value.replace("T", " ").replace("+00:00", " UTC") if value else ""


def render(ledger, meta):
    lines = [f"# Ledger: {meta.get('name') or ledger['run_id']}", "",
             f"- Run: `{ledger['run_id']}`", f"- Protocol: {ledger.get('protocol')}",
             f"- Antiloop: {'On' if (meta.get('loop_police') or {}).get('enabled') else 'Off'}",
             f"- Tools: {', '.join(meta.get('tool_ids') or []) or 'none'}",
             f"- Tokens (input / output / total): {tokens.fmt(ledger.get('tokens'))}", "",
             "| # | Cartridge | Attempt | Kind | Model | Status | Started | Finished | Session | Tokens in/out/total | Failure | Retry decision |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for index, cid in enumerate(ledger["order"], 1):
        record = ledger["cartridges"][cid]
        if not record["attempts"]:
            lines.append(f"| {index} | {cid} | â€“ | | | {record['status']} | | | | unavailable | | |")
        for a in record["attempts"]:
            cell = lambda v: str(v or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {index} | {cell(cid)} | {a.get('attempt_number')} | {a.get('kind', '')} | `{cell(a.get('model'))}` | "
                         f"{a.get('status')} | {_fmt_time(a.get('started'))} | {_fmt_time(a.get('finished'))} | "
                         f"{cell(a.get('session_id'))} | {tokens.fmt(a.get('tokens'))} | {cell(a.get('code'))} | {cell(retry_text(a))} |")
    execution = [(cid, attempt) for cid in ledger["order"]
                 for attempt in ledger["cartridges"][cid]["attempts"] if attempt.get("execution_settings")]
    if execution:
        lines += ["", "## Execution settings", ""]
        for cid, attempt in execution:
            settings = attempt["execution_settings"]
            level = settings["effective_thinking_level"]
            requested = settings["requested_thinking_level"]
            enabled = settings["compaction"]["enabled"]
            events = attempt.get("compaction_events", [])
            lines.append(f"- **{cid}, attempt {attempt['attempt_number']}:** {requested} resolved to {level}; "
                         f"native compaction {'enabled' if enabled else 'disabled'}, {len(events)} SDK events recorded.")
    lines += ["", "Cartridge tokens (input / output / total):"]
    lines += [f"- **{cid}:** {tokens.fmt(ledger['cartridges'][cid].get('tokens'))}" for cid in ledger["order"]]
    notes = [(cid, a.get("attempt_number"), a["note"]) for cid in ledger["order"]
             for a in ledger["cartridges"][cid]["attempts"] if a.get("note")]
    if notes:
        lines += ["", "## Notes", ""] + [f"- **{cid}, attempt {n}:** {text}" for cid, n, text in notes]
    lines += ["", "_Usage is Pi's persisted per-round usage; unavailable means not reported, never zero._", ""]
    return "\n".join(lines)
