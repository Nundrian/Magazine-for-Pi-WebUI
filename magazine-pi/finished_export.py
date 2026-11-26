"""Where "Export Finished Work" goes (0.1.0-pi.10.2).

Before pi.10.2 the export was written only to Magazine's own data volume (``finished-work``), which the owner
cannot see from Windows, so the button appeared to do nothing. Now:

* **download** (default): the export is also handed to the browser as one ZIP, which Firefox saves in the
  Downloads folder (or asks, if the browser is set to ask);
* **folder**: the export is also written into a folder the owner chose. It must be inside a folder mounted into
  the Pi container that workers may already write (the workspace or ``workerWritePaths``) and never inside
  Magazine/Pi state. Existing exports are never overwritten: a different export gets a new numbered folder.

The canonical copy in Magazine's own storage is still written first, unchanged.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
import uuid

import beta5_helpers as h
import confined_fs as fs
from run_export import redact

PREF_NAME = 'export.json'
MODES = ('download', 'folder')
MAX_EXPORT_BYTES = 16 * 1024 * 1024


def _pref_file(data_dir) -> Path:
    return Path(data_dir) / 'preferences' / PREF_NAME


def preferences(data_dir) -> dict:
    try:
        d = json.loads(_pref_file(data_dir).read_text(encoding='utf-8'))
        if d.get('mode') in MODES:
            return {'mode': d['mode'], 'folder': d.get('folder') if d['mode'] == 'folder' else None}
    except (OSError, ValueError, AttributeError):
        pass
    return {'mode': 'download', 'folder': None}


def _inside(path, root) -> bool:
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def check_folder(folder, allowed, protected) -> str:
    if not isinstance(folder, str) or not folder.startswith('/') or '\0' in folder or len(folder) > 1024 \
            or '..' in Path(folder).parts:
        raise ValueError('Choose an absolute folder inside the Pi workspace, for example /workspace/Magazine exports')
    return str(fs.permitted(folder, allowed, protected))


def set_preferences(data_dir, mode, folder, allowed, protected) -> dict:
    if mode not in MODES:
        raise ValueError('Mode must be download or folder')
    value = {'mode': mode, 'folder': check_folder(folder, allowed, protected) if mode == 'folder' else None}
    p = _pref_file(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.export-', dir=str(p.parent))
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(value, f)
    os.replace(tmp, p)
    return value


def windows_mounts(mountinfo='/proc/self/mountinfo') -> list[tuple[str, str]]:
    """Container mount point -> Windows path, from Docker Desktop's drvfs/9p mounts (longest first)."""
    out = []
    try:
        lines = Path(mountinfo).read_text().splitlines()
    except OSError:
        return out
    dec = lambda t: re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), t)
    for line in lines:
        m = re.search(r'path=([A-Za-z]:\\[^;,\s]*)', line)
        fields = line.split()
        if not m or len(fields) < 5:
            continue
        # Field 4 is the folder inside the shared drive (Docker Desktop shares E:\ and binds a subfolder of it).
        win = dec(m.group(1)).rstrip('\\')
        sub = dec(fields[3]).strip('/')
        if sub:
            win += '\\' + sub.replace('/', '\\')
        out.append((dec(fields[4]), win if len(win) > 2 else win + '\\'))
    return sorted(out, key=lambda x: -len(x[0]))


def windows_path(container_path, mounts=None) -> str | None:
    for point, win in (windows_mounts() if mounts is None else mounts):
        if _inside(container_path, point):
            rest = os.path.relpath(os.path.realpath(container_path), os.path.realpath(point))
            win = win.rstrip('\\')
            return win if rest == '.' else win + '\\' + rest.replace('/', '\\')
    return None


def slug(text) -> str:
    return re.sub(r'[^A-Za-z0-9_-]+', '-', str(text or 'Workflow')).strip('-_')[:48] or 'Workflow'


def zip_bytes(files: dict) -> bytes:
    if sum(map(len, files.values())) > MAX_EXPORT_BYTES:
        raise ValueError('Finished Work delivery exceeds 16 MiB')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100600 << 16
            z.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def redact_delivery(files, manifest, secrets):
    """Keep the internal export canonical. Redact credentials in copies delivered to the owner."""
    export_manifest = redact(manifest, secrets)
    result = {}
    for row in export_manifest['cartridges']:
        name = row['file']
        data = redact(files[name].decode('utf-8'), secrets).encode('utf-8')
        result[name] = data
        row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    if result == {k: files[k] for k in result} and export_manifest == manifest:
        return files, manifest
    result['manifest.json'] = (json.dumps(export_manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()
    result['results.zip'] = zip_bytes(result)
    return result, export_manifest


def _same(parent, name, files):
    with fs.child_directory(parent, name) as target:
        return set(os.listdir(target)) == set(files) and all(
            fs.read_plain(target, filename, len(data)) == data for filename, data in files.items())


def deliver_to_folder(folder, base_name, files: dict, *, allowed, protected) -> dict:
    """Write the export into <folder>/<base_name>[-N]/ with new files only; identical re-exports are reused."""
    root = Path(check_folder(folder, allowed, protected))
    fs.plain_name(base_name)
    for name in files:
        fs.plain_name(name)
    if sum(map(len, files.values())) > MAX_EXPORT_BYTES:
        raise ValueError('Finished Work delivery exceeds 16 MiB')
    # Existing settings may name a new immediate child of an already mounted folder.
    with fs.directory(root.parent) as parent, fs.child_directory(parent, root.name, create=True) as target:
        fs.same_directory(root, target)
        for n in range(1, 100):
            name = base_name if n == 1 else f'{base_name}-{n}'
            try:
                existing = os.stat(name, dir_fd=target, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                try:
                    if _same(target, name, files):
                        fs.same_directory(root, target)
                        return {'container_path': str(root / name), 'reused': True}
                except (OSError, ValueError):
                    pass
                continue
            stage = '.magazine-export-' + uuid.uuid4().hex
            os.mkdir(stage, mode=0o700, dir_fd=target)
            with fs.child_directory(target, stage) as scratch:
                identity = fs.identity(os.fstat(scratch))
                try:
                    for filename, data in files.items():
                        fs.write_plain(scratch, filename, data)
                    os.fsync(scratch)
                    fs.same_directory(root, target)
                    fs.same_directory(root / stage, scratch)
                    if not _same(target, stage, files):
                        raise ValueError('Finished Work files changed before publication')
                    h._storage_publish_snapshot(target, stage, name, files, identity)
                    stage = None  # Publication consumes the staging directory in both paths.
                    os.fsync(target)
                    fs.same_directory(root, target)
                    if not _same(target, name, files):
                        raise ValueError('Could not verify the delivered Finished Work files')
                    return {'container_path': str(root / name), 'reused': False}
                except h._LibraryError as error:
                    if error.status_code != 409:
                        raise ValueError(str(error)) from None
                finally:
                    if stage is not None:
                        h._library_cleanup_stage(target, stage, identity)
    raise ValueError('Too many exports with this name in that folder')
