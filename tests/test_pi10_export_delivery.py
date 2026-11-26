"""pi.10.2: Export Finished Work reaches the owner (browser download by default, or a chosen folder)."""
import base64
import errno
import io
import json
import os
import zipfile

import pytest

import finished_export as fx
from test_pi9_reliability import make_env  # noqa: F401  (fixture)

POSIX = pytest.mark.skipif(os.name != 'posix', reason='requires Linux descriptor-relative filesystem operations')

MOUNTINFO = (
    "1 0 0:1 /Codex/Pi/workspace /workspace rw,noatime - 9p drvfs rw,aname=drvfs;path=C:\\;uid=0;gid=0\n"
    "2 0 0:2 /Unreal\\040projects /workspace/Unreal\\040Projects rw,noatime - 9p drvfs rw,aname=drvfs;path=E:\\;uid=0\n"
    "3 0 0:3 / /app/.pi-web rw - 9p drvfs rw,aname=drvfs;path=C:\\Codex\\Pi\\data;uid=0\n")


def test_windows_paths_from_mountinfo(tmp_path):
    mi = tmp_path / 'mountinfo'
    mi.write_text(MOUNTINFO)
    mounts = fx.windows_mounts(str(mi))
    assert fx.windows_path('/workspace/Magazine exports', mounts) == 'C:\\Codex\\Pi\\workspace\\Magazine exports'
    assert fx.windows_path('/workspace/Unreal Projects/Nebula', mounts) == 'E:\\Unreal projects\\Nebula'
    assert fx.windows_path('/opt/x', mounts) is None


def test_default_is_download_and_folder_rules(tmp_path):
    data, ws, state = tmp_path / 'data', tmp_path / 'ws', tmp_path / 'pi'
    ws.mkdir(); state.mkdir()
    assert fx.preferences(data) == {'mode': 'download', 'folder': None}
    with pytest.raises(ValueError):
        fx.set_preferences(data, 'folder', 'relative/path', [str(ws)], [str(state)])
    if os.name == 'posix':
        with pytest.raises(ValueError, match='inside Magazine or Pi state'):
            fx.set_preferences(data, 'folder', str(state / 'x'), [str(ws)], [str(state)])
        with pytest.raises(ValueError, match='outside the folders'):
            fx.set_preferences(data, 'folder', str(tmp_path / 'elsewhere'), [str(ws)], [str(state)])
        v = fx.set_preferences(data, 'folder', str(ws / 'Magazine exports'), [str(ws)], [str(state)])
        assert v['mode'] == 'folder' and fx.preferences(data)['folder'] == str(ws / 'Magazine exports')
    assert fx.set_preferences(data, 'download', None, [str(ws)], [str(state)]) == {'mode': 'download', 'folder': None}


@pytest.fixture(params=['native', 'fallback'])
def folder_publisher(request, monkeypatch):
    if request.param == 'fallback':
        def unsupported(*args):
            raise fx.h._PublicationError('No-replace rename is unsupported', 503, errno.EINVAL)
        monkeypatch.setattr(fx.h, '_library_publish', unsupported)
    return fx.h._storage_publish_snapshot


@POSIX
def test_folder_delivery_never_overwrites(tmp_path, folder_publisher):
    files = {'manifest.json': b'{"a":1}\n', '0001-x.txt': b'text'}
    args = {'allowed': [tmp_path], 'protected': []}
    folder = tmp_path / 'out'
    a = fx.deliver_to_folder(str(folder), 'Magazine-W-r1', files, **args)
    assert not a['reused'] and (folder / 'Magazine-W-r1' / '0001-x.txt').read_bytes() == b'text'
    assert not list(folder.glob('.magazine-export-*'))
    assert fx.deliver_to_folder(str(folder), 'Magazine-W-r1', files, **args)['reused'] is True
    changed = {**files, '0001-x.txt': b'new text'}
    b = fx.deliver_to_folder(str(folder), 'Magazine-W-r1', changed, **args)
    assert b['container_path'].endswith('Magazine-W-r1-2')
    assert (folder / 'Magazine-W-r1' / '0001-x.txt').read_bytes() == b'text'
    assert (folder / 'Magazine-W-r1-2' / '0001-x.txt').read_bytes() == b'new text'
    assert not list(folder.glob('.magazine-export-*'))


@POSIX
def test_folder_delivery_cleans_staging_when_destination_appears(tmp_path, monkeypatch, folder_publisher):
    folder = tmp_path / 'out'
    files = {'manifest.json': b'{}', 'result.txt': b'new export'}

    def publish(fd, stage, name, data, identity):
        if name == 'Export':
            existing = folder / name
            existing.mkdir()
            (existing / 'keep.txt').write_bytes(b'keep this')
        return folder_publisher(fd, stage, name, data, identity)

    monkeypatch.setattr(fx.h, '_storage_publish_snapshot', publish)
    result = fx.deliver_to_folder(str(folder), 'Export', files, allowed=[tmp_path], protected=[])
    assert result == {'container_path': str(folder / 'Export-2'), 'reused': False}
    assert (folder / 'Export' / 'keep.txt').read_bytes() == b'keep this'
    assert sorted(p.name for p in (folder / 'Export').iterdir()) == ['keep.txt']
    assert (folder / 'Export-2' / 'result.txt').read_bytes() == files['result.txt']
    assert not list(folder.glob('.magazine-export-*'))


@POSIX
def test_folder_delivery_cleans_staging_after_failed_publication(tmp_path, monkeypatch):
    folder = tmp_path / 'out'

    def fail(*args):
        raise OSError('publication interrupted')

    monkeypatch.setattr(fx.h, '_storage_publish_snapshot', fail)
    with pytest.raises(OSError, match='publication interrupted'):
        fx.deliver_to_folder(str(folder), 'Export', {'manifest.json': b'{}'},
                             allowed=[tmp_path], protected=[])
    assert not list(folder.iterdir())


def test_redacted_delivery_rebuilds_manifest_and_nested_results_zip():
    secret = 'SYNTHETIC-CREDENTIAL-EXAMPLE'
    filename = '0001-sample.txt'
    files = {filename: f'text\r\nBearer {secret}\r\n'.encode(), 'results.zip': secret.encode()}
    manifest = {'cartridges': [{'file': filename}]}
    delivery, cleaned_manifest = fx.redact_delivery(files, manifest, (secret,))
    assert secret.encode() in files[filename]
    assert secret.encode() not in delivery[filename]
    assert delivery[filename].startswith(b'text\r\n')
    with zipfile.ZipFile(io.BytesIO(delivery['results.zip'])) as inner:
        assert inner.read(filename) == delivery[filename]
        assert json.loads(inner.read('manifest.json')) == cleaned_manifest
    assert fx.zip_bytes(delivery) == fx.zip_bytes(delivery)


def test_delivery_size_cap():
    with pytest.raises(ValueError, match='16 MiB'):
        fx.zip_bytes({'file': b'x' * (fx.MAX_EXPORT_BYTES + 1)})


@pytest.mark.skipif(not os.environ.get("MAGAZINE_PI_SDK"), reason="needs MAGAZINE_PI_SDK (real SDK tree)")
def test_export_button_delivers_download_then_folder(make_env):
    from test_pi6_integration import prepare, run_to_end
    e = make_env()
    rid = prepare(e.rpc, "Write the answer.")
    assert run_to_end(e.rpc, rid)["status"] == "COMPLETED"
    r = e.rpc.call("export", {"run_id": rid})
    assert r["export"]["status"] == "EXPORTED" and r["delivery"]["mode"] == "download"
    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(r["delivery"]["file_data_b64"])))
    assert "manifest.json" in z.namelist() and r["delivery"]["file_name"].endswith(rid + ".zip")
    view = e.rpc.call("export_settings")
    assert view["mode"] == "download" and view["suggestion"].startswith(str(e.cwd))
    bad = e.rpc.call("set_export_settings", {"mode": "folder", "folder": str(e.data)}, ok=False)
    assert bad["code"] == 400
    e.rpc.call("set_export_settings", {"mode": "folder", "folder": view["suggestion"]})
    r = e.rpc.call("export", {"run_id": rid})
    d = r["delivery"]
    assert d["mode"] == "folder" and d["ok"] and "file_data_b64" not in d
    saved = os.path.join(d["container_path"], "manifest.json")
    assert json.loads(open(saved).read())["run_id"] == rid
    assert e.rpc.call("export", {"run_id": rid})["delivery"]["reused"] is True
