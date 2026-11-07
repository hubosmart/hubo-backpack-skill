#!/usr/bin/env python3
"""Keep templates/backpack identical to the hubo-backpack-template repository.

  python tools/check_template_sync.py                           # verify the copy against TEMPLATE.lock
  python tools/check_template_sync.py --write <template repo>   # copy kicad/ and LICENSE from the template repo and rewrite the lock
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPY = ROOT / "templates" / "backpack"
LOCK = ROOT / "templates" / "TEMPLATE.lock"
INCLUDE = ("kicad", "LICENSE")
IGNORE = ("*-backups", "*.kicad_prl", "fp-info-cache")  # generated locally when KiCad opens the project, not part of the template


def _ignored(rel: Path) -> bool:
    return any(part.endswith("-backups") for part in rel.parts) or rel.suffix == ".kicad_prl" or rel.name == "fp-info-cache"


def _hash(p: Path) -> str:
    data = p.read_bytes()
    if b"\0" not in data:  # hash text with LF endings: git autocrlf on Windows converts to CRLF
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def digest(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): _hash(p)
            for p in sorted(root.rglob("*")) if p.is_file() and not _ignored(p.relative_to(root))}


def sync(src: Path, dst: Path, lock: Path, commit: str):
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for name in INCLUDE:
        s = src / name
        if s.is_dir():
            shutil.copytree(s, dst / name, ignore=shutil.ignore_patterns(*IGNORE))
        else:
            shutil.copy2(s, dst / name)
    lock.write_text(json.dumps({"source": "hubosmart/hubo-backpack-template", "commit": commit,
                                "files": digest(dst)}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def verify(dst: Path, lock: Path) -> list:
    want = json.loads(lock.read_text(encoding="utf-8"))["files"]
    have = digest(dst)
    probs = [f"missing: {k}" for k in sorted(want.keys() - have.keys())]
    probs += [f"extra: {k}" for k in sorted(have.keys() - want.keys())]
    probs += [f"changed: {k}" for k in sorted(want.keys() & have.keys()) if want[k] != have[k]]
    return probs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", metavar="TEMPLATE_REPO")
    a = ap.parse_args(argv)
    if a.write:
        src = Path(a.write)
        commit = subprocess.run(["git", "-C", str(src), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True).stdout.strip() or "uncommitted"
        sync(src, COPY, LOCK, commit)
        print(f"[OK] synced from template @ {commit}")
        return 0
    probs = verify(COPY, LOCK)
    for p in probs:
        print(f"[FAIL] {p}")
    print("Result: " + ("copy differs" if probs else "copy matches"))
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
