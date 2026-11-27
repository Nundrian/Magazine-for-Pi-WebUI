"""Restore-point management for Magazine's recoverable trash.

A restore point is one ``<data>/magazine/trash/<32 hex>/`` transaction folder,
created when an operator deletes a saved run or a workflow template from
Manage. Restoring moves the retained folders back unchanged. Deleting a
restore point permanently removes that folder and reclaims its storage.

Safety rules (all re-checked immediately before every deletion):

* A restore point is anchored beneath the state trash. A managed workspace can
  have a retained sibling on its original filesystem, named by the same token.
* Deletion never follows symbolic links and never crosses into another
  filesystem; links are removed as links, so their targets are untouched.
* A deletion that was interrupted (``intent.json`` present) is protected: its
  data may still be split between the original place and the trash. Restore
  it first. A folder whose record cannot be read is protected too.
* A permanent deletion is announced durably (``deleting.json``) before the
  first file is removed; an interrupted permanent deletion is listed as such
  and can be completed.
* Files with other hard links (for example an exported copy elsewhere) are
  unlinked from the restore point only; the other copy keeps the data, and
  the bytes are not counted as reclaimed.

Nothing is deleted automatically.
"""
from __future__ import annotations

import json
import os
import re
import stat
import time
from pathlib import Path

from ui_support import directory, sync_directory, workspace_trash_name

TOKEN = re.compile(r"[a-f0-9]{32}\Z")
RECORD_LIMIT = 64 * 1024


class RestorePointError(ValueError):
    def __init__(self, message, code=409):
        super().__init__(message)
        self.code = code


def _read_json(dir_fd, name):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"{name} is not a regular file")
        raw = stream.read(RECORD_LIMIT + 1)
    if len(raw) > RECORD_LIMIT:
        raise ValueError(f"{name} is oversized")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain an object")
    return value


def _usage(dir_fd, device, budget):
    """(apparent bytes, reclaimable bytes, files, shared files) without following links."""
    total = reclaim = files = shared = 0
    for name in os.listdir(dir_fd):
        budget[0] -= 1
        if budget[0] < 0:
            raise RestorePointError("Restore point is too large to inspect safely", 413)
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            if info.st_dev != device:
                continue  # never account for (or enter) another filesystem
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
            try:
                a, b, c, d = _usage(child, device, budget)
            finally:
                os.close(child)
            total, reclaim, files, shared = total + a, reclaim + b, files + c, shared + d
        else:
            files += 1
            total += info.st_size
            if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                shared += 1
            else:
                reclaim += info.st_size
    return total, reclaim, files, shared


def _remove_tree(dir_fd, device, keep=()):
    """Remove the contents of dir_fd; links are unlinked, never followed."""
    for name in os.listdir(dir_fd):
        if name in keep:
            continue
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            if info.st_dev != device:
                raise RestorePointError("A restore point contains another mounted filesystem; it was not deleted")
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
            try:
                _remove_tree(child, device)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=dir_fd)
        else:
            os.unlink(name, dir_fd=dir_fd)


class RestorePoints:
    def __init__(self, service):
        self.service = service

    @property
    def folder(self):
        return self.service.root / "trash"

    # ------------------------------------------------------------ inspection
    def _describe(self, trash_fd, token):
        item = {"trash_id": token, "kind": None, "id": None, "name": None, "run": None,
                "created_at": None, "state": "unreadable", "bytes": 0, "reclaimable_bytes": 0,
                "files": 0, "shared_files": 0, "can_restore": False, "can_delete": False,
                "protected_reason": None, "reason": None}
        fd = os.open(token, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=trash_fd)
        try:
            info = os.fstat(fd)
            item["created_at"] = info.st_mtime
            names = set(os.listdir(fd))
            try:
                deleting = _read_json(fd, "deleting.json")
                intent = _read_json(fd, "intent.json")
                record = _read_json(fd, "trash.json") or intent or (deleting or {}).get("record")
            except (OSError, ValueError, UnicodeDecodeError):
                item["protected_reason"] = "Its record cannot be read; Magazine will not delete unrecognised data."
                item["reason"] = "Unreadable restore point"
                return item
            try:
                item["bytes"], item["reclaimable_bytes"], item["files"], item["shared_files"] = \
                    _usage(fd, info.st_dev, [200000])
            except RestorePointError as exc:
                item["protected_reason"] = str(exc)
                return item
            if record:
                item.update(kind=record.get("kind"), id=record.get("id"),
                            created_at=record.get("created_at") or item["created_at"])
                if record.get("workspace_location") == "local":
                    try:
                        extra = self._workspace(token, record, allow_missing=deleting is not None or "restoring.json" in names or
                                                record.get("state") == "interrupted")
                    except (OSError, ValueError) as exc:
                        item["protected_reason"] = f"Retained workspace is unavailable: {exc}"
                        return item
                    for key, amount in zip(("bytes", "reclaimable_bytes", "files", "shared_files"), extra):
                        item[key] += amount
            if deleting is not None:
                item.update(state="deleting", can_delete=True,
                            reason="A permanent deletion was interrupted; delete again to finish it")
            elif "restoring.json" in names and record:
                item.update(state="restoring", can_restore=True,
                            reason="Restore in progress or interrupted",
                            protected_reason="Finish restoring this run before permanent deletion.")
            elif intent is not None and "trash.json" not in names:
                item.update(state="interrupted", reason="An interrupted move to the trash",
                            protected_reason="A deletion was interrupted; part of this work may still be in its "
                                             "original place. Restore it first.", can_restore=True)
            elif not record:
                if names:
                    item["protected_reason"] = "This folder has no restore record; it was left untouched."
                    item["reason"] = "Unrecognised folder"
                else:
                    item.update(state="empty", can_delete=True,
                                reason="Empty folder left behind by an earlier restore")
                return item
            else:
                item.update(state="complete", can_delete=True, can_restore=True,
                            reason={"run": "Saved run deleted from Manage",
                                    "template": "Workflow template deleted from Manage"}.get(record.get("kind"),
                                                                                           "Deleted Magazine work"))
            if record and record.get("kind") == "run":
                item["run"] = self._run_summary(fd)
                item["name"] = (item["run"] or {}).get("name")
            elif record and record.get("kind") == "template":
                item["name"] = self._template_name(fd)
            if record and record.get("kind") == "run" and isinstance(record.get("id"), str):
                if self.service.scheduler.entry(record["id"]) is not None:
                    item.update(can_delete=False, protected_reason="This run ID is scheduled for execution.")
            return item
        finally:
            os.close(fd)

    def _workspace(self, token, record, *, allow_missing=False, remove=False):
        if record.get("kind") != "run" or not record.get("workspace_inode"):
            raise RestorePointError("Invalid retained workspace identity")
        root = Path(self.service.config["cwd"]) / "magazine-runs"
        name = workspace_trash_name(token)
        with directory(root) as parent:
            try:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                if allow_missing:
                    return (0, 0, 0, 0)
                raise
            try:
                info = os.fstat(fd)
                if info.st_ino != record["workspace_inode"] or info.st_dev != os.fstat(parent).st_dev:
                    raise RestorePointError("Retained workspace does not match the saved identity")
                if not remove:
                    return _usage(fd, info.st_dev, [200000])
                _remove_tree(fd, info.st_dev)
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                    raise RestorePointError("Retained workspace changed during deletion")
                os.rmdir(name, dir_fd=parent)
                sync_directory(parent)
            finally:
                os.close(fd)

    @staticmethod
    def _run_summary(fd):
        try:
            record = os.open("record", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            return None
        try:
            meta = _read_json(record, "meta.json") or {}
            run_dir = os.open("run", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=record)
            try:
                state = _read_json(run_dir, "state.json") or {}
            finally:
                os.close(run_dir)
        except (OSError, ValueError, UnicodeDecodeError):
            return {"name": None, "status": "unreadable"}
        finally:
            os.close(record)
        done, todo = state.get("done") or [], state.get("todo") or []
        current = state.get("current")
        return {"name": meta.get("name"), "status": state.get("status"), "created_at": meta.get("created_at"),
                "completed": len(done) if isinstance(done, list) else None,
                "total": (len(done) + len(todo) + (1 if current else 0)) if isinstance(done, list) and isinstance(todo, list) else None,
                "cartridge": current or (todo[0] if isinstance(todo, list) and todo else None),
                "last_completed": done[-1] if isinstance(done, list) and done else None}

    @staticmethod
    def _template_name(fd):
        try:
            record = os.open("record", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            return None
        try:
            handle = os.open("magazine.yaml", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=record)
            with os.fdopen(handle, "rb") as stream:
                head = stream.read(4096).decode("utf-8", "replace")
            match = re.search(r"^name:\s*['\"]?(.+?)['\"]?\s*$", head, re.M)
            return match.group(1)[:200] if match else None
        except OSError:
            return None
        finally:
            os.close(record)

    def list(self):
        if not self.folder.exists():
            return {"items": [], "total_bytes": 0, "reclaimable_bytes": 0}
        trash_fd = os.open(self.folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            items = []
            for name in sorted(os.listdir(trash_fd)):
                if TOKEN.fullmatch(name):
                    try:
                        items.append(self._describe(trash_fd, name))
                    except (FileNotFoundError, NotADirectoryError):
                        continue
        finally:
            os.close(trash_fd)
        items.sort(key=lambda i: i.get("created_at") or 0, reverse=True)
        return {"items": items, "total_bytes": sum(i["bytes"] for i in items),
                "reclaimable_bytes": sum(i["reclaimable_bytes"] for i in items if i["can_delete"]),
                "automatic_deletion": False}

    # --------------------------------------------------------------- deletion
    def delete(self, data):
        if data.get("confirm") is not True:
            raise RestorePointError("Confirm the permanent deletion first", 400)
        listing = {i["trash_id"]: i for i in self.list()["items"]}
        if data.get("all_eligible") is True:
            ids = [t for t, i in listing.items() if i["can_delete"]]
            if data.get("expected") != len(ids):
                raise RestorePointError("The list of restore points changed; review it again before deleting")
        else:
            ids = data.get("trash_ids")
            if not isinstance(ids, list) or not ids or len(ids) > 1000 or \
                    not all(isinstance(t, str) and TOKEN.fullmatch(t) for t in ids) or len(set(ids)) != len(ids):
                raise RestorePointError("Select one or more restore points", 400)
        blocked = [{"trash_id": t, "reason": (listing.get(t) or {}).get("protected_reason") or "Restore point not found"}
                   for t in ids if not (listing.get(t) or {}).get("can_delete")]
        if blocked:
            raise RestorePointError("Some selected restore points cannot be deleted: "
                                    + "; ".join(f"{b['trash_id'][:8]}: {b['reason']}" for b in blocked))
        deleted, reclaimed = [], 0
        trash_fd = os.open(self.folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            device = os.fstat(trash_fd).st_dev
            for token in ids:
                item = self._describe(trash_fd, token)  # re-check immediately before removal
                if not item["can_delete"]:
                    raise RestorePointError(f"{token[:8]} became protected: {item['protected_reason']}")
                fd = os.open(token, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=trash_fd)
                try:
                    if os.fstat(fd).st_dev != device:
                        raise RestorePointError("A restore point is on another filesystem; it was not deleted")
                    previous = _read_json(fd, "deleting.json")
                    record = (previous or {}).get("record") or _read_json(fd, "trash.json") or {}
                    marker = {"deleting_at": time.time(), "record": record, "bytes": item["bytes"]}
                    if "deleting.json" not in os.listdir(fd):
                        # Written to a temporary name and renamed: the marker is
                        # either absent or complete, never half written.
                        temp = ".deleting.json.tmp"
                        try:
                            os.unlink(temp, dir_fd=fd)
                        except FileNotFoundError:
                            pass
                        mfd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                        with os.fdopen(mfd, "w") as stream:
                            json.dump(marker, stream)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.rename(temp, "deleting.json", src_dir_fd=fd, dst_dir_fd=fd)
                        os.fsync(fd)
                    if record.get("workspace_location") == "local":
                        self._workspace(token, record, allow_missing=previous is not None, remove=True)
                    _remove_tree(fd, device, keep=("deleting.json",))
                    os.unlink("deleting.json", dir_fd=fd)
                finally:
                    os.close(fd)
                os.rmdir(token, dir_fd=trash_fd)
                os.fsync(trash_fd)
                deleted.append(token)
                reclaimed += item["reclaimable_bytes"]
        finally:
            os.close(trash_fd)
        return {"deleted": deleted, "reclaimed_bytes": reclaimed,
                "message": f"Permanently deleted {len(deleted)} restore point{'s' if len(deleted) != 1 else ''}."}
