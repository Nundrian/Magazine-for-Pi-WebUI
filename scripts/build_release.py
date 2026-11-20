"""Allowlisted deterministic plugin/source archives; excludes private runtime data."""
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
plugin=ROOT/'magazine-pi'
VERSION=json.loads((plugin/'manifest.json').read_text())['version']
for name in ('INSTALL.md','SECURITY.md','ACCEPTANCE.md','UI-STYLING.md','MODELS-AND-JSONL.md','SIMPLIFICATION.md',
             'COMPATIBILITY.md','TESTING.md','PI6-REPORT.md','PI7-SCHEDULER.md',
             'UI8-OPERATOR-GUIDE.md','UI8-REPORT.md','UI8-ALIGNMENT.md','LINUX-STORAGE.md','IMPORT-REPAIR.md'):
    (plugin/'docs').mkdir(exist_ok=True)
    shutil.copy2(ROOT/'docs'/name,plugin/'docs'/name)
reuse=json.loads((plugin/'reused-components.json').read_text())
for name,expected in reuse['core'].items():
    assert hashlib.sha256((plugin/'magazine'/name).read_bytes()).hexdigest()==expected,name
assert hashlib.sha256((plugin/'beta5_helpers.py').read_bytes()).hexdigest()==reuse['helper_sha256']
assert len(reuse['core'])==29
allowed={'.py','.mjs','.json','.md','.txt','.yaml','.yml','.sh','.ini'}


def public_files(directory):
    for p in sorted(directory.rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix in allowed and p.name!='config.json':
            assert not p.is_symlink(),p
            yield p


def build(name,entries):
    target=ROOT/'dist'/name
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_STORED) as archive:
        for entry,p in sorted(entries):
            data=p.read_bytes()
            info=zipfile.ZipInfo(entry,(2026,11,20,0,0,0))
            info.external_attr=0o100644<<16
            archive.writestr(info,data)
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist())==len(entries)
        for entry,p in entries:assert archive.read(entry)==p.read_bytes()
    return {'file':name,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'files':len(entries),'crc':True,'source_parity':True}


(ROOT/'dist').mkdir(exist_ok=True)
plugin_files=list(public_files(plugin))+[plugin/'LICENSE']
plugin_entries=[('magazine-pi/'+str(p.relative_to(plugin)).replace('\\','/'),p) for p in plugin_files]
source_files=plugin_files+[ROOT/p for p in ('README.md','LICENSE','requirements-test.txt','requirements-browser-test.txt','pytest.ini','.gitignore')]
for directory in ('docs','scripts','tests'):
    source_files.extend(p for p in public_files(ROOT/directory)
        if p.name not in ('HOST-CONFIGURATION-RESTORATION.md','PERSISTENT-AUTH-INVESTIGATION.md',
                          'launch_firefox_auth_check.py','live_auth_check.cjs'))
# Host deployment/launcher examples and personal restoration procedures are not
# part of a normal plugin installation and are no longer distributed.
source_entries=[('Magazine-Pi-'+VERSION+'/'+str(p.relative_to(ROOT)).replace('\\','/'),p) for p in source_files]
compat=json.loads((plugin/'compatibility.json').read_text())
manifest={'version':VERSION,'baseline':'Magazine-0.1.0-beta.5','core_files_unchanged':29,
          'reference':'Nundrian/Magazine 09a8706 magazine_simple_reliability3.py + Magazine Simple 0.3.0-scheduler.rc1 (ZIP sha256 b2acaad4a27260b163b87d732fb38886c90623f72b249683efebab93cab81864)',
          'supported_pairs':[{k:p[k] for k in ('host','sdk','level')} for p in compat['pairs']],'archives':[
    build(f'Magazine-Pi-{VERSION}-plugin.zip',plugin_entries),
    build(f'Magazine-Pi-{VERSION}-source.zip',source_entries)]}
(ROOT/'dist/release-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
(ROOT/'dist/SHA256SUMS.txt').write_text(''.join(a['sha256']+'  '+a['file']+'\n' for a in manifest['archives']))
print(json.dumps(manifest,indent=2))
