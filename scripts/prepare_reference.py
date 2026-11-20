"""Copy frozen core and extract platform-neutral beta.5 functions verbatim."""
import ast
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'reference/owui-beta5/Magazine-0.1.0-beta.5'
TARGET = ROOT / 'magazine-pi'
TARGET.mkdir(exist_ok=True)
for dirname in ('magazine',):
    shutil.copytree(SOURCE / dirname, TARGET / dirname, dirs_exist_ok=True)
shutil.copy2(SOURCE / 'LICENSE', TARGET / 'LICENSE')
shutil.copy2(SOURCE / 'LICENSE', ROOT / 'LICENSE')
shutil.copytree(SOURCE / 'tests', ROOT / 'tests/core', dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
shutil.copytree(SOURCE / 'examples/simple', ROOT / 'tests/examples/simple', dirs_exist_ok=True)
(TARGET/'examples/simple').mkdir(parents=True,exist_ok=True)
shutil.copy2(SOURCE/'examples/simple/magazine.yaml',TARGET/'examples/simple/magazine.yaml')
shutil.copytree(SOURCE/'examples/simple/cartridges',TARGET/'examples/simple/cartridges',dirs_exist_ok=True)
text = (SOURCE / 'integrations/open_webui/function/magazine_ef.py').read_text(encoding='utf-8')
tree = ast.parse(text)
lines = text.splitlines(keepends=True)
names = {
    '_document_logical_keys', '_validate_admission', '_normalize_cartridges',
    '_available_model_ids', '_available_tool_ids', '_extract_model_id', '_extract_tool_id',
    '_load_package_from_upload', '_LibraryError', '_initialize_library_root',
    '_library_root_fd', '_library_copy_tree', '_library_validation_error',
    '_library_preview_at', '_library_preview', '_library_list', '_library_zip_members',
    '_library_extract_zip', '_library_sync_directories', '_library_import_lock',
    '_library_publish', '_library_check_conflict', '_library_cleanup_stage',
    '_library_import', '_storage_root', '_storage_token', '_storage_fd',
    '_storage_write', '_retain_input_zip', '_export_record', '_save_export_record',
    '_canonical_result_bytes', '_export_files', '_storage_child', '_export_matches',
    '_storage_publish_snapshot', '_export_run', '_attempt_export',
    '_auto_export_completed', '_status_payload', '_load_state', '_results_payload',
    '_start_precheck', '_lost_runtime_interception', '_recover_run_decision',
    '_run_bg_dispatch', '_stop_run_decision', '_run_base', '_state_dir',
    '_state_path', '_lock_path', '_mag_path', '_meta_path', '_now', '_load_meta',
}
chunks = ['"""Verbatim beta.5 neutral helpers. Generated; see reused-components.json."""\n',
          'import os, sys, json, traceback, base64, hashlib, shutil, zipfile, asyncio, uuid\n',
          'import contextlib, errno, re, stat, tempfile, io, ctypes, time, unicodedata, zlib\n',
          'from pathlib import Path, PureWindowsPath\n']
records = []
for node in tree.body:
    take = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names
    if isinstance(node, ast.Assign):
        ids = [t.id for t in node.targets if isinstance(t, ast.Name)]
        take = any(i.startswith('_LIBRARY_') or i in {'_TASKS', '_RUN_HANDLES', '_STORAGE_LIBRARY_ROOT_GETTER'} for i in ids)
    if take:
        start = min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])
        chunk = ''.join(lines[start - 1:node.end_lineno])
        chunks.extend([chunk, '\n\n'])
        records.append({'name': node.name if hasattr(node, 'name') else ast.unparse(node.targets[0]),
                        'source_line': start, 'sha256': hashlib.sha256(chunk.encode()).hexdigest()})
(TARGET / 'beta5_helpers.py').write_text(''.join(chunks), encoding='utf-8')
core = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (TARGET/'magazine').glob('*.py')}
record={'baseline':'0.1.0-beta.5','core':core,'verbatim_functions':records,
        'helper_sha256':hashlib.sha256((TARGET/'beta5_helpers.py').read_bytes()).hexdigest()}
for destination in (ROOT / 'evidence/reused-components.json',TARGET/'reused-components.json'):
    destination.write_text(json.dumps(record,indent=2)+'\n')
print(f'Copied {len(core)} core files; extracted {len(records)} unchanged helpers')
