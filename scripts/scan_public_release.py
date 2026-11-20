"""Scan the exact staged GitHub source and release ZIPs; never print secrets.

Optional operator-supplied private credential sources remain outside the public
tree. Generic private-key/token patterns are also checked. Reports are evidence
of these checks, not proof that every possible secret format has been detected.
"""
import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

import yaml


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source-tree', type=Path, required=True)
    p.add_argument('--archive', type=Path, action='append', required=True)
    p.add_argument('--private-env', type=Path, action='append', default=[])
    p.add_argument('--private-owner', type=Path, action='append', default=[])
    p.add_argument('--private-json', type=Path, action='append', default=[])
    p.add_argument('--private-compose', type=Path, action='append', default=[])
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    secrets = set()
    sensitive = re.compile(r'(^key$|token|password|secret|api.?key|access.?key|refresh|credential)', re.I)
    def collect(value, name=''):
        if isinstance(value, dict):
            for k, v in value.items(): collect(v, k)
        elif isinstance(value, list):
            for v in value: collect(v, name)
        elif isinstance(value, str) and sensitive.search(name) and len(value) >= 12 and not value.startswith('${'):
            secrets.add(value.encode())
    for path in args.private_env:
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                name, value = line.split('=', 1); collect(value.strip().strip('"\''), name)
    for path in args.private_owner: secrets.add(path.read_text(encoding='utf-8-sig').strip().encode())
    for path in args.private_json: collect(json.loads(path.read_text(encoding='utf-8-sig')))
    for path in args.private_compose: collect(yaml.safe_load(path.read_text(encoding='utf-8-sig')))
    assert all(secrets), 'Empty credential input'
    forbidden = {'runtime', 'private', 'production-backups', 'reference', 'evidence', '__pycache__', '.git', 'node_modules', '.baseline-venv'}
    # Split literals so the scanner source does not match its own sensitive patterns.
    patterns = [rb'-----BEGIN (?:RSA |EC |OPENSSH )?' + rb'PRIVATE KEY-----',
                rb'\bsk-' + rb'(?:proj-)?[A-Za-z0-9_-]{32,}',
                rb'\bgh[pousr]_' + rb'[A-Za-z0-9]{30,}']
    checked = []
    def scan(name, data):
        assert not (set(Path(name).parts) & forbidden), 'Excluded private path: ' + name
        assert Path(name).name not in ('.env', 'config.json', 'auth.json', 'import-key'), 'Private file: ' + name
        # deployment/config.json is an explicitly reviewed public example, handled below.
        for secret in secrets:
            assert secret not in data, 'Known private credential match; file: ' + name
        assert not any(re.search(pattern, data) for pattern in patterns), 'Secret-like pattern; file: ' + name
    for path in sorted(args.source_tree.rglob('*')):
        if path.is_file():
            name = path.relative_to(args.source_tree).as_posix()
            assert not path.is_symlink()
            if name.endswith('deployment/config.json'):
                assert json.loads(path.read_text())['allowedTools'] == []
                name = name.replace('deployment/config.json', 'deployment/config.example.json')
            scan(name, path.read_bytes()); checked.append(name)
    archives = []
    for path in args.archive:
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None
            for name in archive.namelist():
                data = archive.read(name)
                if name.endswith('/deployment/config.json'):
                    assert json.loads(data)['allowedTools'] == []
                    name = name.replace('/deployment/config.json', '/deployment/config.example.json')
                scan(name, data)
            archives.append({'file': path.name, 'members': len(archive.namelist()),
                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    report = {'PASS': True, 'source_files_scanned': len(checked), 'archives': archives,
        'known_private_values_checked_without_disclosure': len(secrets), 'generic_patterns_checked': len(patterns),
        'private_paths_excluded': True, 'scope': 'exact staged public source and supplied ZIPs; not the private development directory',
        'limitations': 'Known supplied credentials and common token/private-key patterns; not an exhaustive secret-format guarantee'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
