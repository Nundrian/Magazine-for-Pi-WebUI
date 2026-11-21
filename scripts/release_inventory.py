"""Archive inventory plus a broader secret/private-data scan. Never prints matches.

Usage: python scripts/release_inventory.py --archive A.zip [--archive B.zip] --out report.json
Complements scan_public_release.py (known private values + key/token patterns).
"""
import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

# Literals are split so this file does not match itself.
PATTERNS = {
    "private_key": rb"-----BEGIN [A-Z ]*" + rb"PRIVATE KEY-----",
    "aws_access_key": rb"\b(?:AKIA|ASIA)" + rb"[0-9A-Z]{16}\b",
    "jwt": rb"\beyJ[A-Za-z0-9_-]{10,}\." + rb"eyJ[A-Za-z0-9_-]{10,}\.",
    "openai_like_key": rb"\bsk-" + rb"(?:ant-|proj-)?[A-Za-z0-9_-]{24,}",
    "github_token": rb"\bgh[pousr]_" + rb"[A-Za-z0-9]{30,}",
    "slack_token": rb"\bxox[abprs]-" + rb"[A-Za-z0-9-]{10,}",
    "bearer_literal": rb"Bearer [A-Za-z0-9._~+/=-]{" + rb"32,}",
    "windows_user_path": rb"(?i)[A-Z]:\\\\?Users\\\\?" + rb"[A-Za-z]",
    "owner_machine_paths": rb"(?i)(C:\\\\?Dock" + rb"er|AI_" + rb"Workflows|Pi" + rb"Bonsai|Pi Magazine work" + rb" folder|spark" + rb"le\b)",
    "email": rb"\b[A-Za-z0-9._%+-]+@(?!example\.|anthropic\.com|users\.noreply)[A-Za-z0-9.-]+\." + rb"[a-z]{2,}\b",
}
FORBIDDEN_NAMES = {".env", "config.json", "auth.json", "models.json", "models-store.json", "provider-keys.json",
                   "import-key", "view-state.json", "ledger.json", "state.json", "execution.json"}
FORBIDDEN_PARTS = {"evidence", "runtime", "private", "production-backups", "__pycache__", ".git", "node_modules",
                   ".baseline-venv", "venv", ".pytest_cache", "sessions", "runs", "reference"}
FORBIDDEN_SUFFIXES = {".jsonl", ".db", ".sqlite", ".pyc", ".log", ".png", ".har", ".key", ".pem"}
# Reviewed, intentional mentions (documentation of the owner's environment is NOT shipped).
ALLOW = {("scripts/verify_release.py", "owner_machine_paths")}  # asserts these paths are ABSENT


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--archive", action="append", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()
    report = {"archives": [], "findings": [], "PASS": True}
    for archive_path in args.archive:
        entries = []
        with zipfile.ZipFile(archive_path) as z:
            assert z.testzip() is None
            for info in z.infolist():
                data = z.read(info)
                name = info.filename
                parts = set(Path(name).parts)
                short = name.split("/", 1)[1] if "/" in name else name
                problems = []
                if Path(name).name in FORBIDDEN_NAMES:
                    problems.append("forbidden_name")
                if parts & FORBIDDEN_PARTS:
                    problems.append("forbidden_directory")
                if Path(name).suffix in FORBIDDEN_SUFFIXES:
                    problems.append("forbidden_type")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    problems.append("symlink")
                for label, pattern in PATTERNS.items():
                    if re.search(pattern, data) and (short, label) not in ALLOW:
                        problems.append(label)
                for problem in problems:
                    report["findings"].append({"archive": archive_path.name, "file": name, "issue": problem})
                entries.append({"name": name, "bytes": info.file_size, "sha256": hashlib.sha256(data).hexdigest()})
        report["archives"].append({"file": archive_path.name, "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                                   "members": len(entries), "bytes": sum(e["bytes"] for e in entries), "inventory": entries})
    report["PASS"] = not report["findings"]
    report["patterns"] = sorted(PATTERNS)
    report["limitations"] = "Pattern-based; cannot recognise every secret format. Matched values are never printed."
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"PASS": report["PASS"], "findings": report["findings"],
                      "archives": [{k: a[k] for k in ("file", "members", "sha256")} for a in report["archives"]]}, indent=1))


if __name__ == "__main__":
    main()
