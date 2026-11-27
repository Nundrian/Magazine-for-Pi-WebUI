"""Token accounting from Pi's persisted assistant and native-compaction usage.

Pi (pi-ai 1.x) stores ``usage = {input, output, cacheRead, cacheWrite,
totalTokens, reasoning?, cost}`` on every saved assistant message, one per
model round. ``input`` excludes cache reads/writes and ``totalTokens`` is
``input + output + cacheRead + cacheWrite``; ``reasoning`` is a subset of
``output`` and is never added. A provider that reports no usage leaves the
whole record at zero, so an all-zero record is *unavailable*, not zero.

An attempt's snapshot is recomputed from its saved session file and replaces
the previous snapshot (never incremented), so re-reading cannot double count.
Cartridge and run totals are recomputed from distinct attempt snapshots.
"""
from __future__ import annotations

import json
from pathlib import Path

FIELDS = ("input", "output", "cache_read", "cache_write", "total")
_PI = {"input": "input", "output": "output", "cache_read": "cacheRead",
       "cache_write": "cacheWrite", "total": "totalTokens"}


def unavailable(reason="usage_not_recorded"):
    return {"state": "unavailable", "available": False, "incomplete": True, **{k: None for k in FIELDS},
            "reasoning": None, "rounds": 0, "rounds_reported": 0, "source": reason}


def message_usage(usage):
    """Normalized usage of one assistant message, or None when not reported."""
    if not isinstance(usage, dict):
        return None
    values = {}
    for key, pi in _PI.items():
        v = usage.get(pi)
        values[key] = v if type(v) is int and v >= 0 else None
    if values["total"] is None and None not in (values["input"], values["output"]):
        values["total"] = sum(values[k] or 0 for k in ("input", "output", "cache_read", "cache_write"))
    if not any(values[k] for k in FIELDS):
        return None  # Pi's zero-filled placeholder: the provider reported nothing
    r = usage.get("reasoning")
    values["reasoning"] = r if type(r) is int and r >= 0 else None
    return values


def from_session(path, *, finished_normally):
    """Attempt snapshot from the persisted session JSONL (complete lines only)."""
    try:
        raw = Path(path).read_bytes()
    except (OSError, TypeError):
        return unavailable("session_unavailable")
    complete = raw if raw.endswith(b"\n") else raw[:raw.rfind(b"\n") + 1]
    seen, rounds, reported = set(), 0, []
    compaction_rounds, compaction_reported = 0, []
    for line in complete.decode("utf-8", "replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        kind, message = entry.get("type"), entry.get("message")
        if kind == "compaction":
            usage = entry.get("usage")
        elif kind == "message" and isinstance(message, dict) and message.get("role") == "assistant":
            usage = message.get("usage")
        else:
            continue
        identity = (kind, entry.get("id") or (entry.get("timestamp"), rounds))
        if identity in seen:
            continue
        seen.add(identity)
        rounds += 1
        value = message_usage(usage)
        if value is not None:
            reported.append(value)
        if kind == "compaction":
            compaction_rounds += 1
            if value is not None:
                compaction_reported.append(value)
    result = summarize(rounds, reported, finished_normally, partial_tail=complete != raw)
    if compaction_rounds:
        result["compaction"] = summarize(compaction_rounds, compaction_reported, True, partial_tail=complete != raw)
        result["compaction"]["source"] = "pi_compaction_usage"
    return result


def summarize(rounds, reported, finished_normally, partial_tail=False):
    if not reported:
        out = unavailable("usage_not_reported" if rounds else "no_saved_rounds")
        out["rounds"] = rounds
        return out
    result = {"available": True, "rounds": rounds, "rounds_reported": len(reported), "source": "pi_session_usage"}
    for key in (*FIELDS, "reasoning"):
        values = [v[key] for v in reported if v.get(key) is not None]
        result[key] = sum(values) if values else None
    missing_dim = any(v.get(k) is None for v in reported for k in ("input", "output", "total"))
    incomplete = (len(reported) < rounds or missing_dim or not finished_normally or partial_tail)
    result["incomplete"] = incomplete
    result["state"] = "partial" if incomplete else "known"
    return result


def live(rounds_usage, rounds):
    """Running display total from forwarded message_end events (replaced later)."""
    reported = [u for u in (message_usage(x) for x in rounds_usage) if u is not None]
    return summarize(rounds, reported, finished_normally=False)


def total(snapshots):
    """Sum distinct attempt snapshots (callers pass each attempt once)."""
    snaps = [s or unavailable() for s in snapshots]
    out = {"attempts": len(snaps), "unavailable_attempts": sum(not s.get("available") for s in snaps),
           "available": any(s.get("available") for s in snaps)}
    for key in (*FIELDS, "reasoning"):
        values = [s[key] for s in snaps if type(s.get(key)) is int]
        out[key] = sum(values) if values else None
    out["incomplete"] = (not snaps) or any(not s.get("available") or s.get("incomplete") for s in snaps)
    out["state"] = ("unavailable" if not out["available"] else ("partial" if out["incomplete"] else "known"))
    return out


def fmt(value):
    t = value or {}
    if not t.get("available"):
        return "unavailable"
    parts = [f"{t.get(k) if t.get(k) is not None else 'unavailable'}" for k in ("input", "output", "total")]
    extra = []
    if t.get("cache_read"):
        extra.append(f"cache read {t['cache_read']}")
    if t.get("cache_write"):
        extra.append(f"cache write {t['cache_write']}")
    return " / ".join(parts) + (f" ({', '.join(extra)})" if extra else "") + (" (incomplete)" if t.get("incomplete") else "")
