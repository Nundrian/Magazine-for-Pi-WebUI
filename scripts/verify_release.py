"""Read-only release inventory/parity checks, with optional evidence output."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {'.py', '.mjs', '.json', '.md', '.txt', '.yaml', '.yml', '.sh', '.ini'}


def public_files(directory):
    return [p for p in directory.rglob('*') if p.is_file()
            and '__pycache__' not in p.parts and p.suffix in ALLOWED
            and p.name != 'config.json']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reference-manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--previous-plugin', type=Path)
    parser.add_argument('--style-only-client', action='store_true')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'dist/release-manifest.json').read_text())
    reference = json.loads(args.reference_manifest.read_text())
    assert manifest == reference, 'Repeated builds differ'
    version = manifest['version']
    plugin = ROOT / 'magazine-pi'
    assert json.loads((plugin / 'manifest.json').read_text())['version'] == version
    for name in ('INSTALL.md', 'SECURITY.md', 'ACCEPTANCE.md', 'UI-STYLING.md', 'MODELS-AND-JSONL.md', 'SIMPLIFICATION.md'):
        assert (plugin / 'docs' / name).read_bytes() == (ROOT / 'docs' / name).read_bytes(), name
    reuse = json.loads((plugin / 'reused-components.json').read_text())
    assert len(reuse['core']) == manifest['core_files_verified'] == 29
    assert manifest['core_files_unchanged'] == 29 - len(reuse.get('pi_core_changes', {}))
    for name, expected in reuse['core'].items():
        assert hashlib.sha256((plugin / 'magazine' / name).read_bytes()).hexdigest() == expected, name
    assert hashlib.sha256((plugin / 'beta5_helpers.py').read_bytes()).hexdigest() == reuse['helper_sha256']
    plugin_files = public_files(plugin) + [plugin / 'LICENSE']
    source_files = plugin_files + [ROOT / p for p in (
        'README.md', 'CHANGELOG.md', 'LICENSE', 'requirements-test.txt', 'requirements-browser-test.txt',
        'pytest.ini', '.gitignore', '.gitattributes', 'TODO.md', 'docs/images/overview-annotated.jpg')]
    for directory in ('docs', 'scripts', 'tests'):
        source_files.extend(p for p in public_files(ROOT / directory)
            if p.name not in ('HOST-CONFIGURATION-RESTORATION.md','PERSISTENT-AUTH-INVESTIGATION.md',
                              'launch_firefox_auth_check.py','live_auth_check.cjs'))
    inventories = {
        f'Magazine-Pi-{version}-plugin.zip': {
            'magazine-pi/' + p.relative_to(plugin).as_posix(): p for p in plugin_files},
        f'Magazine-Pi-{version}-source.zip': {
            f'Magazine-Pi-{version}/' + p.relative_to(ROOT).as_posix(): p for p in source_files},
    }
    assert {a['file'] for a in manifest['archives']} == set(inventories)
    results = []
    for artifact in manifest['archives']:
        path = ROOT / 'dist' / artifact['file']
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == artifact['sha256'], path
        expected = inventories[path.name]
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None, path
            assert len(archive.namelist()) == len(set(archive.namelist())) == artifact['files']
            assert set(archive.namelist()) == set(expected), path
            for name, source in expected.items():
                assert archive.read(name) == source.read_bytes(), name
        results.append({**artifact, 'intended_inventory': True, 'reproducible': True})
    sums = ''.join(a['sha256'] + '  ' + a['file'] + '\n' for a in manifest['archives'])
    assert (ROOT / 'dist/SHA256SUMS.txt').read_text() == sums
    runtime_unchanged = None
    if args.previous_plugin:
        current = ROOT / 'dist' / next(a['file'] for a in manifest['archives'] if a['file'].endswith('-plugin.zip'))
        with zipfile.ZipFile(args.previous_plugin) as old, zipfile.ZipFile(current) as new:
            runtime = [n for n in old.namelist() if n.endswith(('.py', '.mjs')) or n.endswith('/requirements.txt')]
            assert {n for n in new.namelist() if n.endswith(('.py', '.mjs')) or n.endswith('/requirements.txt')} == set(runtime)
            for n in runtime:
                if args.style_only_client and n == 'magazine-pi/client/entry.mjs':
                    import re
                    strip = lambda data: re.sub(rb'<style>.*?</style>', b'<style>STYLE_ONLY</style>', data, count=1, flags=re.S)
                    assert strip(old.read(n)) == strip(new.read(n)), 'Client markup/behavior changed'
                    assert old.read(n).count(b'<style>') == new.read(n).count(b'<style>') == 1
                else: assert old.read(n) == new.read(n), n
            runtime_unchanged = len(runtime) - int(args.style_only_client)
    # Allowlisted inventories exclude all personal deployment/credential material.
    for inventory in inventories.values():
        for name, source in inventory.items():
            assert not any(part in Path(name).parts for part in ('runtime', 'private', 'production-backups', 'evidence', 'reference', '.baseline-venv'))
            assert b'C:\\Codex' not in source.read_bytes() and b'C:\\AI_Workflows' not in source.read_bytes(), name
            assert (b'pi' + b'bonsai') not in source.read_bytes().lower(), name
    report = {'version': version, 'PASS': True, 'core_files_unchanged': manifest['core_files_unchanged'],
              'documentation_copies_agree': True, 'checksums_agree': True,
              'two_builds_identical': True, 'archives': results}
    report['runtime_files_byte_identical_to_previous_release'] = runtime_unchanged
    report['private_material_excluded'] = True
    report['client_markup_and_behavior_unchanged_style_only'] = args.style_only_client
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
