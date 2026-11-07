#!/usr/bin/env python3
"""Pre-publish scan for internal information. Any hit returns exit code 1.

Usage:
  python tools/scan_public.py <dir> [<dir> ...] [--extra-patterns FILE]
--extra-patterns: one regex per line (internal ids, personal names, internal repo names, etc.); this file must
never be placed in a public repository.
"""
import argparse
import re
import sys
from pathlib import Path

# Only generic rules here; any organization-specific term goes through --extra-patterns
RULES = [
    ("windows-path", r"\b[A-Za-z]:[\\/](?:Work|Users|Temp|Espressif)\b"),
    ("user-home", r"[\\/]Users[\\/][A-Za-z0-9_.-]+[\\/]"),
    ("lan-ip", r"\b192\.168\.\d{1,3}\.\d{1,3}\b|\b10\.(?:\d{1,3}\.){2}\d{1,3}\b"),
    ("mac", r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"),
    ("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
]
EMAIL_OK = re.compile(r"@users\.noreply\.github\.com$|@example\.(?:com|org)$", re.I)
# STEP/WRL are text and their headers may carry an author and local paths, so they are not skipped
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".zip", ".pdf", ".bin", ".pyc"}
SKIP_DIRS = {".git", "__pycache__"}  # directories that are not committed


def _load_extra(path):
    if not path:
        return []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def scan(roots, extra):
    rules = [(n, re.compile(p)) for n, p in RULES] + [("private", re.compile(p)) for p in extra]
    hits = []
    for root in roots:
        for f in sorted(Path(root).rglob("*")):
            if not f.is_file() or SKIP_DIRS & set(f.parts) or f.suffix.lower() in BINARY:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = f.read_text(encoding="latin-1")
            for no, line in enumerate(text.splitlines(), 1):
                for name, rx in rules:
                    for m in rx.finditer(line):
                        if name == "email" and EMAIL_OK.search(m.group(0)):
                            continue
                        hits.append((f, no, name, m.group(0)))
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--extra-patterns")
    a = ap.parse_args(argv)
    hits = scan([Path(d) for d in a.dirs], _load_extra(a.extra_patterns))
    for f, no, name, s in hits:
        print(f"[FAIL] {f}:{no}: {name}: {s}")
    n = len(hits)
    print(f"Result: {f'{n} hit(s)' if n else 'clean'}")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
