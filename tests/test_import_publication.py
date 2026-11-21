"""Linux publication and migration faults; no provider or project access."""
import asyncio
import base64
import ctypes
import errno
import hashlib
import importlib.util
import io
import json
import multiprocessing
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import beta5_helpers as h
from service import Service
from pi6_fakes import project_zip


def archive(name='Publication test'):
    return project_zip('schema_version: "1.0"\nname: '+name+'\ncartridges:\n'
        '  - id: one\n    model: general\n    prompt_file: nested/one.md\n',
        {'nested/one.md': 'Read only fixture'})


@pytest.fixture
def library(tmp_path):
    svc = Service(tmp_path/'state', {'cwd': str(tmp_path), 'agentDir': str(tmp_path/'agent')})
    yield svc.root/'library'
    asyncio.run(svc.close())


def upload(library, name='Fixture', content=None):
    return h._library_import(str(library), name+'.zip', content or archive())


def inject_rename(monkeypatch, code):
    class Rename:
        def __call__(self, *args):
            ctypes.set_errno(code)
            return -1
    monkeypatch.setattr(h.ctypes, 'CDLL', lambda *a, **kw: SimpleNamespace(renameat2=Rename()))


@pytest.mark.parametrize('code,fragment,status', [
    (errno.EINVAL, 'unsupported', 503), (errno.ENOSYS, 'unsupported', 503),
    (errno.EOPNOTSUPP, 'unsupported', 503), (errno.EACCES, 'permissions', 503),
    (errno.EPERM, 'permissions', 503), (errno.EROFS, 'permissions', 503),
    (errno.EIO, 'I/O', 503), (errno.ENOSPC, 'I/O', 503),
    (errno.EEXIST, 'already exists', 409), (errno.ENOTEMPTY, 'already exists', 409)])
def test_exact_syscall_errors_fail_closed(library, monkeypatch, capsys, code, fragment, status):
    inject_rename(monkeypatch, code)
    with pytest.raises(h._PublicationError) as result:
        upload(library)
    assert result.value.errno == code and result.value.status_code == status
    assert fragment in str(result.value) and str(library) not in str(result.value)
    assert not list(library.iterdir())
    log = capsys.readouterr().err
    assert errno.errorcode[code] in log and str(library) in log


def test_missing_syscall_is_explicit(library, monkeypatch):
    monkeypatch.setattr(h.ctypes, 'CDLL', lambda *a, **kw: SimpleNamespace())
    ctypes.set_errno(errno.EIO)  # stale errno must never affect export policy
    with pytest.raises(h._PublicationError) as result:
        upload(library)
    assert result.value.errno == errno.ENOSYS and result.value.unsupported
    assert not list(library.iterdir())


def test_linux_success_nested_prompts_retention_and_reopen(library):
    result = upload(library)
    assert result['imported'] and result['cartridge_count'] == 1
    assert (library/'Fixture/nested/one.md').read_text() == 'Read only fixture'
    assert h._library_list(str(library))['workflows'][0]['valid']
    assert h._library_preview(str(library), 'Fixture')['order'] == ['one']
    assert not list(library.glob('.import-*'))
    assert len(list((library.parent/'input-zips').glob('*.zip'))) == 1


@pytest.mark.parametrize('existing', ['empty', 'content', 'case', 'symlink'])
def test_existing_target_preserved_including_empty(library, existing):
    target = library/('fIXTURE' if existing == 'case' else 'Fixture')
    if existing == 'symlink':
        outside = library.parent/'outside'; outside.mkdir()
        target.symlink_to(outside)
    else:
        target.mkdir()
        if existing == 'content': (target/'keep').write_text('untouched')
    before = target.lstat()
    with pytest.raises(h._LibraryError) as result: upload(library)
    assert result.value.status_code == 409
    assert target.lstat().st_ino == before.st_ino
    if existing == 'content': assert (target/'keep').read_text() == 'untouched'
    if existing == 'symlink': assert not list(outside.iterdir())
    assert not list(library.glob('.import-*'))


def test_duplicate_workflow_name_and_repeated_attempts(library):
    upload(library)
    before = (library/'Fixture/magazine.yaml').read_bytes()
    for name in ('Fixture', 'Other', 'fixture'):
        with pytest.raises(h._LibraryError) as error: upload(library, name)
        assert error.value.status_code == 409
    assert (library/'Fixture/magazine.yaml').read_bytes() == before


def test_syscall_itself_never_replaces_empty_directory(library):
    (library/'stage').mkdir(); (library/'target').mkdir()
    before = (library/'target').stat().st_ino
    with h._library_root_fd(str(library)) as fd:
        with pytest.raises(h._PublicationError) as error: h._library_publish(fd, 'stage', 'target')
    assert error.value.status_code == 409
    assert (library/'target').stat().st_ino == before and (library/'stage').is_dir()


def test_concurrent_import_processes_serialize(library):
    context = multiprocessing.get_context('fork')
    queue = context.Queue()
    def worker():
        try: queue.put(('ok', upload(library)['project_id']))
        except h._LibraryError as error: queue.put(('error', error.status_code))
    processes = [context.Process(target=worker) for _ in range(2)]
    for p in processes: p.start()
    values = [queue.get(timeout=15) for _ in processes]
    for p in processes: p.join(15); assert p.exitcode == 0
    assert sorted(values) == [('error', 409), ('ok', 'Fixture')]
    assert len(h._library_list(str(library))['workflows']) == 1


@pytest.mark.parametrize('phase', ['file', 'directories', 'publish'])
def test_sync_and_publication_failures_clean_staging(library, monkeypatch, phase):
    if phase == 'file':
        original = os.fsync
        def fail(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode): raise OSError(errno.EIO, 'injected')
            return original(fd)
        monkeypatch.setattr(os, 'fsync', fail)
    elif phase == 'directories':
        monkeypatch.setattr(h, '_library_sync_directories', lambda fd: (_ for _ in ()).throw(OSError(errno.EIO, 'injected')))
    else: inject_rename(monkeypatch, errno.EIO)
    with pytest.raises(h._LibraryError): upload(library)
    assert not list(library.iterdir())


def test_root_sync_failure_is_not_reported_as_success(library, monkeypatch):
    original = os.fsync; identity = library.stat().st_ino
    def fail(fd):
        if os.fstat(fd).st_ino == identity: raise OSError(errno.EIO, 'injected')
        return original(fd)
    monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(h._LibraryError, match='was published'): upload(library)
    assert h._library_preview(str(library), 'Fixture')['valid']
    assert not list(library.glob('.import-*'))
    monkeypatch.setattr(os, 'fsync', original)
    with pytest.raises(h._LibraryError) as error: upload(library)
    assert error.value.status_code == 409


@pytest.mark.parametrize('phase', ['before', 'after'])
def test_process_interruption_never_exposes_partial_workflow(library, phase):
    script = '''import os,sys
import beta5_helpers as h
import service
h._runs_root=lambda:str(__import__('pathlib').Path(sys.argv[1]).parent/'runs')
original=h._library_publish
def interrupt(*args):
    if sys.argv[2]=='after': original(*args)
    os._exit(77)
h._library_publish=interrupt
h._library_import(sys.argv[1],'Fixture.zip',sys.argv[3])
'''
    out = subprocess.run([sys.executable, '-c', script, str(library), phase, archive()],
                         env={**os.environ, 'PYTHONPATH': str(Path(h.__file__).parent)})
    assert out.returncode == 77
    workflows = h._library_list(str(library))['workflows']
    assert len(workflows) == (1 if phase == 'after' else 0)
    if workflows: assert workflows[0]['valid']
    else:
        assert len(list(library.glob('.import-*'))) == 1
        assert upload(library)['imported']  # stale private stage is never a project


def test_cleanup_identity_substitution_is_refused(library):
    stage = library/'.import-test'; stage.mkdir()
    identity = (stage.stat().st_dev, stage.stat().st_ino)
    stage.rename(library/'.retained'); stage.mkdir(); (stage/'keep').write_text('owner data')
    with h._library_root_fd(str(library)) as fd:
        with pytest.raises(h._LibraryError, match='identity'): h._library_cleanup_stage(fd, stage.name, identity)
    assert (stage/'keep').read_text() == 'owner data'


def test_cleanup_failure_is_not_hidden(library, monkeypatch):
    inject_rename(monkeypatch, errno.EIO)
    monkeypatch.setattr(h, '_library_cleanup_stage', lambda *a: (_ for _ in ()).throw(OSError(errno.EIO, 'injected')))
    with pytest.raises(h._LibraryError, match='cleanup could not be confirmed') as error: upload(library)
    assert error.value.status_code == 500
    assert not h._library_list(str(library))['workflows']


@pytest.mark.parametrize('kind', ['duplicate_id', 'oversize', 'case_collision', 'bad_yaml'])
def test_zip_schema_limits_and_collisions(library, kind):
    if kind == 'duplicate_id':
        raw=project_zip('schema_version: "1.0"\nname: Duplicate\ncartridges:\n'
            '  - {id: a, model: general, prompt_file: a.md}\n'
            '  - {id: a, model: general, prompt_file: a.md}\n', {'a.md':'fixture'})
    elif kind == 'bad_yaml': raw=project_zip('schema_version: [broken', {})
    elif kind == 'oversize': raw=project_zip('schema_version: "1.0"', {'large': 'x'*(h._LIBRARY_MAX_FILE_BYTES+1)})
    else: raw=project_zip('schema_version: "1.0"', {'a.md':'one','A.md':'two'})
    with pytest.raises(h._LibraryError): upload(library, content=raw)
    assert not list(library.iterdir())


@pytest.mark.parametrize('path,mode', [('../escape', 0), ('/absolute', 0), ('C:/drive', 0),
    ('nested\\escape', 0), ('link', stat.S_IFLNK | 0o777), ('fifo', stat.S_IFIFO | 0o600)])
def test_unsafe_archive_entries_remain_rejected(library, path, mode):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        info = zipfile.ZipInfo(path); info.create_system = 3; info.external_attr = mode << 16
        z.writestr(info, 'unsafe')
    with pytest.raises(h._LibraryError): upload(library, content=base64.b64encode(buffer.getvalue()).decode())
    assert not list(library.iterdir())


@pytest.mark.parametrize('code', [errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP])
def test_export_fallback_retains_no_replace_completion_order(library, monkeypatch, code):
    inject_rename(monkeypatch, code)
    stage = library/'.export-test'; stage.mkdir()
    files = {'result.txt': b'result', 'manifest.json': b'{}'}
    for name, data in files.items(): (stage/name).write_bytes(data)
    info = stage.stat(); calls = []; original = os.link
    def link(src, dst, **kw): calls.append(dst); return original(src, dst, **kw)
    monkeypatch.setattr(os, 'link', link)
    with h._library_root_fd(str(library)) as fd:
        h._storage_publish_snapshot(fd, stage.name, 'Export', files, (info.st_dev, info.st_ino))
    assert calls == ['result.txt', 'manifest.json']
    assert not stage.exists() and (library/'Export/result.txt').read_bytes() == b'result'


def test_export_never_falls_back_for_stale_errno(library, monkeypatch):
    def fail(*args):
        ctypes.set_errno(errno.EINVAL)
        raise h._LibraryError('unrelated failure', 503)
    monkeypatch.setattr(h, '_library_publish', fail)
    with h._library_root_fd(str(library)) as fd:
        with pytest.raises(h._LibraryError): h._storage_publish_snapshot(fd, '.stage', 'Export', {}, (0, 0))
    assert not (library/'Export').exists()


def migration_module():
    path = Path(__file__).parents[1]/'scripts/migrate_storage.py'
    spec = importlib.util.spec_from_file_location('migration', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_migration_preserves_complete_state_and_refuses_overwrite(tmp_path):
    module = migration_module()
    source = tmp_path/'old'; source.mkdir(); destination = tmp_path/'new'; destination.mkdir()
    (source/'library/Fixture/nested').mkdir(parents=True)
    (source/'library/Fixture/nested/a').write_bytes(b'existing workflow')
    (source/'sessions').mkdir(); (source/'sessions/keep.jsonl').write_bytes(b'existing session\n')
    (source/'ledger.json').write_text('{}'); os.chmod(source/'ledger.json', 0o600)
    before = module.inventory(source)
    assert module.migrate(source, destination)['verified']
    assert module.inventory(source) == before == module.inventory(destination)
    assert module.migrate(source, destination, True)['verified']
    with pytest.raises(ValueError, match='empty'): module.migrate(source, destination)
    (destination/'ledger.json').write_text('tampered')
    with pytest.raises(ValueError, match='verification'): module.migrate(source, destination, True)


@pytest.mark.parametrize('kind', ['run', 'queue', 'symlink', 'sync'])
def test_migration_failure_keeps_original_unselected(tmp_path, monkeypatch, kind):
    module = migration_module()
    source = tmp_path/'old'; source.mkdir(); destination = tmp_path/'new'; destination.mkdir()
    (source/'keep').write_text('existing state')
    if kind == 'run':
        p=source/'runs/local/run/run'; p.mkdir(parents=True); (p/'state.json').write_text('{"status":"RUNNING"}')
    if kind == 'queue': (source/'scheduler-v1.json').write_text('{"queue":["run"]}')
    if kind == 'symlink': (source/'link').symlink_to(source/'keep')
    if kind == 'sync': monkeypatch.setattr(os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError(errno.EIO, 'injected')))
    with pytest.raises((ValueError, OSError)): module.migrate(source, destination)
    assert (source/'keep').read_text() == 'existing state'
