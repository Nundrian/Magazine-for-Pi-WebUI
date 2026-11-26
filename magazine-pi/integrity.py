"""Controller-integrity fence and explicit repair of damaged controller state.

The worker sandbox (``sandbox.py``) prevents workers from writing Magazine's
files. This module is the independent second line: it seals the controller
files before every attempt and verifies them after the worker has exited. Any
change, or any file a worker placed beside the run state, fails the attempt
closed. Magazine then restores its own last written bytes (which it holds in
memory) so that the run can still be halted normally, and keeps the foreign
bytes as evidence in ``quarantine/``.

``repair_state`` is the operator's explicit, non-executing recovery for a run
whose ``run/state.json`` is already unreadable (for example damaged before
this release). It never starts execution, never accepts a cartridge on the
strength of a worker's claim alone, and records every decision.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import stat
import time
from dataclasses import replace
from pathlib import Path

import beta5_helpers as h
import handover as ho
import ledger as lg
from magazine.durability import atomic_write

# Controller files that no worker may change while an attempt runs. control.json
# is excluded: the operator's STOP legitimately writes it during an attempt.
SEALED = ("run/state.json", "run/execution.json", "magazine.json", "meta.json", "protocol.json")
# Names Magazine itself keeps in run/ (plus its own transient atomic-write files).
RUN_DIR_NAMES = {"state.json", "execution.json", "control.json", ".lock", "emergency-stop.json", "results"}
_TRANSIENT_PREFIXES = (".magazine_state_", ".magazine_execution_", ".magazine_control_", ".magazine_emergency_",
                       "..lock.")
INTEGRITY_CODE = "CONTROLLER_STATE_MODIFIED"


class IntegrityError(ValueError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _read(path: Path):
    try:
        if path.is_symlink():
            return b"<symlink>"
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except PermissionError:
        return b"<unreadable>"


# ------------------------------------------------------------------ modes
# Workers cannot open Magazine's files, but no Landlock right governs chmod or
# utimes. Magazine owns every file under its root (same user), so it can always
# put the permissions it needs back: owner rwx on folders, owner rw on files.
HEALED = []  # (monotonic time, path) of every permission this process restored


def _heal_one(path, is_dir):
    try:
        st = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except PermissionError:
        return None  # an unreadable parent; healed in its own turn
    if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid():
        return None
    need = stat.S_IRWXU if is_dir else (stat.S_IRUSR | stat.S_IWUSR)
    if (is_dir and not stat.S_ISDIR(st.st_mode)) or (not is_dir and not stat.S_ISREG(st.st_mode)):
        return None
    if st.st_mode & need == need:
        return None
    try:
        os.chmod(path, stat.S_IMODE(st.st_mode) | need)  # lstat above: not a link
    except (OSError, NotImplementedError):
        return None
    HEALED.append((time.monotonic(), str(path)))
    return str(path)


def heal_full(root, budget=200000) -> list[str]:
    """Every folder and file under Magazine's root, without following links.

    Run after each worker exits (and at service start): a worker cannot open
    these files but could have removed their permissions anywhere in the tree.
    """
    healed, stack = [], [Path(root)]
    item = _heal_one(root, True)
    if item:
        healed.append(item)
    while stack and budget > 0:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            budget -= 1
            try:
                if entry.is_symlink():
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            item = _heal_one(entry.path, is_dir)
            if item:
                healed.append(item)
            if is_dir:
                stack.append(Path(entry.path))
    return healed


def heal_run(run_base) -> list[str]:
    """Restore owner access on one run's controller folders and files."""
    base = Path(run_base)
    healed = []
    for directory in (base.parent.parent, base.parent, base, base / "run", base / "run" / "results"):
        item = _heal_one(directory, True)
        if item:
            healed.append(item)
    for folder in (base, base / "run"):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if name.endswith(".json"):
                item = _heal_one(folder / name, False)
                if item:
                    healed.append(item)
    return healed


def heal_tree(root) -> list[str]:
    """Cheap whole-service check: top folders, every run's controller files."""
    root = Path(root)
    healed = [p for p in (_heal_one(root, True),) if p]
    for name in ("runs", "library", "trash", "temp", "preferences", "input-zips", "finished-work"):
        item = _heal_one(root / name, True)
        if item:
            healed.append(item)
    for name in ("scheduler-v1.json", "view-state.json", "models-store.json"):
        item = _heal_one(root / name, False)
        if item:
            healed.append(item)
    try:
        owners = [root / "runs" / o for o in os.listdir(root / "runs")]
    except OSError:
        owners = []
    for owner in owners:
        item = _heal_one(owner, True)
        if item:
            healed.append(item)
        try:
            runs = [owner / r for r in os.listdir(owner)]
        except OSError:
            continue
        for run in runs:
            healed += heal_run(run)
    return sorted(set(healed))


def _run_dir_names(run_base: Path) -> set[str]:
    try:
        return set(os.listdir(run_base / "run"))
    except FileNotFoundError:
        return set()


def foreign_run_files(run_base, before=()) -> list[str]:
    """Regular files or links in run/ that Magazine did not create.

    Directories are never treated as foreign: Magazine keeps its canonical
    results there, and moving a directory could hide controller data.
    """
    base = Path(run_base) / "run"
    names = _run_dir_names(Path(run_base)) - set(before)
    out = []
    for name in sorted(names):
        if name in RUN_DIR_NAMES or name.startswith(_TRANSIENT_PREFIXES):
            continue
        try:
            mode = os.lstat(base / name).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            out.append(name)
    return out


class Seal:
    """Exact controller bytes captured immediately before a worker starts."""

    def __init__(self, run_base):
        self.run_base = Path(run_base)
        self.started = time.monotonic()
        self.root = self.run_base.parents[2]  # <root>/runs/<owner>/<rid>
        self.files = {rel: _read(self.run_base / rel) for rel in SEALED}
        self.run_names = _run_dir_names(self.run_base)
        self.modes = {}
        for rel in (".", "run") + SEALED + ("ledger.json",):
            try:
                self.modes[rel] = stat.S_IMODE(os.lstat(self.run_base / rel).st_mode)
            except OSError:
                pass

    def changes(self, ledger_digest=None) -> list[str]:
        changed = []
        # Permission changes first: put Magazine's access back anywhere under its
        # root so the comparison below reads what is actually there. Repairs made
        # meanwhile by the service's per-request heal count as tampering too.
        heal_full(self.root)
        for at, path in list(HEALED):
            if at >= self.started and (path == str(self.root) or path.startswith(str(self.root) + os.sep)):
                changed.append("mode:" + os.path.relpath(path, self.run_base))
        for rel, mode in self.modes.items():
            try:
                now = stat.S_IMODE(os.lstat(self.run_base / rel).st_mode)
            except OSError:
                continue
            if now != mode:
                os.chmod(self.run_base / rel, mode, follow_symlinks=False)
                changed.append("mode:" + rel)
        changed = sorted(set(changed))
        changed += [rel for rel, data in self.files.items() if _read(self.run_base / rel) != data]
        changed += ["run/" + name for name in foreign_run_files(self.run_base, self.run_names)]
        if ledger_digest is not None:
            current = _read(lg.path(self.run_base))
            if current is None or sha256(current) != ledger_digest:
                changed.append("ledger.json")
        return changed

    def restore(self, changed, attempt_id) -> dict:
        """Quarantine foreign bytes, then put Magazine's own sealed bytes back."""
        quarantine = self.run_base / "quarantine" / f"{_now_stamp()}-{attempt_id}"
        quarantine.mkdir(mode=0o700, parents=True)
        evidence = []
        for rel in changed:
            if rel.startswith("mode:"):
                evidence.append({"path": rel[5:], "permissions_changed": True, "restored": True})
                continue
            source = self.run_base / rel
            data = _read(source)
            entry = {"path": rel, "present": data is not None}
            if data is not None:
                entry.update(bytes=len(data), sha256=sha256(data))
                target = quarantine / rel.replace("/", "__")
                original_missing = self.files.get(rel, b"") is None
                if source.is_symlink() or original_missing or (rel.startswith("run/") and rel[4:] not in RUN_DIR_NAMES):
                    os.replace(source, target)  # move a foreign file (or link) out of the run
                else:
                    shutil.copyfile(source, target, follow_symlinks=False)
            original = self.files.get(rel)
            if original is not None:
                atomic_write(str(source), original)
                entry["restored_sha256"] = sha256(original)
            evidence.append(entry)
        record = {"attempt_id": attempt_id, "detected_at": lg.now_iso(), "files": evidence,
                  "action": "Magazine restored its own last written controller files and halted the run."}
        atomic_write(str(quarantine / "manifest.json"), (json.dumps(record, indent=2) + "\n").encode())
        record["quarantine"] = quarantine.relative_to(self.run_base).as_posix()
        return record


# --------------------------------------------------------------- repair
def _iso_ts(value):
    try:
        return datetime.datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


def _state_problem(core, state_path):
    try:
        core["ss"].load_run_state(state_path)
        return None
    except FileNotFoundError:
        return "run/state.json is missing"
    except Exception as exc:  # malformed JSON or schema violation
        return f"{type(exc).__name__}: {exc}"


def _accepted(service, owner, rid, layout, ledger, protocol, cid):
    """Independent evidence that Magazine itself committed this cartridge."""
    record = (ledger.get("cartridges") or {}).get(cid) or {}
    attempts = record.get("attempts") or []
    if record.get("status") != "done" or not attempts or attempts[-1].get("status") != "done":
        return False, "no committed attempt in the ledger"
    if attempts[-1].get("integrity"):
        return False, "attempt rejected by the integrity check"
    if protocol.get("handover_mode") == "result_file_v1" and not protocol.get("legacy"):
        try:
            ho.committed_payload(layout, ledger, cid)
        except Exception as exc:
            return False, f"committed handover does not verify: {exc}"
        return True, "committed handover verifies"
    try:
        h._canonical_result_bytes(owner, rid, cid)
    except Exception as exc:
        return False, f"canonical result unavailable: {exc}"
    return True, "canonical result present"


def plan_repair(service, owner, rid):
    """Read-only analysis. Returns the proposed state and every decision."""
    core = h._mag()
    base = Path(h._run_base(owner, rid))
    state_path = Path(h._state_path(owner, rid))
    problem = _state_problem(core, str(state_path))
    if problem is None:
        raise IntegrityError("The run state is valid; there is nothing to repair")
    order = [c["id"] for c in core["doc"].load_document(h._mag_path(owner, rid)).document["cartridges"]]
    try:
        ledger = lg.load(base)
    except ValueError as exc:
        raise IntegrityError(f"The attempt ledger is unreadable too ({exc}); inspect the run manually") from None
    protocol = service.load_protocol(owner, rid)
    layout = ho.Layout(base, service.config["cwd"], rid, protocol)
    heal_run(base)
    damaged_at = None
    if state_path.exists():
        st = state_path.stat()
        damaged_at = max(st.st_mtime, st.st_ctime)  # ctime cannot be set back by a worker
    # Attempts of one run execute one at a time. Implicated: the most recent
    # attempt (never accepted on the strength of records written while the state
    # could not be trusted) and the attempt that was running when the file last
    # changed (the newest one started at or before that moment).
    implicated, starts = [], []
    if ledger:
        for cid, record in (ledger.get("cartridges") or {}).items():
            for attempt in record.get("attempts") or []:
                begun_at = _iso_ts(attempt.get("started"))
                if begun_at is not None:
                    starts.append((begun_at, cid, attempt))
    starts.sort(key=lambda item: item[0])
    chosen = []
    if starts:
        chosen.append(starts[-1])
        if damaged_at is not None:
            running = [item for item in starts if item[0] <= damaged_at + 1]
            if running and running[-1] is not starts[-1]:
                chosen.append(running[-1])
    for _, cid, attempt in chosen:
        implicated.append({"cartridge_id": cid, "attempt_id": attempt.get("attempt_id"),
                           "attempt_number": attempt.get("attempt_number")})
    rejected = {i["attempt_id"] for i in implicated}
    done, decisions = [], []
    for cid in order:
        record = ((ledger or {}).get("cartridges") or {}).get(cid) or {}
        last = (record.get("attempts") or [{}])[-1]
        if last.get("attempt_id") in rejected:
            decisions.append({"cartridge_id": cid, "accepted": False,
                              "reason": "its attempt was running when the controller state was damaged"})
            break
        ok, why = _accepted(service, owner, rid, layout, ledger or {}, protocol, cid) if ledger else (False, "no ledger")
        decisions.append({"cartridge_id": cid, "accepted": ok, "reason": why})
        if not ok:
            break
        done.append(cid)
    rest = order[len(done):]
    begun = bool(rest) and bool((((ledger or {}).get("cartridges") or {}).get(rest[0]) or {}).get("attempts"))
    if rest:
        current, todo = rest[0], rest[1:]
    else:
        current, todo = None, []
    revision = 2 * len(done) + (1 if begun else 0)
    try:  # never lower than the damaged file's own counter (snapshot names use it)
        damaged_revision = json.loads(state_path.read_text(encoding="utf-8")).get("revision")
        if type(damaged_revision) is int and revision < damaged_revision < 10 ** 9:
            revision = damaged_revision
    except (OSError, ValueError, AttributeError):
        pass
    state = {"done": done, "current": current, "todo": todo,
             "status": "COMPLETED" if not rest else "HALTED", "revision": revision}
    core["state"].validate_run_state(state)
    return {"run_id": rid, "problem": problem, "proposed_state": state, "decisions": decisions,
            "implicated_attempts": implicated, "damaged_at": damaged_at,
            "foreign_files": foreign_run_files(base),
            "message": ("Magazine will keep the damaged file as evidence, rebuild its own state from verified "
                        "controller records and halt the run. Nothing is started; worker claims are not accepted.")}


def repair_state(service, owner, rid):
    """Explicit operator repair. Never starts work. Returns the receipt."""
    core = h._mag()
    if service.has_task(owner, rid):
        raise IntegrityError("A local task for this run is still active; stop it before repairing")
    import interrupted
    workers = interrupted.worker_evidence()
    if workers["status"] != "absent":
        raise IntegrityError("A Magazine worker process is still present (or presence is uncertain); "
                             "repair would race it: " + workers["status"])
    plan = plan_repair(service, owner, rid)
    base = Path(h._run_base(owner, rid))
    state_path = h._state_path(owner, rid)
    lock_path = h._lock_path(owner, rid)
    identity = core["ri"].get_runtime_identity()
    core["rl"].acquire_run_lock(lock_path, owner, identity.runtime_id, identity.pid, identity.start_ticks)
    try:
        quarantine = base / "quarantine" / f"{_now_stamp()}-state-repair"
        quarantine.mkdir(mode=0o700, parents=True)
        evidence = []
        damaged = Path(state_path)
        moved = [("run/state.json", damaged, True)] if damaged.exists() else []
        moved += [("run/" + n, base / "run" / n, False) for n in plan["foreign_files"]]
        for rel, source, copy_only in moved:
            data = _read(source)
            original_mtime = os.stat(source, follow_symlinks=False).st_mtime
            target = quarantine / rel.replace("/", "__")
            if copy_only:
                shutil.copyfile(source, target, follow_symlinks=False)
            else:
                os.replace(source, target)
            evidence.append({"path": rel, "bytes": len(data or b""), "sha256": sha256(data or b""),
                             "mtime": original_mtime,
                             "kept_as": target.relative_to(base).as_posix()})
        ledger = lg.load(base)
        if ledger:
            for item in plan["implicated_attempts"]:
                record = ledger["cartridges"].get(item["cartridge_id"]) or {}
                for attempt in record.get("attempts") or []:
                    if attempt.get("attempt_id") != item["attempt_id"]:
                        continue
                    attempt["integrity"] = {"verdict": "rejected", "code": INTEGRITY_CODE, "at": lg.now_iso(),
                                            "detail": "The run's controller state was modified while this attempt "
                                                      "ran. Its completion claim and commit are not accepted.",
                                            "previous_status": attempt.get("status")}
                    if attempt.get("status") == "done":
                        attempt.update(status="rejected", code=INTEGRITY_CODE)
                    attempt["retry_decision"] = {**(attempt.get("retry_decision") or {}), "decision": "stop",
                                                 "reason_code": INTEGRITY_CODE,
                                                 "reason": "Controller state was modified during this attempt; "
                                                           "its result is not accepted. No automatic retry."}
                    record["status"] = "failed"
            lg.note(ledger, "Controller state repaired by the operator; damaged file kept in "
                    + quarantine.relative_to(base).as_posix() + ". Nothing was started.", None, "warn")
            lg.save(base, ledger)
        core["ss"].save_run_state(state_path, plan["proposed_state"])
        exec_path = Path(state_path).with_name("execution.json")
        claim = core["em"].load_execution_metadata(str(exec_path))
        if claim is not None and claim.halted_by is None:
            core["em"].save_execution_metadata(str(exec_path), replace(
                claim, halted_by=identity.runtime_id, halted_at=core["em"].utc_now_iso(),
                halt_reason="state-repaired"))
        receipt = {"run_id": rid, "repaired_at": lg.now_iso(), "problem": plan["problem"],
                   "state": plan["proposed_state"], "decisions": plan["decisions"],
                   "implicated_attempts": plan["implicated_attempts"], "evidence": evidence,
                   "quarantine": quarantine.relative_to(base).as_posix()}
        atomic_write(str(quarantine / "manifest.json"), (json.dumps(receipt, indent=2) + "\n").encode())
        atomic_write(str(base / "repair.json"), (json.dumps(receipt, indent=2) + "\n").encode())
    finally:
        core["rl"].release_run_lock(lock_path, owner, identity.runtime_id, identity.pid, identity.start_ticks)
    try:
        service.scheduler.forget(rid)
    except Exception:
        pass
    return receipt
