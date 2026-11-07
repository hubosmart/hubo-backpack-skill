"""hubo-kicad environment self-check: list all problems at once with fix commands.

Usage: <KiCad>/bin/python doctor.py
  (Must run with KiCad's bundled python - pcbnew is only there; running with the system python reports it directly)

Checks: pcbnew and KiCad version, kicad-cli, whether library tables are initialized, shapely, the kicad MCP in Claude Code,
and optional Java + Freerouting (only needed for autorouting).
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

OK, WARN, FAIL = "[OK]  ", "[WARN]", "[FAIL]"
problems = 0


def report(status, msg, fix=""):
    global problems
    print(f"  {status} {msg}")
    if fix:
        print(f"         → {fix}")
    problems += status == FAIL


def jar_java_version(jar):
    """Convert the class-file major version in the jar to a Java version (e.g. 65 -> 21, 69 -> 25).
    Different Freerouting versions need different Java (2.0.x: 21, 2.4.x: 25); it cannot be hard-coded."""
    import zipfile
    with zipfile.ZipFile(jar) as z:
        name = next((n for n in z.namelist() if n.endswith(".class")), None)
        if not name:
            return None
        head = z.read(name)[:8]
    return int.from_bytes(head[6:8], "big") - 44


def java_version(java):
    out = subprocess.run([java, "-version"], capture_output=True, text=True, errors="replace").stderr
    tok = next((t.strip('"') for t in out.split() if t.startswith('"')), "0")
    head = tok.split(".")[1] if tok.startswith("1.") else tok.split(".")[0].split("-")[0]
    return int(head) if head.isdigit() else 0


def main():
    print("== KiCad")
    try:
        import pcbnew
        ver = pcbnew.GetBuildVersion()
        major = int(ver.split(".")[0])
        report(OK if major >= 10 else WARN, f"pcbnew {ver} ({sys.executable})",
               "" if major >= 10 else "KiCad 10 recommended: import_pcb and other features need 10")
    except ImportError:
        report(FAIL, f"Current python cannot import pcbnew: {sys.executable}",
               "Run with KiCad's bundled python, e.g. \"<KiCad install dir>/bin/python.exe\" doctor.py")
        major = 0
    try:
        from _kicad import kicad_cli
        k = kicad_cli()
        v = subprocess.run([k, "--version"], capture_output=True, text=True).stdout.strip()
        report(OK, f"kicad-cli {v} ({k})")
    except SystemExit as e:
        report(FAIL, str(e), "Set the KICAD_CLI environment variable to point at kicad-cli")
    if major:
        base = pathlib.Path(os.environ.get("APPDATA", pathlib.Path.home() / ".config")) / "kicad" / f"{major}.0"
        if (base / "fp-lib-table").exists() and (base / "sym-lib-table").exists():
            report(OK, f"Global library tables initialized ({base})")
        else:
            report(FAIL, "Global symbol/footprint library tables not initialized: ERC will report many \"footprint library not found\" errors and search_symbols returns nothing",
                   "After installing, open KiCad once by hand and complete the library table initialization when prompted")

    print("== Python dependencies (installed in KiCad's bundled python)")
    try:
        import shapely
        report(OK, f"shapely {shapely.__version__}")
    except ImportError:
        report(FAIL, "shapely missing (needed by plane_check / route_prep)", f"\"{sys.executable}\" -m pip install shapely")

    print("== kicad MCP in Claude Code")
    claude = shutil.which("claude")
    if not claude:
        report(WARN, "No claude command on PATH, skipping the MCP check")
    else:
        r = subprocess.run([claude, "mcp", "get", "kicad"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0 and "Connected" in r.stdout:
            report(OK, "kicad MCP connected")
        elif r.returncode == 0:
            report(WARN, "kicad MCP registered but not shown as Connected", "Check the KICAD_PYTHON / KICAD_CLI environment variables, see references/01")
        else:
            report(FAIL, "kicad MCP not registered", "Register it with claude mcp add kicad ... as in references/01")

    print("== Autorouting (optional)")
    jar = os.environ.get("FREEROUTING_JAR")
    need = jar_java_version(jar) if jar and pathlib.Path(jar).exists() else None
    if need:
        report(OK, f"Freerouting: {jar} (compiled for Java {need})")
    else:
        report(WARN, f"Freerouting: {jar or 'FREEROUTING_JAR not set'}",
               "Download freerouting-x.y.z.jar (verify sha256) and set the FREEROUTING_JAR environment variable")
    java = os.environ.get("FREEROUTING_JAVA") or shutil.which("java")
    have = java_version(java) if java else 0
    if not java:
        report(WARN, "Java: not found", "Install a portable JRE and set FREEROUTING_JAVA to its java")
    elif need and have < need:
        report(WARN, f"Java {have} ({java}) is older than the {need} Freerouting requires; running will raise UnsupportedClassVersionError",
               f"Install a portable JRE of Java {need} or newer and set FREEROUTING_JAVA to its java")
    else:
        report(OK, f"Java {have} ({java})")

    print(f"\nResult: {str(problems) + ' required item(s) must be fixed' if problems else 'all required items pass'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
