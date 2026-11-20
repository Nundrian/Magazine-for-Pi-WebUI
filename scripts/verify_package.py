"""Validate archive parity and native CLI installation in an isolated Pi image."""
import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'dist/release-manifest.json').read_text())
base=ROOT/'evidence/package-validation';base.mkdir(exist_ok=True)
archive=ROOT/'dist'/next(a['file'] for a in manifest['archives'] if a['file'].endswith('-plugin.zip'))
expected=next(a['sha256'] for a in manifest['archives'] if a['file']==archive.name)
assert hashlib.sha256(archive.read_bytes()).hexdigest()==expected
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    z.extractall(base/'extracted')
env={**os.environ,'PI_CODING_AGENT_DIR':str(base/'agent'),'PI_WEB_PLUGIN_CATALOG_URL':'off','PI_OFFLINE':'1'}
subprocess.run(['pi-web-ui','install',str(base/'extracted/magazine-pi'),'--data-dir',str(base/'installed')],env=env,check=True)
installed=base/'installed/plugins/magazine-pi'
with zipfile.ZipFile(archive) as z:
    for member in z.namelist():
        path=installed/Path(member).relative_to('magazine-pi')
        assert path.read_bytes()==z.read(member),member
report={'archive_sha256':expected,'crc':True,'native_cli_install':True,'installed_source_parity':True}
(ROOT/'evidence/package-verification.json').write_text(json.dumps(report,indent=2)+'\n')
print('PACKAGE_CLI_INSTALL_PASS',flush=True)
