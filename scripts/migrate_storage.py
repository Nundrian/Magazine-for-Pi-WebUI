"""Copy idle Magazine state to EMPTY Linux storage; never delete the source.

Run with the affected Pi host stopped, source mounted read-only, and sufficient
privilege to preserve numeric ownership. Back up the entire Pi data directory
and Compose configuration first. This helper does not change Docker mounts.
"""
import argparse
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path


def inventory(root):
    result = {}
    for path in [root] + sorted(root.rglob('*')):
        info = path.lstat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError('State contains a link or special file; manual review required')
        key = path.relative_to(root).as_posix()
        record = {'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid,
                  'gid': info.st_gid, 'directory': stat.S_ISDIR(info.st_mode)}
        if not record['directory']:
            record.update(size=info.st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        result[key] = record
    return result


def require_idle(source):
    for path in (source / 'runs').rglob('state.json'):
        if json.loads(path.read_text())['status'] in ('RUNNING', 'QUEUED', 'STOP_REQUESTED'):
            raise ValueError('Active run found; migration refused')
    scheduler = source / 'scheduler-v1.json'
    if scheduler.exists():
        value = json.loads(scheduler.read_text())
        if value.get('queue') or value.get('current'):
            raise ValueError('Occupied scheduler found; migration refused')


def migrate(source, destination, verify_only=False):
    source, destination = Path(source), Path(destination)
    # Refuse linked roots/ancestors, overlapping trees and unexpected targets.
    for root in (source, destination):
        if not root.is_absolute() or any(p.is_symlink() for p in [root] + list(root.parents)):
            raise ValueError('Use absolute, unlinked storage directories')
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('Storage directories must be disjoint')
    expected = inventory(source)
    require_idle(source)
    if not verify_only:
        if any(destination.iterdir()):
            raise ValueError('Destination must be empty; nothing was overwritten')
        # Failure leaves an unselected copy for inspection. The source is never
        # retired. Retry requires a new empty volume, never a merge/overwrite.
        shutil.copytree(source, destination, dirs_exist_ok=True)
        for relative, record in reversed(list(expected.items())):
            target = destination / relative
            os.chown(target, record['uid'], record['gid'], follow_symlinks=False)
            os.chmod(target, record['mode'], follow_symlinks=False)
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    if inventory(source) != expected or inventory(destination) != expected:
        raise ValueError('State/ownership/permissions verification failed; do not switch mounts')
    return {'verified': True, 'entries': len(expected), 'inventory': expected,
            'source_retained': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.destination, args.verify_only), indent=2))
