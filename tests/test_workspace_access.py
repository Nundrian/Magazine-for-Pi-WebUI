"""Focused simplification regressions, retaining the original run engine."""
import hashlib
import json
import shutil
from pathlib import Path

from test_pi_integration import setup, prepare, RPC, run_jsonl


def digest_tree(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_legacy_partitions_read_in_place_after_restart(setup):
    rpc, key, provider, cwd = setup
    assert 'administrator' not in rpc.call('library')['projects']['message'].lower()
    rid = prepare(rpc, key, 'FIRST', 'SECOND')
    rpc.close()
    legacy = hashlib.sha256(b'old-browser-owner').hexdigest()
    source = rpc.root/'runs/local'/rid
    target = rpc.root/'runs'/legacy/rid
    target.parent.mkdir()
    shutil.move(source, target)
    meta = json.loads((target/'meta.json').read_text())
    meta['owner_id'] = legacy
    (target/'meta.json').write_text(json.dumps(meta))
    (rpc.root/'owners').mkdir()
    (rpc.root/'owners'/legacy).write_text('owner-v1\n')
    (rpc.root/'import-key').write_text('RETIRED_IMPORT_SECRET')
    before = digest_tree(target)
    new = RPC(rpc.root, rpc.config)
    try:
        assert new.call('runs')['runs'][0]['run_id'] == rid
        assert new.call('status', {'run_id': rid})['status'] == 'READY'
        assert new.call('results', {'run_id': rid})['done'] == []
        assert run_jsonl(new, '', rid)
        assert digest_tree(target) == before
        assert (rpc.root/'import-key').read_text() == 'RETIRED_IMPORT_SECRET'
        assert not provider.requests
        fresh = prepare(new, '', 'FIRST RETIRED_IMPORT_SECRET')
        assert (rpc.root/'runs/local'/fresh).is_dir()
        assert 'RETIRED_IMPORT_SECRET' not in json.dumps(run_jsonl(new, '', fresh))
    finally:
        new.close()


def test_ambiguous_legacy_id_refused_without_modifying_records(setup):
    rpc, key, provider, cwd = setup
    rid = prepare(rpc, key, 'FIRST')
    legacy = 'a'*64
    target = rpc.root/'runs'/legacy/rid
    shutil.copytree(rpc.root/'runs/local'/rid, target)
    meta = json.loads((target/'meta.json').read_text()); meta['owner_id'] = legacy
    (target/'meta.json').write_text(json.dumps(meta))
    before = digest_tree(rpc.root/'runs')
    for action in ('runs', 'status', 'download_jsonl'):
        assert rpc.call(action, {'run_id': rid}, ok=False)['code'] == 409
    assert digest_tree(rpc.root/'runs') == before


def test_namespace_and_body_identity_cannot_escape_workspace(setup, tmp_path):
    rpc, key, provider, cwd = setup
    rid = prepare(rpc, key, 'FIRST')
    outside = tmp_path/'unrelated-user'
    shutil.copytree(rpc.root/'runs/local', outside)
    (rpc.root/'runs'/('b'*64)).symlink_to(outside, target_is_directory=True)
    assert len(rpc.call('runs')['runs']) == 1
    data = {'run_id': '../'+rid, 'owner_id': 'b'*64, 'user_id': 'unrelated-user'}
    assert rpc.call('status', data, ok=False)['code'] == 404
    assert not provider.requests


def test_no_visible_magazine_credentials_or_registration():
    root = Path(__file__).resolve().parents[1]/'magazine-pi'
    client = (root/'client/entry.mjs').read_text()
    for forbidden in ('Your owner key', 'use-owner', 'show-owner', 'import-key',
                      'Administrator', 'X-Magazine-Owner', "call('register'", 'type="password"'):
        assert forbidden not in client
    assert 'Download Run JSONL' in client
    server = (root/'index.mjs').read_text()
    assert 'x-magazine-owner' not in server and 'x-magazine-import-key' not in server
    assert "local.workspace || path.join(host.dataDir,'magazine','workspace')" in server
    assert 'allowedTools:local.allowedTools || []' in server
