"""Committed result_file_v1 handovers for Pi workers.

Adapted from magazine_simple_reliability3.py (Nundrian/Magazine 09a8706):
strict_json, durable_json, read_result_file, validate_outgoing,
validate_envelope, committed_result, commit_attempt_result, prepare_files,
collect_outputs, snapshot_candidates. Open WebUI chat archives become archived
Pi session JSONL files; chat identity becomes Pi session identity.

Nothing here trusts a model's claim: a producer is complete only when its
attempt-scoped result file (written by the trusted publisher tool) parses
strictly, carries the expected identities, matches the publisher receipt,
supplies every declared outgoing selector, and has been copied into
Magazine-controlled storage and read back with matching SHA-256.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path, PurePosixPath

from protocol import ProtocolError, select_pointer

MAX_RESULT_BYTES = 1024 * 1024
MAX_SESSION_BYTES = 32 * 1024 * 1024
MAX_CANDIDATE_BYTES = 64 * 1024 * 1024


class ResultFailure(ProtocolError):
    """A classified attempt failure. ``recovery`` is hard, publish or repair."""

    def __init__(self, code, message, recovery="hard"):
        super().__init__(code + ": " + message)
        self.code, self.recovery = code, recovery


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(raw):
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError("duplicate JSON key: " + key)
            obj[key] = value
        return obj
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))


def ensure_dir(path, mode=0o700):
    path = Path(path)
    probe = path
    while not probe.exists():
        probe = probe.parent
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    if path.is_symlink() or not path.is_dir():
        raise ResultFailure("RESULT_NOT_REGULAR", f"Unsafe directory: {path.name}")
    return path


def open_plain(base, rel):
    """Open base/rel read-only if it is a regular file reached without links."""
    path = Path(base)
    if path.is_symlink():
        raise OSError(f"{path.name} is a link")
    for part in PurePosixPath(rel).parts:
        path = path / part
        if path.is_symlink():
            raise OSError(f"{part} is a link")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(f"{part} is not a regular file")
    return os.fdopen(fd, "rb")


def hash_stream(src, dst=None):
    digest, size = hashlib.sha256(), 0
    while True:
        chunk = src.read(1024 * 1024)
        if not chunk:
            return size, digest.hexdigest()
        digest.update(chunk)
        size += len(chunk)
        if dst is not None:
            dst.write(chunk)


def durable_bytes(path, raw):
    path = Path(path)
    ensure_dir(path.parent)
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        with open_plain(path.parent, path.name) as handle:
            back = handle.read(len(raw) + 1)
        if back != raw:
            raise ResultFailure("RESULT_READBACK_MISMATCH", path.name)
    finally:
        if temp.exists():
            temp.unlink()
    return {"bytes": len(raw), "sha256": sha256_bytes(raw)}


def durable_json(path, value):
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    info = durable_bytes(path, raw)
    with open_plain(Path(path).parent, Path(path).name) as handle:
        if strict_json(handle.read()) != value:
            raise ResultFailure("RESULT_READBACK_MISMATCH", Path(path).name)
    return info


def read_result_file(base, name="publish/result.json", limit=MAX_RESULT_BYTES):
    try:
        with open_plain(base, name) as handle:
            before = os.fstat(handle.fileno())
            if before.st_size > limit:
                raise ResultFailure("RESULT_TOO_LARGE", f"Result exceeds {limit} bytes.")
            raw = handle.read(limit + 1)
            after = os.fstat(handle.fileno())
            current = os.stat(Path(base) / name, follow_symlinks=False)
    except FileNotFoundError:
        raise ResultFailure("RESULT_MISSING", "Publish this attempt's result with the magazine_publish_result tool.", "publish") from None
    except OSError as exc:
        raise ResultFailure("RESULT_NOT_REGULAR", str(exc)) from None
    fields = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    if fields(before) != fields(after) or fields(after) != fields(current):
        raise ResultFailure("RESULT_CHANGED_DURING_READ", "The source changed while Magazine read it.")
    if len(raw) > limit:
        raise ResultFailure("RESULT_TOO_LARGE", f"Result exceeds {limit} bytes.")
    try:
        document = strict_json(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ResultFailure("RESULT_JSON_INVALID", str(exc), "publish") from None
    return document, {"bytes": len(raw), "sha256": sha256_bytes(raw)}


def outgoing_pointers(protocol, cid):
    return sorted({h["select"] for c in protocol["cartridges"].values() for h in c["handover"] if h["from"] == cid})


def validate_outgoing(protocol, cid, payload):
    pointers = outgoing_pointers(protocol, cid)
    if pointers and not isinstance(payload, dict):
        raise ResultFailure("RESULT_JSON_INVALID", f"{cid} has no valid outgoing JSON object.", "publish")
    for pointer in pointers:
        try:
            select_pointer(payload, pointer)
        except ProtocolError:
            raise ResultFailure("RESULT_POINTER_MISSING", f"{cid} must supply {pointer!r}.", "publish") from None
    return pointers


IDENTITY_KEYS = ("run_id", "cartridge_id", "attempt_id", "session_id")


def validate_envelope(document, expected, protocol, cid):
    if not isinstance(document, dict) or document.get("schema") != "magazine.result.v1":
        raise ResultFailure("RESULT_SCHEMA_INVALID", "Expected a magazine.result.v1 object.", "publish")
    if set(document) != {*IDENTITY_KEYS, "schema", "payload"}:
        raise ResultFailure("RESULT_SCHEMA_INVALID", "Unexpected result envelope fields.", "publish")
    for key in IDENTITY_KEYS:
        if not expected.get(key) or document.get(key) != expected[key]:
            raise ResultFailure("RESULT_IDENTITY_MISMATCH", f"The {key} does not match this attempt.")
    payload = document.get("payload")
    if not isinstance(payload, dict):
        raise ResultFailure("RESULT_SCHEMA_INVALID", "payload must be an object.", "publish")
    if protocol.get("result_profile") == "magazine.task.v1":
        value = payload.get("result")
        if not isinstance(value, dict) or value.get("step") != cid \
                or type(value.get("verified")) is not bool \
                or not isinstance(value.get("files"), list) or not all(isinstance(x, str) for x in value["files"]) \
                or not isinstance(value.get("checks"), list) or not all(isinstance(x, str) for x in value["checks"]) \
                or not isinstance(value.get("note"), str) or (value.get("verified") and not value["checks"]):
            raise ResultFailure("RESULT_SCHEMA_INVALID", "Result does not satisfy magazine.task.v1.", "publish")
    return validate_outgoing(protocol, cid, payload)


def attempt_identity(run_id, cid, attempt):
    return {"run_id": run_id, "cartridge_id": cid, "attempt_id": attempt["attempt_id"],
            "session_id": attempt.get("session_id")}


class Layout:
    """Where one run's Magazine-controlled and model-visible files live."""

    def __init__(self, run_base, workspace, run_id, protocol):
        self.base = Path(run_base)
        self.workspace = Path(workspace)
        self.run_id = run_id
        self.protocol = protocol

    def cdir(self, cid):
        return self.protocol["cartridges"][cid]["dir"]

    def attempt_dir(self, cid, aid):
        return self.base / "attempts" / self.cdir(cid) / aid

    def handover_rel(self, cid, aid):
        return f"handovers/{self.cdir(cid)}/{aid}"

    def work(self, cid):
        return self.workspace / "magazine-runs" / self.run_id / self.cdir(cid)


def setup_attempt(layout, cid, attempt, prompt):
    base = layout.attempt_dir(cid, attempt["attempt_id"])
    if base.exists():
        raise ResultFailure("RESULT_NOT_REGULAR", "Attempt directory already exists.")
    ensure_dir(base / "publish")
    durable_json(base / "attempt.json", attempt_identity(layout.run_id, cid, attempt))
    durable_bytes(base / "prompt.md", prompt.encode("utf-8"))
    return base


def archive_session(layout, cid, attempt, session_file):
    """Pin the saved Pi session bytes for this attempt (original kept in place)."""
    try:
        with open_plain(Path(session_file).parent, Path(session_file).name) as src:
            raw = src.read(MAX_SESSION_BYTES + 1)
    except OSError as exc:
        raise ResultFailure("SESSION_ARCHIVE_FAILED", f"Saved Pi session unavailable: {exc}") from None
    if len(raw) > MAX_SESSION_BYTES:
        raise ResultFailure("SESSION_ARCHIVE_FAILED", "Saved Pi session exceeds the 32 MiB archive limit.")
    lines = raw.decode("utf-8").splitlines()
    try:
        header = strict_json(lines[0]) if lines else None
    except ValueError:
        header = None
    if not header or header.get("type") != "session" or header.get("id") != attempt.get("session_id"):
        raise ResultFailure("SESSION_ARCHIVE_FAILED", "Saved Pi session identity does not match this attempt.")
    rel = layout.handover_rel(cid, attempt["attempt_id"]) + "/session.jsonl"
    info = durable_bytes(layout.base / rel, raw)
    return dict(info, path=rel, session_id=attempt["session_id"], readback_verified=True)


def commit_attempt_result(layout, cid, attempt, document, source_meta, pointers):
    rel = layout.handover_rel(cid, attempt["attempt_id"]) + "/result.json"
    final = layout.base / rel
    if final.exists():
        previous, _ = read_result_file(layout.base, rel)
        if previous != document:
            raise ResultFailure("RESULT_COMMIT_MISMATCH", "An existing commit has different result bytes.")
    info = durable_json(final, document)
    marker = dict(attempt_identity(layout.run_id, cid, attempt), result_sha256=info["sha256"],
                  session_sha256=attempt["session_archive"]["sha256"])
    durable_json(final.with_name("commit.json"), marker)
    return dict(info, path=rel, source=source_meta, identity_verified=True, pointers_verified=pointers)


def committed_payload(layout, ledger, cid):
    """Re-read and verify the committed handover of ``cid`` before it is used."""
    record = ledger.get("cartridges", {}).get(cid) or {}
    attempt = next((a for a in reversed(record.get("attempts", []))
                    if a.get("status") == "done" and a.get("phase") == "committed"), None)
    if attempt is None or not attempt.get("handover"):
        raise ResultFailure("RESULT_NOT_COMMITTED", f"{cid} has no committed handover.")
    meta = attempt["handover"]
    document, observed = read_result_file(layout.base, meta["path"])
    if observed["sha256"] != meta["sha256"] or observed["bytes"] != meta["bytes"]:
        raise ResultFailure("RESULT_COMMIT_MISMATCH", f"Committed result for {cid} changed.")
    expected = attempt_identity(layout.run_id, cid, attempt)
    for key, value in expected.items():
        if document.get(key) != value:
            raise ResultFailure("RESULT_IDENTITY_MISMATCH", f"Committed {cid} has a different {key}.")
    marker, _ = read_result_file(layout.base, str(PurePosixPath(meta["path"]).with_name("commit.json")), MAX_SESSION_BYTES)
    archive = attempt.get("session_archive") or {}
    try:
        with open_plain(layout.base, archive.get("path", "")) as handle:
            size, digest = hash_stream(handle)
    except (OSError, TypeError):
        raise ResultFailure("RESULT_COMMIT_MISMATCH", f"Session archive for {cid} is missing.") from None
    if marker != dict(expected, result_sha256=meta["sha256"], session_sha256=archive.get("sha256")) \
            or digest != archive.get("sha256") or size != archive.get("bytes"):
        raise ResultFailure("RESULT_COMMIT_MISMATCH", f"Commit/archive for {cid} changed.")
    return document["payload"]


def recorded_output(ledger, cid, rel):
    record = ledger.get("cartridges", {}).get(cid) or {}
    for attempt in reversed(record.get("attempts", [])):
        if attempt.get("status") == "done":
            return next((f for f in attempt.get("outputs") or [] if f.get("path") == rel), None)
    return None


def prepare_files(layout, ledger, cid):
    """Create the cartridge work folder and hand over verified declared inputs."""
    decl = layout.protocol["cartridges"][cid]
    here = layout.work(cid)
    for path in (here, here / "inputs", here / "outputs"):
        if path.is_symlink():
            raise ResultFailure("INPUT_INTEGRITY_FAILED", f"{path.name} is a link; Magazine will not use it")
    if not (decl["inputs"] or decl["outputs"]):
        return []
    ensure_dir(here / "outputs")
    delivered = []
    for f in decl["inputs"]:
        what = f"Input file {f['path']} from {f['from']}"
        recorded = recorded_output(ledger, f["from"], f["path"])
        if recorded is None:
            raise ResultFailure("INPUT_INTEGRITY_FAILED", f"{what} has no recorded committed output.")
        dst = here / "inputs" / layout.cdir(f["from"]) / f["path"]
        try:
            src = open_plain(layout.work(f["from"]), "outputs/" + f["path"])
        except OSError:
            raise ResultFailure("INPUT_INTEGRITY_FAILED", f"{what} is missing or is not a regular file") from None
        part = dst.with_name(dst.name + ".part")
        with src:
            ensure_dir(dst.parent)
            with open(part, "wb") as out:
                size, digest = hash_stream(src, out)
        if digest != recorded.get("sha256") or size != recorded.get("bytes"):
            os.unlink(part)
            raise ResultFailure("INPUT_INTEGRITY_FAILED",
                f"{what} changed after {f['from']} finished: recorded {recorded.get('bytes')} B "
                f"{str(recorded.get('sha256'))[:12]}, found {size} B {digest[:12]}. Nothing was handed over.")
        os.replace(part, dst)
        delivered.append({"from": f["from"], "path": f["path"], "bytes": size, "sha256": digest,
                          "local": str(dst), "verified": True})
    return delivered


def collect_outputs(layout, cid):
    here = layout.work(cid)
    files, missing = [], []
    for rel in layout.protocol["cartridges"][cid]["outputs"]:
        try:
            with open_plain(here, "outputs/" + rel) as src:
                size, digest = hash_stream(src)
            files.append({"path": rel, "bytes": size, "sha256": digest})
        except OSError:
            missing.append(rel)
    return files, missing


def snapshot_candidates(layout, cid, attempt):
    """Keep bounded declared output evidence; never promoted as verified input."""
    root = layout.base / layout.handover_rel(cid, attempt["attempt_id"]) / "candidates"
    found, total = [], 0
    for rel in layout.protocol["cartridges"][cid]["outputs"]:
        try:
            with open_plain(layout.work(cid), "outputs/" + rel) as src:
                size = os.fstat(src.fileno()).st_size
                if total + size > MAX_CANDIDATE_BYTES:
                    break
                dest = root / rel
                ensure_dir(dest.parent)
                with open(dest, "xb") as dst:
                    length, digest = hash_stream(src, dst)
                total += length
                found.append({"path": rel, "bytes": length, "sha256": digest, "verified": False})
        except (OSError, ValueError):
            continue
    return found
