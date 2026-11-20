"""Single-process Magazine scheduler: Sequential (default) or Round Robin.

Adapted from the Engine of Magazine Simple 0.3.0-scheduler.rc1 (journal,
set_mode, boundary, finished, retained entries after a restart). Open WebUI
credentials have no Pi counterpart, so a retained entry is resumed by an
explicit Start (after Prepare recovery where the core requires it), never
automatically.

Only one Magazine cartridge executes at a time in this plugin process. A run
waiting for its first turn stays READY in the unchanged core and enters it
only when it receives the slot. In Round Robin a run yields only at the start
of its next cartridge, after the core has durably committed the previous one
and every attempt, recovery and handover commit of that cartridge has ended;
it waits there (core RUNNING) for its next turn.

The journal ``scheduler-v1.json`` holds the mode, current entry and ordered
queue (run/owner identity, next cartridge index, whether the run has entered
the core). It never holds credentials, prompts or model parameters. A corrupt
or unwritable journal blocks new dispatch and is kept for inspection.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from magazine.durability import atomic_write

MODES = ("sequential", "round_robin")
RUN_ID = re.compile(r"[a-f0-9]{12}\Z")
OWNER = re.compile(r"(local|[a-f0-9]{64})\Z")


class SchedulerError(RuntimeError):
    def __init__(self, message, code=409):
        super().__init__(message)
        self.code = code


class QueueStopped(Exception):
    """STOP reached a run while it was waiting for its next turn."""


class Scheduler:
    def __init__(self, root, launcher):
        self.journal = Path(root) / "scheduler-v1.json"
        self.launcher = launcher        # async (entry) -> None; raises if it cannot start
        self.mode = "sequential"
        self.queue = []                 # ordered entries waiting for the slot
        self.current = None             # entry holding the slot
        self.waiters = {}               # run_id -> Future of a rotated, waiting run
        self.notes = {}                 # run_id -> why it could not start from the queue
        self.persistence_error = None
        self.closing = False
        self._lock = asyncio.Lock()
        if self.journal.exists():
            try:
                saved = json.loads(self.journal.read_bytes(), object_pairs_hook=_unique)
                if saved.get("schema_version") != 1 or saved.get("mode") not in MODES:
                    raise ValueError("Unsupported scheduler journal")
                self.mode = saved["mode"]
                entries = ([saved["current"]] if saved.get("current") else []) + list(saved.get("queue") or [])
                seen = set()
                for entry in entries:
                    if not isinstance(entry, dict) or not RUN_ID.fullmatch(str(entry.get("run_id", ""))) \
                            or not OWNER.fullmatch(str(entry.get("owner", ""))):
                        raise ValueError("Invalid queue identity")
                    if entry["run_id"] in seen or type(entry.get("from_index")) is not int or entry["from_index"] < 0 \
                            or type(entry.get("prepared")) is not bool:
                        raise ValueError("Invalid queue checkpoint")
                    seen.add(entry["run_id"])
                    # Nothing from a previous process generation runs unattended.
                    self.queue.append(dict(_clean(entry), needs_start=True))
                notes = saved.get("notes") or {}
                if isinstance(notes, dict):
                    self.notes = {k: str(v)[:500] for k, v in notes.items() if RUN_ID.fullmatch(str(k))}
            except Exception as exc:
                self.queue = []
                self.persistence_error = f"Scheduler journal requires repair: {exc}"

    # ------------------------------------------------------------ journal
    def _persist(self):
        if self.persistence_error:
            raise SchedulerError(self.persistence_error)
        try:
            raw = json.dumps({"schema_version": 1, "mode": self.mode,
                              "current": _clean(self.current) if self.current else None,
                              "queue": [_clean(e) for e in self.queue],
                              "notes": dict(list(self.notes.items())[-50:])}, separators=(",", ":")) + "\n"
            atomic_write(str(self.journal), raw.encode("utf-8"))
            if json.loads(self.journal.read_bytes()) != json.loads(raw):
                raise OSError("journal read-back mismatch")
        except Exception as exc:
            self.persistence_error = f"Scheduler checkpoint could not be saved: {exc}"
            raise SchedulerError(self.persistence_error) from exc

    def set_mode(self, mode):
        if mode not in MODES:
            raise SchedulerError("Choose sequential or round_robin", 400)
        self.mode = mode
        self._persist()  # observed at the next committed boundary; never cancels work

    # -------------------------------------------------------------- views
    def entry(self, rid):
        if self.current and self.current["run_id"] == rid:
            return self.current
        return next((e for e in self.queue if e["run_id"] == rid), None)

    def position(self, rid):
        return next((n for n, e in enumerate(self.queue, 1) if e["run_id"] == rid), None)

    def eligible(self, entry):
        if entry.get("needs_start"):
            return False
        return (not entry["prepared"]) or entry["run_id"] in self.waiters

    def next_eligible(self):
        return next((e for e in self.queue if self.eligible(e)), None)

    def describe(self, rid):
        entry = self.entry(rid)
        nxt = self.next_eligible()
        return {"holder": bool(self.current and self.current["run_id"] == rid),
                "position": self.position(rid), "queued": entry is not None and entry is not self.current,
                "next": bool(nxt and nxt["run_id"] == rid),
                "recovery_required": bool(entry and entry.get("needs_start")),
                "entered_core": bool(entry and entry["prepared"]),
                "next_index": entry["from_index"] if entry else None,
                "note": self.notes.get(rid)}

    def view(self):
        nxt = self.next_eligible()
        return {"mode": self.mode, "error": self.persistence_error,
                "current": self.current["run_id"] if self.current else None,
                "queue": [{"run_id": e["run_id"], "position": n, "next": e is nxt,
                           "recovery_required": bool(e.get("needs_start")), "entered_core": e["prepared"]}
                          for n, e in enumerate(self.queue, 1)]}

    # ---------------------------------------------------------- admission
    def admit(self, rid, owner, from_index):
        """Durable admission at an explicit Start; precedes any dispatch."""
        if self.persistence_error:
            raise SchedulerError(self.persistence_error)
        entry = self.entry(rid)
        if entry is not None and not entry.get("needs_start"):
            raise SchedulerError("This run is already running or waiting.")
        if entry is not None:
            # A retained entry keeps its place; the run re-enters the core afresh.
            entry.pop("needs_start", None)
            entry.update(prepared=False, from_index=from_index)
        else:
            self.queue.append({"run_id": rid, "owner": owner, "from_index": from_index, "prepared": False})
        self.notes.pop(rid, None)
        self._persist()

    def dequeue(self, rid):
        """Remove a run that has not entered the core (or is retained)."""
        entry = next((e for e in self.queue if e["run_id"] == rid), None)
        if entry is None or (entry["prepared"] and not entry.get("needs_start")):
            raise SchedulerError("Only a run waiting for its first turn, or a retained entry, can be removed from the queue.")
        self.queue.remove(entry)
        self._persist()

    def forget(self, rid):
        """Drop a retained entry whose run no longer needs scheduling."""
        before = len(self.queue)
        self.queue = [e for e in self.queue if not (e["run_id"] == rid and e.get("needs_start"))]
        if len(self.queue) != before:
            self._persist()

    # ----------------------------------------------------------- dispatch
    async def pump(self):
        if self.closing or self.persistence_error:
            return
        async with self._lock:
            while self.current is None and not self.closing:
                entry = self.next_eligible()
                if entry is None:
                    return
                self.queue.remove(entry)
                self.current = entry
                self._persist()
                rid = entry["run_id"]
                if entry["prepared"]:
                    future = self.waiters.pop(rid)
                    if not future.done():
                        future.set_result(True)
                    return
                try:
                    await self.launcher(entry)
                    entry["prepared"] = True
                    self._persist()
                    return
                except Exception as exc:
                    self.notes[rid] = f"Could not start from the queue: {exc}"[:500]
                    if self.current is entry:
                        self.current = None
                    self._persist()

    def schedule(self):
        if not self.closing:
            asyncio.ensure_future(self.pump())

    async def turn(self, rid, owner, index, first):
        """Called by the executor before each cartridge of ``rid``."""
        if self.current is None or self.current["run_id"] != rid:
            # A run that entered the core without the scheduler (should not happen).
            raise SchedulerError("Run is executing without the scheduler slot")
        self.current.update(from_index=index, prepared=True)
        self._persist()
        if first or self.mode != "round_robin" or self.next_eligible() is None:
            return
        entry = self.current
        self.current = None
        future = asyncio.get_running_loop().create_future()
        self.waiters[rid] = future
        self.queue.append(entry)
        self._persist()
        self.schedule()
        await future  # QueueStopped when STOP reaches the waiting run

    def cancel_wait(self, rid):
        future = self.waiters.pop(rid, None)
        if future is None:
            return False
        self.queue = [e for e in self.queue if e["run_id"] != rid]
        self._persist()
        if not future.done():
            future.set_exception(QueueStopped())
        return True

    def finished(self, rid):
        """The run's task ended (completed, halted, stopped or failed)."""
        if self.closing:
            return  # keep retained entries for the next process generation
        if self.current and self.current["run_id"] == rid:
            self.current = None
        self.waiters.pop(rid, None)
        self.queue = [e for e in self.queue if e["run_id"] != rid or e.get("needs_start")]
        try:
            self._persist()
        finally:
            self.schedule()

    def freeze(self):
        """Service shutdown: retain the queue; nothing is dispatched any more."""
        self.closing = True
        if self.current:
            self.queue.insert(0, dict(self.current, needs_start=True))
            self.current = None
        for entry in self.queue:
            entry["needs_start"] = True
        try:
            self._persist()
        except SchedulerError:
            pass


def _clean(entry):
    return {k: entry[k] for k in ("run_id", "owner", "from_index", "prepared") if k in entry}


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate journal key " + key)
        out[key] = value
    return out
