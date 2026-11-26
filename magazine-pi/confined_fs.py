"""Descriptor-relative access for privileged operations on project files."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import uuid

from run_export import directory


def plain_name(name):
    if (not isinstance(name, str) or not name or name in ('.', '..')
            or any(c in name for c in ('/', '\\', '\0')) or len(os.fsencode(name)) > 255):
        raise ValueError('Expected a plain file or directory name')
    return name


def within(path, root):
    return path == root or root in path.parents


def permitted(path, allowed, protected):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Choose an absolute project folder')
    excluded = {root for p in protected for root in (Path(p), Path(p).resolve())}
    if any(within(path, p) or within(p, path) for p in excluded):
        raise ValueError('Folder is inside Magazine or Pi state or contains protected data')
    if not any(within(path, Path(p)) for p in allowed):
        raise ValueError('Folder is outside the folders workers may write')
    return path


def identity(info):
    return info.st_dev, info.st_ino


def same_directory(path, fd):
    with directory(path) as current:
        if identity(os.fstat(fd)) != identity(os.fstat(current)):
            raise ValueError('Project directory changed during the operation')


@contextmanager
def child_directory(parent, name, *, create=False):
    plain_name(name)
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
        else:
            os.fsync(parent)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def read_plain(parent, name, limit):
    plain_name(name)
    if hasattr(os, 'O_PATH') and os.path.isdir('/proc/self/fd'):
        # Inspect the inode before opening it for I/O, so a special file
        # cannot trigger a device open. Reopen the pinned inode through procfs.
        claim = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent)
        try:
            _regular(os.fstat(claim), name, limit)
            fd = os.open(f'/proc/self/fd/{claim}', os.O_RDONLY | os.O_NONBLOCK)
        finally:
            os.close(claim)
    else:
        raise ValueError('Safe project file reads require Linux procfs')
    with os.fdopen(fd, 'rb') as stream:
        initial = os.fstat(fd)
        _regular(initial, name, limit)
        raw = stream.read(limit + 1)
        final = os.fstat(fd)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (len(raw) > limit or len(raw) != initial.st_size
                or _snapshot(initial) != _snapshot(final) or _snapshot(final) != _snapshot(current)):
            raise ValueError(f'{name} changed while it was read or exceeds its size limit')
        return raw


def _snapshot(info):
    return identity(info), info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _regular(info, name, limit):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError(f'{name} must be a regular file without links')
    if info.st_size > limit:
        raise ValueError(f'{name} exceeds its size limit')


def write_plain(parent, name, raw):
    plain_name(name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(name, flags, 0o600, dir_fd=parent)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(fd)
        return identity(os.fstat(fd))


def replace_plain(parent, name, raw, before_replace=None):
    plain_name(name)
    temp = f'.magazine-{uuid.uuid4().hex}.tmp'
    written = write_plain(parent, temp, raw)
    try:
        if before_replace is not None:
            before_replace()
        staged = os.stat(temp, dir_fd=parent, follow_symlinks=False)
        if identity(staged) != written or staged.st_nlink != 1:
            raise ValueError('Staged project file changed before publication')
        os.replace(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            staged = os.stat(temp, dir_fd=parent, follow_symlinks=False)
            if identity(staged) == written:
                os.unlink(temp, dir_fd=parent)
        except FileNotFoundError:
            pass
