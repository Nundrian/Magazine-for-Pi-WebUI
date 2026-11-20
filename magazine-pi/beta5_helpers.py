"""Beta.5 helpers with Pi publication diagnostics; see reused-components.json."""
import os, sys, json, traceback, base64, hashlib, shutil, zipfile, asyncio, uuid
import contextlib, errno, re, stat, tempfile, io, ctypes, time, unicodedata, zlib
from pathlib import Path, PureWindowsPath
_TASKS = set()


_RUN_HANDLES = {}


_LIBRARY_ROOT_DEFAULT = "/app/backend/data/magazine/library"


_LIBRARY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


_LIBRARY_MAX_WORKFLOWS = 128


_LIBRARY_MAX_ROOT_ENTRIES = 1024


_LIBRARY_MAX_FILES = 256


_LIBRARY_MAX_ENTRIES = 512


_LIBRARY_MAX_DEPTH = 8


_LIBRARY_MAX_FILE_BYTES = 1024 * 1024


_LIBRARY_MAX_MANIFEST_BYTES = 64 * 1024


_LIBRARY_MAX_TOTAL_BYTES = 4 * 1024 * 1024


_LIBRARY_MAX_ARCHIVE_BYTES = 8 * 1024 * 1024


_LIBRARY_MAX_PATH_BYTES = 240


_LIBRARY_MAX_EXPANSION_RATIO = 100


_LIBRARY_IMPORT_LOCK_SECONDS = 5


def _run_base(owner_id, rid):
    return os.path.join(_runs_root(), str(owner_id), str(rid))


def _state_dir(owner_id, rid):
    return os.path.join(_run_base(owner_id, rid), "run")


def _state_path(owner_id, rid):
    return os.path.join(_state_dir(owner_id, rid), "state.json")


def _lock_path(owner_id, rid):
    return os.path.join(_state_dir(owner_id, rid), ".lock")


def _mag_path(owner_id, rid):
    return os.path.join(_run_base(owner_id, rid), "magazine.json")


def _meta_path(owner_id, rid):
    return os.path.join(_run_base(owner_id, rid), "meta.json")


def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load_meta(owner_id, rid):
    mp = _meta_path(owner_id, rid)
    try:
        with open(mp) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _extract_model_id(item):
    """Extract the canonical model identifier from a model object / dict / str."""
    if item is None:
        return ""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for k in ("id", "model", "name"):
            v = item.get(k)
            if v not in (None, ""):
                return str(v)
        return ""
    for attr in ("id", "model", "name"):
        v = getattr(item, attr, None)
        if v not in (None, ""):
            return str(v)
    return ""


def _extract_tool_id(item):
    """Extract the canonical tool identifier from a tool object / dict / str."""
    if item is None:
        return ""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for k in ("id", "name"):
            v = item.get(k)
            if v not in (None, ""):
                return str(v)
        return ""
    for attr in ("id", "name"):
        v = getattr(item, attr, None)
        if v not in (None, ""):
            return str(v)
    return ""


def _available_model_ids(models):
    """Set of available model ids (user-scoped)."""
    ids = set()
    for m in models or []:
        mid = _extract_model_id(m)
        if mid:
            ids.add(mid)
    return ids


def _available_tool_ids(tools):
    """Set of available tool ids (user-scoped)."""
    ids = set()
    for t in tools or []:
        tid = _extract_tool_id(t)
        if tid:
            ids.add(tid)
    return ids


def _document_logical_keys(document):
    """Logical model keys (cartridge job_type/model) present in the document."""
    keys = set()
    if not isinstance(document, dict):
        return keys
    for c in document.get("cartridges") or []:
        if not isinstance(c, dict):
            continue
        for field in ("job_type", "model"):
            v = c.get(field)
            if v not in (None, ""):
                keys.add(str(v))
                break
    return keys


def _validate_admission(document, model_map, tool_ids, available_models,
                        available_tools):
    """
    Server-side admission validation for a READY run. Pure function — no OWUI
    imports — so it is testable in isolation.

    Returns a list of human-readable error strings; an empty list means the
    run is admissible.

    Rules:
      1. Every logical model key present in the document must have a model_map
         entry.
      2. Every model id referenced by model_map must be in the user's available
         model set.
      3. Every requested tool id must be in the user's available tool set.
    """
    errors = []
    models = _available_model_ids(available_models)
    tools = _available_tool_ids(available_tools)
    logical = _document_logical_keys(document)
    model_map = dict(model_map) if isinstance(model_map, dict) else {}
    tool_ids = list(tool_ids) if tool_ids is not None else []

    for key in sorted(logical):
        if key not in model_map:
            errors.append("model key %r (from document) has no model_map entry" % (key,))

    for key in sorted(model_map):
        mid_s = str(model_map[key]) if model_map[key] is not None else ""
        if mid_s and mid_s not in models:
            errors.append("model %r (for key %r) is not in the user's available models"
                          % (mid_s, key))

    for tid in sorted(set(str(t) for t in tool_ids)):
        if tid and tid not in tools:
            errors.append("tool %r is not in the user's available tools" % (tid,))

    return errors


def _normalize_cartridges(document):
    """Normalize a validated document into a UI-ready cartridge list."""
    out = []
    for i, c in enumerate(document.get("cartridges", [])):
        out.append({
            "order": i + 1,
            "id": c["id"],
            "model": c.get("job_type", ""),
            "prompt": c.get("prompt", ""),
        })
    return out


def _load_package_from_upload(file_name, file_data_b64, file_type, document_json):
    """Load & validate a package via the core boundary.

    Returns (document, plan, name). Raises on validation failure.
    """
    m = _mag()
    if not m.get("ok"):
        raise ValueError(m.get("error", "magazine core not available"))
    doc_mod = m["doc"]
    # Separate requests must never overwrite one another's validation input.
    with tempfile.TemporaryDirectory(prefix="magazine-upload-") as validate_dir:
        if file_type == "json":
            if not document_json:
                raise ValueError("document_json is required for json type")
            tmp = os.path.join(validate_dir, "magazine.json")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(document_json)
        else:  # magazine
            if not file_data_b64:
                raise ValueError("file_data_b64 is required for magazine type")
            try:
                raw = base64.b64decode(file_data_b64)
            except Exception:
                raise ValueError("invalid base64 in file_data_b64")
            tmp = os.path.join(validate_dir, "upload.magazine")
            with open(tmp, "wb") as f:
                f.write(raw)
        loaded = doc_mod.load_document(tmp)
    document = loaded.document
    plan = loaded.plan
    name = (plan.name if plan is not None else "") or "pasted-document"
    return document, plan, name


class _LibraryError(ValueError):
    def __init__(self, message, status_code=422):
        super().__init__(message)
        self.status_code = status_code


def _initialize_library_root(root):
    """Create a configured library without following any existing link.

    Event installation calls this once so a clean Open WebUI data volume is
    immediately usable. A later missing or replaced directory is still an
    error; request handlers never recreate it implicitly.
    """
    if (not root or not os.path.isabs(root) or
            os.mkdir not in os.supports_dir_fd or
            os.open not in os.supports_dir_fd or
            not hasattr(os, "O_NOFOLLOW")):
        raise _LibraryError("Library unavailable: configure an absolute container directory with safe directory-create support.", 503)
    library_path = Path(os.path.sep).joinpath(*Path(root).parts[1:])
    runs_path = Path(_runs_root()).absolute()
    if library_path.is_relative_to(runs_path) or runs_path.is_relative_to(library_path):
        raise _LibraryError("Library unavailable: its directory must be separate from Magazine run storage.", 503)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(os.path.sep, flags)
    try:
        for part in Path(root).parts[1:]:
            if part in (".", ".."):
                raise _LibraryError("Library unavailable: invalid root configuration.", 503)
            try:
                next_fd = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    # A concurrent installer won the race; the no-follow open
                    # below still verifies what was created.
                    pass
                next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
    except (OSError, _LibraryError):
        raise _LibraryError("Library unavailable: its directory could not be created safely or contains a link.", 503) from None
    finally:
        os.close(fd)


@contextlib.contextmanager
def _library_root_fd(root):
    """Anchor every read to open directories; reject links in root ancestors."""
    fd = None
    try:
        if (not root or not os.path.isabs(root) or
                os.open not in os.supports_dir_fd or
                not hasattr(os, "O_NOFOLLOW")):
            raise _LibraryError("Library unavailable: configure an absolute container directory with safe directory-read support.", 503)
        library_path = Path(os.path.sep).joinpath(*Path(root).parts[1:])
        runs_path = Path(_runs_root()).absolute()
        if library_path.is_relative_to(runs_path) or runs_path.is_relative_to(library_path):
            raise _LibraryError("Library unavailable: its directory must be separate from Magazine run storage.", 503)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(os.path.sep, flags)
        for part in Path(root).parts[1:]:
            if part in (".", ".."):
                raise _LibraryError("Library unavailable: invalid root configuration.", 503)
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    except OSError:
        raise _LibraryError("Library unavailable: the configured directory is missing, inaccessible, or linked. Uploads remain available.", 503) from None
    finally:
        if fd is not None:
            os.close(fd)


def _library_copy_tree(source_fd, destination, budget, depth=0):
    """Copy bounded regular files into our disposable snapshot, never follow links.

    fds also defeat replacement between directory discovery and file reads.
    A source changing during a read is refused, rather than partially accepted.
    """
    with os.scandir(source_fd) as entries:
        for entry in entries:
            budget["entries"] += 1
            if budget["entries"] > _LIBRARY_MAX_ENTRIES:
                raise _LibraryError("Workflow exceeds the directory-entry limit.")
            before = os.stat(entry.name, dir_fd=source_fd, follow_symlinks=False)
            target = destination / entry.name
            if stat.S_ISLNK(before.st_mode):
                raise _LibraryError("Workflow contains a symbolic link; links are not supported.")
            if stat.S_ISDIR(before.st_mode):
                if depth >= _LIBRARY_MAX_DEPTH:
                    raise _LibraryError("Workflow exceeds the directory-depth limit.")
                child_fd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source_fd)
                try:
                    target.mkdir()
                    _library_copy_tree(child_fd, target, budget, depth + 1)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(before.st_mode):
                budget["files"] += 1
                limit = _LIBRARY_MAX_FILE_BYTES
                if depth == 0 and entry.name == "magazine.yaml":
                    limit = min(limit, _LIBRARY_MAX_MANIFEST_BYTES)
                if budget["files"] > _LIBRARY_MAX_FILES or before.st_size > limit:
                    raise _LibraryError("Workflow exceeds the file-count or file-size limit.")
                fd = os.open(entry.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=source_fd)
                with os.fdopen(fd, "rb") as file:
                    opened = os.fstat(file.fileno())
                    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                        raise _LibraryError("Workflow changed while being read; refresh and retry.")
                    data = file.read(min(limit, _LIBRARY_MAX_TOTAL_BYTES - budget["bytes"]) + 1)
                    after = os.fstat(file.fileno())
                budget["bytes"] += len(data)
                if len(data) > limit or budget["bytes"] > _LIBRARY_MAX_TOTAL_BYTES:
                    raise _LibraryError("Workflow exceeds the file-size or total-byte limit.")
                if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != after.st_size:
                    raise _LibraryError("Workflow changed while being read; refresh and retry.")
                target.write_bytes(data)
            else:
                raise _LibraryError("Workflow contains a non-regular file; only files and directories are supported.")


def _library_validation_error(exc):
    """Return fixed diagnostics, never raw paths, YAML excerpts, or tracebacks."""
    if isinstance(exc, _LibraryError):
        return str(exc)
    if isinstance(exc, UnicodeError):
        return "Invalid UTF-8 in the workflow manifest or prompt files."
    if isinstance(exc, FileNotFoundError):
        return "Missing magazine.yaml or a referenced prompt file."
    if isinstance(exc, OSError):
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            return "Workflow contains a link or an invalid directory."
        return "Workflow files are inaccessible or changed while being read."
    # The canonical exceptions include source paths or offending YAML values.
    # Classify them; do not send their text to a browser.
    detail = str(exc).lower()
    if "invalid yaml" in detail:
        return "Invalid YAML in magazine.yaml."
    if "duplicate" in detail:
        return "Duplicate cartridge identifiers or YAML keys."
    if any(term in detail for term in ("path escapes", "absolute", "relative_path")):
        return "Invalid prompt path; every prompt must remain inside its workflow."
    return "Workflow failed canonical package schema validation."


def _library_preview_at(root_fd, workflow_id):
    summary = {"workflow_id": workflow_id, "name": workflow_id,
               "valid": False, "cartridge_count": 0, "order": []}
    source_fd = None
    try:
        source_fd = os.open(workflow_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        with tempfile.TemporaryDirectory(prefix="magazine-library-") as scratch:
            _library_copy_tree(source_fd, Path(scratch), {"entries": 0, "files": 0, "bytes": 0})
            core = _mag()
            if not core.get("ok"):
                raise _LibraryError("Workflow validation is temporarily unavailable.")
            loaded = core["doc"].load_document(scratch)
        document = loaded.document
        cartridges = _normalize_cartridges(document)
        summary.update(valid=True, name=loaded.plan.name, schema_version=document["schema_version"],
                       cartridge_count=len(cartridges), order=[c["id"] for c in cartridges],
                       cartridges=cartridges, document=document)
    except Exception as exc:
        summary["error"] = _library_validation_error(exc)
    finally:
        if source_fd is not None:
            os.close(source_fd)
    return summary


def _library_preview(root, workflow_id):
    if not isinstance(workflow_id, str) or not _LIBRARY_ID.fullmatch(workflow_id):
        raise _LibraryError("Invalid workflow identifier; select a listed workflow.", 400)
    with _library_root_fd(root) as root_fd:
        return _library_preview_at(root_fd, workflow_id)


def _library_list(root):
    result = {"available": False, "workflows": [], "truncated": False, "message": ""}
    try:
        with _library_root_fd(root) as root_fd:
            names = []
            with os.scandir(root_fd) as entries:
                for count, entry in enumerate(entries, 1):
                    if count > _LIBRARY_MAX_ROOT_ENTRIES:
                        result["truncated"] = True
                        break
                    if _LIBRARY_ID.fullmatch(entry.name) and (entry.is_dir(follow_symlinks=False) or entry.is_symlink()):
                        names.append(entry.name)
            names.sort()
            if len(names) > _LIBRARY_MAX_WORKFLOWS:
                result["truncated"] = True
            for name in names[:_LIBRARY_MAX_WORKFLOWS]:
                preview = _library_preview_at(root_fd, name)
                result["workflows"].append({key: value for key, value in preview.items()
                                            if key not in ("document", "cartridges")})
        result["available"] = True
        result["message"] = ("Library scan limit reached; only a bounded selection is shown."
                             if result["truncated"] else
                             ("No Magazine projects yet. An administrator can import a Project ZIP."
                              if not result["workflows"] else "Select a project to validate and preview its prompts."))
    except _LibraryError as exc:
        result["message"] = str(exc)
    return result


def _library_zip_members(archive):
    """Validate the whole ZIP directory before extraction; no new manifest parser."""
    infos = archive.infolist()
    if not infos or len(infos) > _LIBRARY_MAX_ENTRIES:
        raise _LibraryError("ZIP is empty or exceeds the archive-entry limit.", 413)
    core = _mag()
    if not core.get("ok"):
        raise _LibraryError("Project validation is temporarily unavailable.", 503)
    members, explicit, paths = [], set(), {}
    files, total = 0, 0
    for info in infos:
        # Reuse the frozen package's archive checks, then add import limits
        # and collision checks before anything is materialized.
        try:
            core["doc"].package._validate_member_info(info)
        except Exception:
            raise _LibraryError("ZIP contains an unsafe archive path or entry.", 400) from None
        name = info.filename
        file_type = stat.S_IFMT(info.external_attr >> 16)
        directory = info.is_dir()
        if (file_type not in (0, stat.S_IFREG, stat.S_IFDIR) or
                (file_type == stat.S_IFDIR and not directory) or
                (file_type == stat.S_IFREG and directory) or
                (info.create_system == 0 and info.external_attr & 0x400) or
                info.flag_bits & 1 or
                info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
            raise _LibraryError("ZIP contains an unsupported or unsafe archive entry.", 400)
        if (name != info.orig_filename or "\\" in name or
                PureWindowsPath(name).drive or name.startswith("/") or
                len(name.encode("utf-8")) > _LIBRARY_MAX_PATH_BYTES):
            raise _LibraryError("ZIP contains an invalid path or exceeds the path-length limit.", 400)
        parts = name.rstrip("/").split("/")
        if len(parts) > _LIBRARY_MAX_DEPTH + 1:
            raise _LibraryError("ZIP exceeds the directory-depth limit.", 413)
        for part in parts:
            reserved = part.split(".", 1)[0].upper()
            if (not part or part in (".", "..") or part.rstrip(" .") != part or
                    any(ord(c) < 32 or ord(c) == 127 or c in '<>:"|?*' for c in part) or
                    reserved in {"CON", "PRN", "AUX", "NUL"} or
                    re.fullmatch(r"(?:COM|LPT)[1-9]", reserved)):
                raise _LibraryError("ZIP contains an unsafe or ambiguous path.", 400)
        key = unicodedata.normalize("NFC", "/".join(parts)).casefold()
        if key in explicit:
            raise _LibraryError("ZIP contains duplicate or case-colliding paths.", 400)
        explicit.add(key)
        for index in range(1, len(parts) + 1):
            path = "/".join(parts[:index])
            folded = unicodedata.normalize("NFC", path).casefold()
            kind = "directory" if index < len(parts) or directory else "file"
            if folded in paths and paths[folded] != (path, kind):
                raise _LibraryError("ZIP contains case-colliding paths or a file/directory conflict.", 400)
            paths[folded] = (path, kind)
        if len(paths) > _LIBRARY_MAX_ENTRIES:
            raise _LibraryError("ZIP exceeds the expanded directory-entry limit.", 413)
        if not directory:
            files += 1
            total += info.file_size
            limit = (_LIBRARY_MAX_MANIFEST_BYTES if parts[-1] == "magazine.yaml"
                     else _LIBRARY_MAX_FILE_BYTES)
            if files > _LIBRARY_MAX_FILES or info.file_size > limit or total > _LIBRARY_MAX_TOTAL_BYTES:
                raise _LibraryError("ZIP exceeds file-count, file-size or expanded-content limits.", 413)
            if info.file_size > 128 * 1024 and info.file_size > max(1, info.compress_size) * _LIBRARY_MAX_EXPANSION_RATIO:
                raise _LibraryError("ZIP exceeds the permitted expansion ratio.", 413)
        elif info.file_size:
            raise _LibraryError("ZIP directory entry contains unexpected content.", 400)
        members.append((info, parts, directory))
    if any(parts == ["magazine.yaml"] and not directory for _, parts, directory in members):
        return members
    first_parts = {parts[0] for _, parts, _ in members}
    if len(first_parts) == 1:
        wrapper = next(iter(first_parts))
        if any(parts == [wrapper, "magazine.yaml"] and not directory for _, parts, directory in members):
            return [(info, parts[1:], directory) for info, parts, directory in members if len(parts) > 1]
    raise _LibraryError("ZIP must contain magazine.yaml at its root or inside one enclosing project folder.", 400)


def _library_extract_zip(archive, members, staging_fd):
    """Stream checked regular files into a private, newly created staging tree."""
    total = 0
    for info, parts, directory in members:
        parent = os.dup(staging_fd)
        try:
            for part in (parts if directory else parts[:-1]):
                try:
                    os.mkdir(part, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = child
            if directory:
                continue
            fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            size = 0
            with os.fdopen(fd, "wb") as target, archive.open(info) as source:
                while True:
                    chunk = source.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    total += len(chunk)
                    if size > info.file_size or size > _LIBRARY_MAX_FILE_BYTES or total > _LIBRARY_MAX_TOTAL_BYTES:
                        raise _LibraryError("ZIP exceeds expanded-content limits.", 413)
                    target.write(chunk)
                if size != info.file_size:
                    raise _LibraryError("ZIP content length does not match its directory.", 400)
                target.flush()
                os.fsync(target.fileno())
        finally:
            os.close(parent)


def _library_sync_directories(fd):
    with os.scandir(fd) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    _library_sync_directories(child)
                finally:
                    os.close(child)
    os.fsync(fd)


@contextlib.contextmanager
def _library_import_lock(root_fd):
    # Lock the directory inode: no persistent lock file or new shared database.
    import fcntl
    deadline = time.monotonic() + _LIBRARY_IMPORT_LOCK_SECONDS
    while True:
        try:
            fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise _LibraryError("Another project import is in progress; refresh and retry.", 409) from None
            time.sleep(0.05)
    try:
        yield
    finally:
        fcntl.flock(root_fd, fcntl.LOCK_UN)


class _PublicationError(_LibraryError):
    """Keep the syscall result with the failure; ctypes errno is thread-local state."""
    def __init__(self, message, status_code, error):
        super().__init__(message, status_code)
        self.errno = error
        self.unsupported = error in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP)


def _library_io_error(operation, error, root_fd=None):
    # Server stderr only. Browser errors below contain no configured paths.
    location = None
    if root_fd is not None:
        try:
            location = os.readlink('/proc/self/fd/' + str(root_fd))
        except OSError:
            pass
    print('Magazine filesystem: ' + json.dumps({
        'operation': operation, 'errno': error,
        'symbol': errno.errorcode.get(error, 'UNKNOWN'), 'directory': location,
    }), file=sys.stderr, flush=True)


def _library_publish(root_fd, staging, project_id):
    """Atomic directory publication that cannot overwrite even an empty target."""
    rename = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if rename is None:
        _library_io_error('renameat2(RENAME_NOREPLACE)', errno.ENOSYS, root_fd)
        raise _PublicationError("Atomic no-overwrite publication is unavailable on this runtime. Use a supported Linux runtime and Linux-backed Magazine storage.", 503, errno.ENOSYS)
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(root_fd, os.fsencode(staging), root_fd, os.fsencode(project_id), 1) != 0:  # RENAME_NOREPLACE
        error = ctypes.get_errno()
        _library_io_error('renameat2(RENAME_NOREPLACE)', error, root_fd)
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise _PublicationError("A project with that identifier already exists.", 409, error)
        if error in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP):
            raise _PublicationError("Atomic no-overwrite publication is unsupported by this filesystem or runtime. Use Linux-backed Magazine storage; existing projects were preserved.", 503, error)
        if error in (errno.EACCES, errno.EPERM, errno.EROFS):
            raise _PublicationError("Project publication was denied by storage permissions. Check Magazine storage access; existing projects were preserved.", 503, error)
        raise _PublicationError("Project publication failed with an unexpected storage I/O error. Inspect server logs; existing projects were preserved.", 503, error)


def _library_check_conflict(root_fd, project_id, name):
    names = []
    with os.scandir(root_fd) as entries:
        for count, entry in enumerate(entries, 1):
            if count > _LIBRARY_MAX_ROOT_ENTRIES:
                raise _LibraryError("Project library scan limit reached; import is unavailable.", 413)
            if entry.name.casefold() == project_id.casefold():
                raise _LibraryError("A project with that identifier already exists.", 409)
            if _LIBRARY_ID.fullmatch(entry.name):
                names.append(entry.name)
    if len(names) >= _LIBRARY_MAX_WORKFLOWS:
        raise _LibraryError("Project library capacity limit reached.", 413)
    folded_name = unicodedata.normalize("NFC", name.strip()).casefold()
    for identifier in names:
        preview = _library_preview_at(root_fd, identifier)
        if preview["valid"] and unicodedata.normalize("NFC", preview["name"].strip()).casefold() == folded_name:
            raise _LibraryError("A project with that workflow name already exists.", 409)


def _library_cleanup_stage(root_fd, staging, identity):
    current = os.stat(staging, dir_fd=root_fd, follow_symlinks=False)
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != identity:
        raise _LibraryError("Import staging identity changed; administrator review is required.", 500)
    shutil.rmtree(staging, dir_fd=root_fd)


def _library_import(root, file_name, file_data_b64, project_id=""):
    if not file_name.lower().endswith(".zip"):
        raise _LibraryError("Select a Project ZIP file.", 400)
    if not project_id:
        project_id = re.sub(r"[^A-Za-z0-9_-]+", "-", file_name[:-4]).strip("-_")[:64] or "Project"
    if not _LIBRARY_ID.fullmatch(project_id):
        raise _LibraryError("Invalid project identifier; use letters, digits, hyphens or underscores.", 400)
    if len(file_data_b64) > ((_LIBRARY_MAX_ARCHIVE_BYTES + 2) // 3) * 4:
        raise _LibraryError("ZIP exceeds the compressed upload limit.", 413)
    try:
        raw = base64.b64decode(file_data_b64, validate=True)
    except (ValueError, TypeError):
        raise _LibraryError("Invalid base64 Project ZIP upload.", 400) from None
    if len(raw) > _LIBRARY_MAX_ARCHIVE_BYTES:
        raise _LibraryError("ZIP exceeds the compressed upload limit.", 413)
    if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        raise _LibraryError("Safe project staging cleanup is unavailable on this runtime.", 503)
    with _library_root_fd(root) as root_fd:
        staging, identity, created, published = None, None, False, False
        try:
            with _library_import_lock(root_fd), zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = _library_zip_members(archive)
                staging = ".import-" + uuid.uuid4().hex
                os.mkdir(staging, mode=0o700, dir_fd=root_fd)
                created = True
                st = os.stat(staging, dir_fd=root_fd, follow_symlinks=False)
                identity = (st.st_dev, st.st_ino)
                stage_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                try:
                    _library_extract_zip(archive, members, stage_fd)
                    _library_sync_directories(stage_fd)
                finally:
                    os.close(stage_fd)
                preview = _library_preview_at(root_fd, staging)
                if not preview["valid"]:
                    raise _LibraryError(preview["error"], 400)
                _library_check_conflict(root_fd, project_id, preview["name"])
                _library_publish(root_fd, staging, project_id)
                published = True
                try:
                    os.fsync(root_fd)
                except OSError as error:
                    _library_io_error('import directory fsync after publication', error.errno, root_fd)
                    raise _LibraryError("Project was published, but directory synchronization failed. Refresh projects before retrying; administrator review is required.", 503) from None
                retained = _retain_input_zip(project_id, raw, root)
                return {"imported": True, "project_id": project_id, "name": preview["name"],
                        "cartridge_count": preview["cartridge_count"], "order": preview["order"],
                        "input_zip": retained,
                        "message": ("Project imported. " + retained["message"] +
                                    " Select it to preview and assign models; no run was started.")}
        except _LibraryError:
            raise
        except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError, ValueError):
            raise _LibraryError("Invalid or unsupported Project ZIP archive.", 400) from None
        except OSError as error:
            _library_io_error('import staging write/sync', error.errno, root_fd)
            if error.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
                raise _LibraryError("Project import was denied by storage permissions. Check Magazine storage access.", 503) from None
            raise _LibraryError("Project import could not write or synchronize staging files. Inspect server logs for the storage I/O error.", 503) from None
        except ImportError:
            raise _LibraryError("Project import could not write, synchronize or publish its staging files. Check library permissions and runtime support.", 503) from None
        finally:
            if created and not published:
                try:
                    if identity is None:
                        raise _LibraryError("Staging identity could not be confirmed.", 500)
                    _library_cleanup_stage(root_fd, staging, identity)
                except Exception:
                    raise _LibraryError("Failed-import staging cleanup could not be confirmed for temporary import " + staging + "; administrator review is required.", 500) from None


def _storage_root(kind):
    variable, directory = {
        "input": ("MAGAZINE_INPUT_ZIPS_DIR", "input-zips"),
        "finished": ("MAGAZINE_FINISHED_WORK_DIR", "finished-work"),
    }[kind]
    return os.environ.get(variable, str(Path(_runs_root()).parent / directory))


def _storage_token(label):
    """Readable, bounded, Windows-safe component with a 128-bit identity hash."""
    label = str(label)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-_")[:16] or "item"
    return "m-" + slug + "--" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:32]


@contextlib.contextmanager
def _storage_fd(kind, library_root=None):
    root = _storage_root(kind)
    # Keep deliverables separate from canonical runs, the shared library, and
    # each other. Ancestor checks and no-follow FD walks also reject root links.
    path = Path(root)
    other = Path(_storage_root("finished" if kind == "input" else "input"))
    library = Path(library_root or _STORAGE_LIBRARY_ROOT_GETTER())
    if not path.is_absolute() or ".." in path.parts:
        raise _LibraryError("Storage root must be an absolute container path without '..'.", 503)
    for protected in (other, library):
        if path.is_relative_to(protected) or protected.is_relative_to(path):
            raise _LibraryError("Dedicated storage roots must not overlap each other or the library.", 503)
    _initialize_library_root(str(path))
    with _library_root_fd(str(path)) as fd:
        yield fd


def _storage_write(fd, name, data):
    """Only new regular files inside an unpublished directory or unique temp."""
    handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=fd)
    with os.fdopen(handle, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


_STORAGE_LIBRARY_ROOT_GETTER = lambda: _LIBRARY_ROOT_DEFAULT


def _retain_input_zip(project_id, raw, library_root=None):
    name = _storage_token(project_id) + "--" + uuid.uuid4().hex + ".zip"
    temporary = ".zip-" + uuid.uuid4().hex
    try:
        with _storage_fd("input", library_root) as fd:
            try:
                _storage_write(fd, temporary, raw)
                # Hard-link publication is atomic and refuses ANY existing name.
                os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                os.unlink(temporary, dir_fd=fd)
                os.fsync(fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass
        return {"retained": True, "file": name, "sha256": hashlib.sha256(raw).hexdigest(),
                "message": "Original ZIP retained in Input ZIPs."}
    except Exception as error:
        # Import has already committed. Never encourage a conflicting re-import
        # or label a write/fsync failure as successful retention.
        print("[magazine] project imported but original ZIP retention failed (%s, errno=%s); keep original upload" % (type(error).__name__, getattr(error, "errno", None)), file=sys.stderr, flush=True)
        return {"retained": False,
                "message": "WARNING: project imported, but original ZIP retention could not be confirmed. Keep your original ZIP; check storage permissions and server logs. Do not re-import this project."}


def _export_record(owner_id, rid):
    try:
        with open(os.path.join(_run_base(owner_id, rid), "export.json"), encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return None


def _save_export_record(owner_id, rid, record):
    from magazine.durability import atomic_write
    atomic_write(os.path.join(_run_base(owner_id, rid), "export.json"),
                 (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))


def _canonical_result_bytes(owner_id, rid, cid):
    path = Path(_mag()["rs"].result_path(_state_dir(owner_id, rid), cid))
    fd = os.open(os.path.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        handle = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(handle, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise _LibraryError("Canonical result must be a regular file without hard links.", 503)
            return stream.read()
    finally:
        os.close(fd)


def _export_files(owner_id, rid):
    # Validate identifiers BEFORE consulting the existing run path helpers.
    if not _LIBRARY_ID.fullmatch(str(rid)) or not _LIBRARY_ID.fullmatch(str(owner_id)):
        raise _LibraryError("Run not found.", 404)
    payload = _results_payload(owner_id, rid)
    if payload is None:
        raise _LibraryError("Run not found.", 404)
    if payload.get("status") not in ("COMPLETED", "HALTED"):
        raise _LibraryError("Only COMPLETED or HALTED runs can be exported; saved results remain available.", 409)
    meta = _load_meta(owner_id, rid)
    files, cartridges = {}, []
    for index, cid in enumerate(payload["done"], 1):
        if payload["results"].get(cid) is None:
            raise _LibraryError("A completed cartridge's canonical result is missing or unreadable; export refused.", 503)
        # Reuse the canonical result-store naming and preserve UTF-8 bytes,
        # including CRLF: the UI's text reader normalizes line endings.
        data = _canonical_result_bytes(owner_id, rid, cid)
        data.decode("utf-8")
        name = "%04d-%s.txt" % (index, _storage_token(cid))
        files[name] = data
        cartridges.append({"id": cid, "file": name, "bytes": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()})
    # A concurrent recovery may change HALTED into READY/RUNNING. Refuse a
    # changing snapshot; never acquire or change the engine's execution claim.
    after = _results_payload(owner_id, rid)
    if after is None or any(after.get(k) != payload.get(k) for k in ("status", "revision", "done", "results")):
        raise _LibraryError("Run changed during export; retry after it has stopped.", 409)
    project = str(meta.get("name") or "Workflow")
    manifest = {"schema_version": "1.0", "run_id": rid, "owner_id": owner_id,
                "project_name": project, "project_id": meta.get("project_id"), "status": payload["status"],
                "revision": payload["revision"], "created_at": meta.get("created_at"),
                "cartridges": cartridges,
                "scope": "Canonical saved cartridge text only; no external tool files or reconstructed chats."}
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    buffer = io.BytesIO()
    # ZIP_STORED avoids version-dependent compression and streams exact bytes.
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            entry.external_attr = 0o100600 << 16
            archive.writestr(entry, data)
    files["results.zip"] = buffer.getvalue()
    parts = ["owner-" + hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:32],
             _storage_token(meta.get("project_id") or project), "run-" + rid,
             "snapshot-%s-%s" % (payload["revision"], payload["status"].lower())]
    return files, manifest, parts


def _storage_child(fd, name):
    try:
        os.mkdir(name, mode=0o700, dir_fd=fd)
        os.fsync(fd)
    except FileExistsError:
        pass
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)


def _export_matches(fd, name, files):
    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        if set(os.listdir(child)) != set(files):
            return False
        for filename, data in files.items():
            handle = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=child)
            with os.fdopen(handle, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != len(data):
                    return False
                if stream.read() != data:
                    return False
        return True
    finally:
        os.close(child)


def _storage_publish_snapshot(fd, staging, name, files, identity):
    try:
        _library_publish(fd, staging, name)
        return
    except _LibraryError as error:
        # Docker Desktop NTFS mounts return EINVAL for RENAME_NOREPLACE even
        # though exclusive mkdir, no-replace hard links, locks/fsync work.
        # Never fall back for conflicts or ordinary permission/I/O failures.
        if not isinstance(error, _PublicationError) or not error.unsupported:
            raise
    try:
        os.mkdir(name, mode=0o700, dir_fd=fd)
    except FileExistsError:
        raise _LibraryError("Export destination already exists; nothing was overwritten.", 409) from None
    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    target_identity = (info.st_dev, info.st_ino)
    source = target = None
    try:
        source = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        target = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        # All source files were fully synchronized before publication. Each
        # link is an atomic no-overwrite publication; manifest is the LAST
        # completion marker. No fallback rename can replace a user's directory.
        for filename in sorted(n for n in files if n != "manifest.json") + ["manifest.json"]:
            os.link(filename, filename, src_dir_fd=source, dst_dir_fd=target, follow_symlinks=False)
        os.fsync(target)
        _library_cleanup_stage(fd, staging, identity)
    except Exception:
        _library_cleanup_stage(fd, name, target_identity)
        raise
    finally:
        if source is not None:
            os.close(source)
        if target is not None:
            os.close(target)


def _export_run(owner_id, rid):
    """Publish a complete, repeatable terminal snapshot; never replace work."""
    files, manifest, parts = _export_files(owner_id, rid)
    with _storage_fd("finished") as root_fd:
        fd = os.dup(root_fd)
        staging, identity = None, None
        try:
            for part in parts[:-1]:
                next_fd = _storage_child(fd, part)
                os.close(fd)
                fd = next_fd
            with _library_import_lock(fd):
                try:
                    exists = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    exists = None
                if exists is not None:
                    if not _export_matches(fd, parts[-1], files):
                        raise _LibraryError("Export destination already contains different work; nothing was overwritten.", 409)
                    os.fsync(fd)
                else:
                    staging = ".export-" + uuid.uuid4().hex
                    os.mkdir(staging, mode=0o700, dir_fd=fd)
                    info = os.stat(staging, dir_fd=fd, follow_symlinks=False)
                    identity = (info.st_dev, info.st_ino)
                    child = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        for filename, data in files.items():
                            _storage_write(child, filename, data)
                        os.fsync(child)
                    finally:
                        os.close(child)
                    _storage_publish_snapshot(fd, staging, parts[-1], files, identity)
                    staging = None
                    os.fsync(fd)
        finally:
            try:
                if staging is not None and identity is not None:
                    _library_cleanup_stage(fd, staging, identity)
            finally:
                os.close(fd)
    record = {"status": "EXPORTED", "run_status": manifest["status"],
              "revision": manifest["revision"], "directory": "/".join(parts),
              "zip": "/".join(parts + ["results.zip"]),
              "cartridge_count": len(manifest["cartridges"])}
    _save_export_record(owner_id, rid, record)
    return record


def _attempt_export(owner_id, rid):
    try:
        return _export_run(owner_id, rid)
    except Exception as error:
        record = {"status": "FAILED", "message": "Finished Work export failed. Canonical results and execution status are unchanged; check configured storage permissions, paths and free space, then retry POST /api/magazine/runs/" + rid + "/export."}
        # Only authenticated/known owner runs may receive a sidecar. Never
        # persist an authorization error or an unsafe request identifier.
        if (_LIBRARY_ID.fullmatch(str(owner_id)) and _LIBRARY_ID.fullmatch(str(rid))
                and _load_meta(owner_id, rid).get("owner_id") == owner_id):
            try:
                _save_export_record(owner_id, rid, record)
            except Exception:
                pass
        print("[magazine] export failed for run %s (%s); canonical results unchanged" % (rid, type(error).__name__), file=sys.stderr, flush=True)
        return record


async def _auto_export_completed(owner_id, rid):
    # Export I/O cannot change execution success or the runner's exception path.
    try:
        payload = _status_payload(owner_id, rid)
        if payload and payload.get("status") == "COMPLETED":
            await asyncio.to_thread(_attempt_export, owner_id, rid)
    except Exception:
        print("[magazine] automatic export could not be checked; canonical state unchanged", file=sys.stderr, flush=True)


def _status_payload(owner_id, rid):
    m = _mag()
    if not m.get("ok"):
        return {"run_id": rid, "status": "error", "error": m.get("error", "")}
    ss = m["ss"]
    sp = _state_path(owner_id, rid)
    if not os.path.exists(sp):
        return None  # no such run for this owner -> 404
    try:
        state = ss.load_run_state(sp)
    except Exception as e:
        return {"run_id": rid, "status": "error", "error": repr(e),
                "exists": os.path.exists(sp)}
    meta = _load_meta(owner_id, rid)
    if not meta or meta.get("owner_id") != owner_id:
        return None  # not owned by the caller -> 404
    # Optional execution block: read the run's execution-ownership claim if
    # present.  Read-only, no lock, no side effects.  When the claim file is
    # absent (a run with no claim yet), the block is null.  owner_alive
    # compares the claim's runtime_id against this process's own runtime_id
    # (a plain string equality, per the design: it *surfaces* FM-2 without
    # acting on it).
    execution = None
    try:
        claim = m["em"].load_execution_metadata(
            os.path.join(os.path.dirname(sp), "execution.json"))
        if claim is not None:
            cur = m["ri"].get_runtime_identity().runtime_id
            execution = {
                "attempt": claim.attempt,
                "runtime_id": claim.runtime_id,
                "owner_alive": claim.runtime_id == cur,
                "halted": claim.halted_by is not None,
                "halt_reason": claim.halt_reason,
            }
    except Exception:
        execution = None
    control = None
    try:
        control = m["rc"].load_stop_control(sp)
    except Exception as exc:
        control = {"error": repr(exc)}
    return {
        "run_id": rid,
        "status": state["status"],
        "current_cartridge": state.get("current"),
        "done": state.get("done", []),
        "todo": state.get("todo", []),
        "revision": state.get("revision", 0),
        "model_map": meta.get("model_map"),
        "tool_ids": meta.get("tool_ids"),
        "owner_id": meta.get("owner_id"),
        "created_at": meta.get("created_at"),
        "execution": execution,
        "control": control,
    }


def _load_state(owner_id, rid):
    m = _mag()
    if not m.get("ok"):
        raise ValueError(m.get("error", ""))
    sp = _state_path(owner_id, rid)
    if not os.path.exists(sp):
        raise KeyError("run not found")
    return m["ss"].load_run_state(sp)


def _results_payload(owner_id, rid):
    """Owner-scoped native result data for a run.

    Reads the existing run state (magazine.state_store) and the per-cartridge
    durable result files (magazine.result_store, ``<run_dir>/results/<token>.txt``).
    No second result format is created: the ``results`` map is keyed by the
    completed cartridge IDs from the run state and each value is the verbatim
    text of that cartridge's canonical result file.  Returns ``None`` when the
    run does not exist for the caller or is not owned by the caller (HTTP 404).
    """
    m = _mag()
    if not m.get("ok"):
        return {"run_id": rid, "status": "error", "error": m.get("error", "")}
    ss = m["ss"]
    rs = m["rs"]
    sp = _state_path(owner_id, rid)
    if not os.path.exists(sp):
        return None  # no such run for this owner -> 404
    try:
        state = ss.load_run_state(sp)
    except Exception as e:
        return {"run_id": rid, "status": "error", "error": repr(e),
                "exists": os.path.exists(sp)}
    meta = _load_meta(owner_id, rid)
    if not meta or meta.get("owner_id") != owner_id:
        return None  # not owned by the caller -> 404
    run_dir = os.path.dirname(sp)
    results = {}
    for cid in state.get("done", []):
        p = rs.result_path(run_dir, cid)
        try:
            with open(p, "r", encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            text = None
        except Exception:
            text = None
        results[cid] = text
    return {
        "run_id": rid,
        "status": state["status"],
        "done": state.get("done", []),
        "revision": state.get("revision", 0),
        "model_map": meta.get("model_map"),
        "owner_id": meta.get("owner_id"),
        "created_at": meta.get("created_at"),
        "results": results,
    }


def _start_precheck(payload, api_key):
    """Pure, side-effect-free decision for the /start route.

    Returns `` (ok, status_code, detail) ``.  Gates are ordered ownership
    -> auth -> state: a missing or unowned run yields 404 before any state is
    disclosed; a missing/empty usable bearer token yields 401; only then is the
    run status checked and a non-READY run yields 409.
    """
    if payload is None:
        return False, 404, "run not found"
    if not api_key:
        return False, 401, "no usable bearer token"
    if payload["status"] != "READY":
        return False, 409, "cannot start a %s run" % payload["status"]
    return True, 202, "run is READY and owned by the caller"


def _lost_runtime_interception(payload, owner_id, rid):
    """Slice 4: prove ownership of a RUNNING run before the /start precheck.

    Returns ``None`` when the run is not RUNNING (no interception; the normal
    precheck decides).  Returns the string ``"same_runtime"`` when this process
    owns the run (the normal precheck then rejects the double-start with 409).
    Returns a dict ``{status_code, status, detail, [execution]}`` when the run
    is auto-halted (lost-owner) or cannot be started because the recorded
    owner is still live/ambiguous or for a lock / TOCTOU reason
    (``owner_live`` / ``locked`` / ``state_changed`` / ``claim_changed``).
    """
    if payload is None or payload.get("status") != "RUNNING":
        return None
    m = _mag()
    identity = m["ri"].get_runtime_identity()
    outcome = m["racton"].recover_lost_runtime(
        _state_path(owner_id, rid),
        _lock_path(owner_id, rid),
        owner_id,
        identity.runtime_id, identity.pid, identity.start_ticks,
        expected_status="RUNNING",
    )
    if outcome.outcome == "halted":
        refreshed = _status_payload(owner_id, rid)
        return {
            "status_code": 409,
            "status": "HALTED",
            "detail": "auto-halted (lost runtime); recover to READY first",
            "execution": (refreshed or {}).get("execution"),
        }
    if outcome.outcome == "no_claim":
        return {
            "status_code": 409,
            "status": "RUNNING",
            "detail": ("legacy RUNNING run -- no ownership evidence; "
                        "recover manually"),
        }
    if outcome.outcome == "same_runtime":
        return "same_runtime"
    if outcome.outcome == "owner_live":
        return {
            "status_code": 409,
            "status": "RUNNING",
            "detail": ("cannot start: recorded execution owner is still "
                       "alive or its liveness is ambiguous"),
        }
    # "locked" / "state_changed" / "claim_changed".
    return {
        "status_code": 409,
        "status": (outcome.state or payload).get("status"),
        "detail": f"cannot start: lock not owned ({outcome.outcome})",
    }


def _recover_run_decision(owner_id, rid):
    """Slice 4: operator-recovery decision for the /recover route.

    Returns ``(status_code, body)``.  HALTED -> READY recovery and retirement
    of a positively extinct owner's READY claim both return 200 without
    starting execution.  Missing/unowned runs return 404; ineligible or
    conflicting runs return 409; persistence failures return 500.
    """
    payload = _status_payload(owner_id, rid)
    if payload is None:
        return 404, {"detail": "Run not found"}
    if (payload.get("control") or {}).get("request") == "STOP":
        return 409, {
            "detail": "Run has a durable Stop request and cannot be recovered",
            "status": payload.get("status"),
            "control": payload.get("control"),
        }
    status = payload.get("status")
    if status not in ("HALTED", "READY"):
        return 409, {"detail": "Run is %s, not HALTED" % payload["status"]}
    state_path = _state_path(owner_id, rid)
    lock_path = _lock_path(owner_id, rid)
    m = _mag()
    identity = m["ri"].get_runtime_identity()
    try:
        if status == "HALTED":
            m["racton"].persist_recover_halted(
                state_path, lock_path, owner_id,
                identity.runtime_id, identity.pid, identity.start_ticks,
            )
        else:
            outcome = m["racton"].recover_lost_runtime(
                state_path, lock_path, owner_id,
                identity.runtime_id, identity.pid, identity.start_ticks,
                expected_status="READY",
            )
            if outcome.outcome == "no_claim":
                return 409, {"detail": "Run is READY, not HALTED"}
            if outcome.outcome != "ready_recovered":
                return 409, {"detail": "READY recovery refused: %s" % outcome.outcome}
    except Exception as e:
        return 500, {"detail": "recovery failed: %r" % e}
    # Re-fetch: the run is READY now.
    return 200, _status_payload(owner_id, rid)


async def _run_bg_dispatch(rid, magazine_path, state_dir, owner_id,
                           model_map, prepared, dispatch):
    """Dispatch an *already-admitted* run in-process as a detached task.

    The run is already admitted (execution claim written, READY -> RUNNING
    committed) by the caller before this task is created; this handler performs
    only the dispatch, never re-admission.  On success the core has already
    persisted COMPLETED. On a dispatch or execution exception the core's
    fenced error path fence-persists a HALTED state (RUNNING -> HALTED, the
    error preserved in the execution claim) before re-raising; this handler
    only emits the error to stderr because the OWUI process is the only
    visible channel. No secrets are written to any file or log."""
    m = _mag()
    try:
        await m["ar"].run_dispatch(
            magazine_path=magazine_path,
            state_path=prepared.state_path,
            lock_path=prepared.lock_path,
            owner_id=owner_id,
            prepared=prepared,
            model_map=model_map,
            dispatch=dispatch,
        )
    except Exception as e:
        msg = "[magazine] run %s dispatch error: %r" % (rid, repr(e))
        try:
            print(msg, file=sys.stderr, flush=True)
        except Exception:
            pass

    await _auto_export_completed(owner_id, rid)


async def _stop_run_decision(owner_id, rid):
    """Durably request Stop, then cancel only this owner's local dispatch.

    Returns ``(status_code, body)``. A 200 ``stopped`` response is emitted
    only after the run is durably HALTED. A server cancellation failure or
    timeout remains a 202 ``stop_requested`` response and does not cancel the
    local Magazine task, allowing a completed output to be checkpointed before
    the safe cartridge boundary consumes the request.
    """
    payload = _status_payload(owner_id, rid)
    if payload is None:
        return 404, {"detail": "Run not found"}
    if payload.get("status") in ("COMPLETED",):
        return 409, {"detail": "Completed runs cannot be stopped"}
    existing = payload.get("control")
    if isinstance(existing, dict) and existing.get("termination_status") == "CONFIRMED":
        return 200, {
            "run_id": rid, "status": "stopped", "control": existing,
            "message": "Stop was already confirmed",
        }
    if isinstance(existing, dict) and existing.get("termination_status") == "NOT_APPLIED":
        return 409, {
            "run_id": rid, "status": "stop_not_applied", "control": existing,
            "detail": "Run ended independently before Stop could take effect",
        }
    if payload.get("status") == "HALTED" and existing is None:
        return 409, {
            "run_id": rid, "status": "already_halted",
            "detail": "Run is already halted; there is no active workflow to stop",
        }

    m = _mag()
    identity = m["ri"].get_runtime_identity()
    claim = m["em"].load_execution_metadata(
        os.path.join(os.path.dirname(_state_path(owner_id, rid)), "execution.json")
    )
    if claim is None:
        return 409, {"detail": "Run has no active execution to stop"}
    if claim.runtime_id != identity.runtime_id:
        if isinstance(existing, dict) and existing.get("request") == "STOP":
            outcome = m["racton"].recover_lost_runtime(
                _state_path(owner_id, rid), _lock_path(owner_id, rid), owner_id,
                identity.runtime_id, identity.pid, identity.start_ticks,
                expected_status="RUNNING",
            )
            if outcome.outcome == "halted":
                return 202, {
                    "run_id": rid,
                    "status": "stop_requested",
                    "control": existing,
                    "message": ("The restarted Magazine process durably halted "
                                "the run, but cancellation of any previously "
                                "active inference could not be confirmed"),
                }
        return 409, {
            "detail": ("Run belongs to a different process generation and "
                       "active inference cancellation cannot be confirmed"),
            "status": payload.get("status"),
            "control": existing,
        }

    if (
        isinstance(existing, dict)
        and existing.get("request") == "STOP"
        and existing.get("cancellation_status") in ("NOT_ATTEMPTED", "FAILED")
    ):
        return 202, {
            "run_id": rid,
            "status": "stop_requested",
            "control": existing,
            "message": ("Stop was already durably requested; Magazine will "
                        "not duplicate the cancellation request"),
        }

    state_path = _state_path(owner_id, rid)
    lock_path = _lock_path(owner_id, rid)
    try:
        control = m["rc"].persist_request_stop(
            state_path, lock_path, owner_id, claim.attempt, identity
        )
    except Exception as exc:
        return 500, {"detail": "Stop request could not be persisted: %r" % exc}

    handle = _RUN_HANDLES.get((owner_id, rid))
    if handle is None:
        # The local task may have just reached a safe READY boundary. Consume
        # the request there if possible; never invent cancellation evidence.
        try:
            stopped = m["rc"].persist_stop_if_requested(
                state_path, lock_path, owner_id, claim.attempt, identity,
                method="boundary-without-local-task",
            )
        except Exception:
            stopped = None
        refreshed = _status_payload(owner_id, rid)
        if stopped is not None and refreshed.get("control", {}).get("termination_status") == "CONFIRMED":
            return 200, {
                "run_id": rid, "status": "stopped",
                "control": refreshed["control"],
                "message": "Workflow stopped at a safe cartridge boundary",
            }
        return 202, {
            "run_id": rid, "status": "stop_requested", "control": control,
            "message": "Stop is durable; no local active inference could be confirmed",
        }

    dispatch = handle["dispatch"]
    task = handle["task"]
    cancellation = await dispatch.cancel_active()
    try:
        control = m["rc"].persist_cancellation_result(
            state_path, lock_path, owner_id, claim.attempt, identity,
            confirmed=cancellation.confirmed,
            detail=cancellation.detail,
        )
    except Exception as exc:
        return 500, {
            "detail": "Cancellation evidence could not be persisted: %r" % exc,
            "status": "stop_requested",
        }

    if cancellation.confirmed:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            # The core error boundary has already attempted a fenced HALT.
            pass
    elif not cancellation.active:
        # No inference was accepted, or the task is between cartridges. Give
        # the cooperative boundary one event-loop turn without cancelling it.
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass
    else:
        return 202, {
            "run_id": rid,
            "status": "stop_requested",
            "control": control,
            "message": ("Stop is durable, but active inference cancellation "
                        "was not confirmed; no successful cancellation is reported"),
        }

    # Once the local task has ended, close a possible READY/RUNNING race under
    # the same claim fence. This is safe after a confirmed provider cancel, or
    # when no provider task was ever accepted.
    if task.done():
        try:
            refreshed_state = m["ss"].load_run_state(state_path)
            if refreshed_state["status"] in ("READY", "RUNNING"):
                m["rc"].persist_stop_if_requested(
                    state_path, lock_path, owner_id, claim.attempt, identity,
                    method=("active-inference-cancelled" if cancellation.confirmed
                            else "boundary-before-inference"),
                )
            refreshed_state = m["ss"].load_run_state(state_path)
            if refreshed_state["status"] == "HALTED":
                m["rc"].persist_confirm_termination(
                    state_path, lock_path, owner_id, claim.attempt, identity,
                    ("active-inference-cancelled" if cancellation.confirmed
                     else "boundary-before-inference"),
                )
        except Exception:
            pass

    refreshed = _status_payload(owner_id, rid)
    final_control = (refreshed or {}).get("control") or control
    if final_control.get("termination_status") == "NOT_APPLIED":
        return 409, {
            "run_id": rid, "status": "stop_not_applied", "control": final_control,
            "detail": "Run ended independently before Stop could take effect",
        }
    if (
        refreshed is not None
        and refreshed.get("status") == "HALTED"
        and final_control.get("termination_status") == "CONFIRMED"
    ):
        return 200, {
            "run_id": rid, "status": "stopped", "control": final_control,
            "message": "Workflow termination is durably confirmed",
        }
    return 202, {
        "run_id": rid, "status": "stop_requested", "control": final_control,
        "message": "Stop is durable and will take effect at the next safe boundary",
    }


