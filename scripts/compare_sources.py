"""File-level comparison: pi.6 plugin vs released pi.5 plugin ZIP (by SHA-256).

Usage: python scripts/compare_sources.py --pi5-zip <Magazine-Pi-0.1.0-pi.5-rc.1-plugin.zip> --out <json>
"""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pi5-zip", required=True, type=Path)
    p.add_argument("--plugin", default=ROOT / "magazine-pi", type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()
    old = {}
    with zipfile.ZipFile(args.pi5_zip) as z:
        for name in z.namelist():
            if not name.endswith("/"):
                old[name.split("/", 1)[1]] = hashlib.sha256(z.read(name)).hexdigest()
    new = {p.relative_to(args.plugin).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted(args.plugin.rglob("*")) if p.is_file() and "__pycache__" not in p.parts and p.name != "config.json"}
    rows = []
    for name in sorted(set(old) | set(new)):
        status = ("unchanged" if old.get(name) == new.get(name) else "modified") if name in old and name in new \
            else ("new" if name in new else "removed")
        rows.append({"file": name, "status": status, "pi5_sha256": old.get(name), "pi6_sha256": new.get(name)})
    summary = {s: sum(r["status"] == s for r in rows) for s in ("unchanged", "modified", "new", "removed")}
    args.out.write_text(json.dumps({"pi5_zip_sha256": hashlib.sha256(args.pi5_zip.read_bytes()).hexdigest(),
                                    "summary": summary, "files": rows}, indent=2) + "\n")
    print(json.dumps(summary))
    for r in rows:
        if r["status"] != "unchanged":
            print(r["status"], r["file"])


if __name__ == "__main__":
    main()
