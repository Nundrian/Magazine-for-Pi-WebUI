"""Antiloop over Pi's structured execution events.

Adapted from LoopPolice in magazine_simple_reliability3.py (Nundrian/Magazine
09a8706). Open WebUI's saved-chat polling is replaced by the Pi SDK event
stream forwarded by worker.mjs:

* ``assistant`` (message_end of an assistant message): the tool calls the
  model issued together in that message form one batch;
* ``tool_result`` (tool_execution_end): one completed or failed tool call.

Only completed calls with a known batch are counted; text, reasoning, silence
and generation speed never contribute. Call IDs are deduplicated, a parallel
batch contributes one representative event, and a successful mutating tool
resets repetition history unless it explicitly reports ``changed: false``.
"""
from __future__ import annotations

import hashlib
import json

from protocol import loop_policy

MAX_SEEN_IDS = 4096
MAX_WARNINGS = 16
MUTATING_WORDS = ("write_file", "edit_file", "apply_patch", "replace_file")
MUTATING_EXACT = {"write", "edit"}  # Pi built-in mutating tools


def _sig(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class LoopDetected(RuntimeError):
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__("Antiloop: " + json.dumps(evidence, sort_keys=True))


class LoopPolice:
    def __init__(self, policy=None):
        self.policy = loop_policy(policy)
        self.history, self.seen = [], set()
        self.warnings = []
        self.observed = 0
        self.limit_reached = False
        self.batches = []        # [[call ids]] in issue order
        self.calls = {}          # call id -> {name, argsSha256}
        self.results = {}        # call id -> result event
        self.done_batches = set()

    def snapshot(self):
        return {"enabled": self.policy["enabled"], "observed_events": self.observed,
                "warnings": list(self.warnings), "history": list(self.history),
                "identity_limit_reached": self.limit_reached}

    def observe(self, event):
        """Feed one forwarded worker event; raises LoopDetected on a confirmed loop."""
        if not self.policy["enabled"] or not isinstance(event, dict):
            return
        kind = event.get("event")
        if kind == "assistant":
            ids = []
            for call in event.get("toolCalls") or []:
                cid = call.get("id")
                if not isinstance(cid, str) or not cid or cid in self.calls:
                    continue  # a repeated snapshot of an already known call
                self.calls[cid] = {"name": call.get("name"), "argsSha256": call.get("argsSha256")}
                ids.append(cid)
            if ids:
                self.batches.append(ids)
            self._drain()
        elif kind == "tool_result":
            cid = event.get("toolCallId")
            if isinstance(cid, str) and cid in self.calls and cid not in self.results:
                self.results[cid] = event
                self._drain()

    def _drain(self):
        for index, batch in enumerate(self.batches):
            if index in self.done_batches or not all(c in self.results for c in batch):
                continue
            self.done_batches.add(index)
            call_id = batch[-1]
            call, result = self.calls[call_id], self.results[call_id]
            self._count({"id": call_id, "name": call.get("name"), "args": call.get("argsSha256"),
                         "result": result.get("resultSha256"), "error": result.get("isError") is True,
                         "no_change": result.get("noChange") is True, "batch_size": len(batch)})

    def _count(self, event):
        if event["id"] in self.seen:
            return
        if len(self.seen) >= MAX_SEEN_IDS:
            self.limit_reached = True
            return  # bounded identity state; never recount evicted events
        self.seen.add(event["id"])
        self.observed += 1
        name = (event.get("name") or "").lower()
        mutating = name in MUTATING_EXACT or any(word in name for word in MUTATING_WORDS)
        if mutating and not event["error"] and not event["no_change"]:
            self.history.clear()  # a successful change is progress evidence
            return
        sig = _sig(event["name"], event["args"], event["result"], event["error"])
        entry = {"signature": sig, "call_id": event["id"], "tool": event["name"], "error": event["error"],
                 "batch_size": event["batch_size"]}
        self.history.append(entry)
        self.history = self.history[-self.policy["history_size"]:]
        signatures = [e["signature"] for e in self.history]
        streak = 0
        for s in reversed(signatures):
            if s != sig:
                break
            streak += 1
        reason = None
        if event["error"] and streak >= self.policy["error_repeats"]:
            reason = "identical_errors"
        elif streak >= self.policy["stop_repeats"]:
            reason = "identical_call_result"
        elif streak == self.policy["warn_repeats"]:
            self.warnings.append(dict(entry, repetitions=streak))
            self.warnings = self.warnings[-MAX_WARNINGS:]
        cycles = self.policy["cycle_repeats"]
        for width in range(2, 5):
            if len(set(signatures[-width:])) < 2:
                continue
            if len(signatures) >= width * cycles and signatures[-width:] * cycles == signatures[-width * cycles:]:
                reason = reason or "short_cycle"
        if reason:
            raise LoopDetected({"reason": reason, "signature": sig, "repetitions": streak,
                                "events": self.history[-min(len(self.history), 4 * cycles):]})
