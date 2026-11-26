"""Restore-point management (Magazine trash): listing, protection, deletion, restore."""
import json
import os
import uuid
from pathlib import Path

import pytest

import restore_points as rp
import service as svc


@pytest.fixture
def service(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return svc.Service(tmp_path / "data" / "magazine", {"cwd": str(workspace), "agentDir": str(tmp_path / "agent")})


def trash_run(service, rid=None, *, state="complete", size=1000, name="Nebula"):
    token = uuid.uuid4().hex
    rid = rid or uuid.uuid4().hex[:12]
    base = service.root / "trash" / token
    (base / "record" / "run").mkdir(parents=True)
    (base / "record" / "meta.json").write_text(json.dumps({"name": name, "created_at": "2026-11-24T04:50:13"}))
    (base / "record" / "run" / "state.json").write_text(json.dumps(
        {"done": ["c001"], "current": "c002", "todo": ["c003"], "status": "HALTED", "revision": 3}))
    (base / "record" / "blob.bin").write_bytes(b"x" * size)
    record = {"kind": "run", "id": rid, "owner": "local", "created_at": 1795493000.0, "parts": ["record"], "state": state}
    (base / ("trash.json" if state == "complete" else "intent.json")).write_text(json.dumps(record))
    return token, rid, base


def items(service):
    return {i["trash_id"]: i for i in service.restore_points.list()["items"]}


def test_listing_describes_run_cartridge_time_reason_and_size(service):
    token, rid, _ = trash_run(service, size=5000)
    item = items(service)[token]
    assert item["kind"] == "run" and item["id"] == rid and item["name"] == "Nebula"
    assert item["run"]["completed"] == 1 and item["run"]["total"] == 3 and item["run"]["cartridge"] == "c002"
    assert item["run"]["last_completed"] == "c001"
    assert item["reason"] == "Saved run deleted from Manage" and item["created_at"] == 1795493000.0
    assert item["bytes"] >= 5000 and item["reclaimable_bytes"] == item["bytes"]
    assert item["can_delete"] and item["can_restore"] and item["state"] == "complete"


def test_delete_single_reclaims_storage_and_keeps_others(service):
    keep, _, keep_base = trash_run(service)
    gone, _, gone_base = trash_run(service, size=20000)
    result = service.restore_points.delete({"trash_ids": [gone], "confirm": True})
    assert result["deleted"] == [gone] and result["reclaimed_bytes"] >= 20000
    assert not gone_base.exists() and keep_base.exists()
    assert set(items(service)) == {keep}


def test_confirmation_is_required(service):
    token, _, base = trash_run(service)
    with pytest.raises(rp.RestorePointError):
        service.restore_points.delete({"trash_ids": [token]})
    assert base.exists()


def test_interrupted_deletion_is_protected(service):
    token, _, base = trash_run(service, state="interrupted")
    item = items(service)[token]
    assert not item["can_delete"] and "interrupted" in item["protected_reason"] and item["can_restore"]
    with pytest.raises(rp.RestorePointError, match="cannot be deleted"):
        service.restore_points.delete({"trash_ids": [token], "confirm": True})
    assert base.exists()


def test_links_are_removed_not_followed_and_shared_files_survive(service, tmp_path):
    outside = tmp_path / "project-file.txt"
    outside.write_text("user project data")
    export = tmp_path / "finished-export.txt"
    export.write_text("exported result")
    token, _, base = trash_run(service)
    os.symlink(outside, base / "record" / "link-to-project")
    os.symlink(tmp_path, base / "record" / "link-to-dir")
    os.link(export, base / "record" / "shared-result.txt")
    item = items(service)[token]
    assert item["shared_files"] == 1
    service.restore_points.delete({"trash_ids": [token], "confirm": True})
    assert outside.read_text() == "user project data"
    assert export.read_text() == "exported result"
    assert not base.exists()


def test_delete_all_eligible_requires_the_reviewed_count(service):
    trash_run(service)
    trash_run(service)
    protected, _, protected_base = trash_run(service, state="interrupted")
    with pytest.raises(rp.RestorePointError, match="changed"):
        service.restore_points.delete({"all_eligible": True, "expected": 3, "confirm": True})
    result = service.restore_points.delete({"all_eligible": True, "expected": 2, "confirm": True})
    assert len(result["deleted"]) == 2 and protected_base.exists()
    assert set(items(service)) == {protected}


def test_unrecognised_and_foreign_folders_are_never_deleted(service):
    trash = service.root / "trash"
    trash.mkdir(exist_ok=True)
    stranger = trash / ("a" * 32)
    stranger.mkdir()
    (stranger / "data.bin").write_bytes(b"?")
    other = trash / "not-a-restore-point"
    other.mkdir()
    listing = items(service)
    assert "not-a-restore-point" not in listing
    assert not listing["a" * 32]["can_delete"]
    with pytest.raises(rp.RestorePointError):
        service.restore_points.delete({"trash_ids": ["a" * 32], "confirm": True})
    assert stranger.exists() and other.exists()


def test_empty_leftover_and_interrupted_permanent_deletion_can_be_finished(service):
    trash = service.root / "trash"
    trash.mkdir(exist_ok=True)
    empty = uuid.uuid4().hex
    (trash / empty).mkdir()
    token, _, base = trash_run(service)
    (base / "deleting.json").write_text(json.dumps({"record": {"kind": "run"}}))
    listing = items(service)
    assert listing[empty]["state"] == "empty" and listing[empty]["can_delete"]
    assert listing[token]["state"] == "deleting" and listing[token]["can_delete"]
    with pytest.raises(Exception):
        service.ui.restore({"trash_id": token})  # never restore a half-deleted point
    service.restore_points.delete({"trash_ids": [empty, token], "confirm": True})
    assert not (trash / empty).exists() and not base.exists()


def test_scheduled_run_id_is_protected(service, monkeypatch):
    token, rid, _ = trash_run(service)
    monkeypatch.setattr(service.scheduler, "entry", lambda r: {"run_id": r} if r == rid else None)
    item = items(service)[token]
    assert not item["can_delete"] and "scheduled" in item["protected_reason"]


def test_restore_removes_the_emptied_transaction_folder(service):
    token, rid, base = trash_run(service)
    (service.root / "runs" / "local").mkdir(parents=True, exist_ok=True)
    result = service.ui.restore({"trash_id": token})
    assert result["restored"] and (service.root / "runs" / "local" / rid / "meta.json").exists()
    assert not base.exists()
    assert token not in items(service)
