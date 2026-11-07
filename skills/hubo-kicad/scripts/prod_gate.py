"""Production gate: mandatory checks before exporting production files. export_prod.py calls it automatically before every export; it can also run on its own.

Checks in order; any failing item refuses the export:
  1 Project config <board name>.hz_layout.json exists and is valid (power_rules.load_cfg)
  2 <board name>.kicad_dru contains the base rule template (assets/jlc_base.kicad_dru) and the power track-width rules match the config
  2b Net class clearance >= 0.127 mm (the base rules cover only the Default net class; other net classes are judged by their own clearance and must not be below the 10.2 recommendation;
     if lower is really needed, state the net class and reason in the config netclass_exceptions and list it in the design document)
  3 KiCad DRC (zones refilled, including schematic parity): errors 0, unconnected 0, parity issues 0; warnings are listed but do not block
  4 route_check.py: right/acute angles, via-in-pad
  5 hs_review / impedance_check / plane_check / fence_check from the config checks
To waive when truly necessary add --allow "reason": **explicit user consent must be obtained first**, and the reason must say who agreed and why; failed items and the reason are written to
<out>/gate_waiver.txt and archived with the production files. The agent must not decide to waive on its own.
Check details are written to <out>/check_report.txt on every run.

Why it sits at export: the process requirements in the skill rely on the agent to follow them and may be skipped; production files are the only exit,
so blocking here means a non-conforming board cannot be exported no matter whether earlier checks were run.

Usage (KiCad's bundled python):
  python prod_gate.py <board.kicad_pcb> [--out output] [--allow "reason"]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import power_rules  # noqa: E402

# kicad-cli follows the UI language: with a zh-CN locale it writes "规则" instead of "rule" in violation text.
# The Chinese word is a compatibility input, not output.
DRC_RULE_WORD = r"(?:rule|规则)"
# legacy markers, kept for compatibility: output of older check scripts must still count as failing
LEGACY_NOT_MET = "不满足"


def summarize_drc(report):
    """kicad-cli DRC JSON -> (blocking items list, warning count) (pure function).

    Items reported by our own rules (rule name starting with HZ_) always block: KiCad assigns severity by violation type, and hole spacing (hole_to_hole) etc.
    is only a warning by default, but these are JLCPCB process requirements (SPEC-005 chapter 10) and must not be let through because of a low severity.
    """
    hz = lambda v: re.search(DRC_RULE_WORD + r" 'HZ_", v["description"]) is not None  # noqa: E731
    errs = [v for v in report.get("violations", []) if v.get("severity") == "error" or hz(v)]
    warns = [v for v in report.get("violations", []) if v.get("severity") == "warning" and not hz(v)]
    block = []
    by = {}
    for v in errs:
        # Same track_width: the base minimum and power track-width rules must be listed separately, otherwise a non-conforming power trace would be drowned out
        m = re.search(DRC_RULE_WORD + r" '([^']+)'", v["description"])
        by.setdefault((v["type"], m.group(1) if m else ""), []).append(v["description"])
    for (t, rule), ds in sorted(by.items()):
        block.append(f"DRC error {t}{f' ({rule})' if rule else ''} x{len(ds)}: {ds[0]}")
    n_un = len(report.get("unconnected_items", []))
    if n_un:
        block.append(f"DRC unconnected x{n_un}")
    n_par = len(report.get("schematic_parity", []))
    if n_par:
        block.append(f"Schematic and PCB mismatch x{n_par}")
    return block, len(warns)


NETCLASS_MIN = 0.127


def netclass_floor(classes, exceptions):
    """[(net class name, clearance mm)] -> blocking items list (pure function). Default is covered by the base rules and not judged here."""
    return [f"Net class {n} clearance {c} mm is below {NETCLASS_MIN} mm (SPEC-005 10.2): increase it, or state the reason in the config netclass_exceptions"
            for n, c in classes
            if n != "Default" and c is not None and c < NETCLASS_MIN - 1e-9 and n not in exceptions]


def judge(name, rc, text):
    """Whether a check script's output passes (pure function). hs_review has no exit code, so it is judged by "NOT MET"; route_check and fence_check also look at the exit code."""
    if name in ("hs_review", "route_check", "fence_check"):
        return "NOT MET" not in text and LEGACY_NOT_MET not in text and rc == 0
    if name == "impedance_check":
        return rc == 0 and "Result: no FAIL items" in text and LEGACY_NOT_MET not in text
    return rc == 0


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout + r.stderr


def gate(board):
    """Return (blocking items list, report lines list)."""
    board = pathlib.Path(board)
    block, rep = [], []
    cfg_path, dru = power_rules.paths(board)

    # 1, 2 Config and rules
    try:
        cfg = power_rules.load_cfg(cfg_path)
        rep.append(f"1 {cfg_path.name}: valid, {len(cfg['power'])} power net group(s), extra checks {', '.join(cfg['checks']) or 'none'}")
    except (ValueError, json.JSONDecodeError) as e:
        cfg = None
        block.append(f"{cfg_path.name}: {e}")
    text = dru.read_text(encoding="utf-8") if dru.exists() else ""
    if not power_rules.has_base(text):
        block.append(f"{dru.name} does not contain the base rule template: run power_rules.py")
    elif cfg is not None:
        want = power_rules.apply(power_rules.ensure_base(text, power_rules.BASE.read_text(encoding="utf-8")),
                                 power_rules.render(cfg["power"]))
        if want != text:
            block.append(f"{dru.name} base rules differ from the current template, or power rules differ from the config: run power_rules.py")
    rep.append(f"2 {dru.name}: {'checked' if text else 'does not exist'}")
    pro = board.with_suffix(".kicad_pro")
    if pro.exists():
        classes = [(c.get("name"), c.get("clearance"))
                   for c in json.loads(pro.read_text(encoding="utf-8")).get("net_settings", {}).get("classes", [])]
        exc = cfg["netclass_exceptions"] if cfg else {}
        nb = netclass_floor(classes, exc)
        block += nb
        rep.append(f"2b net class clearance: {'FAIL' if nb else 'PASS'}"
                   + "".join(f"; {n} waived by exception ({exc[n]})" for n, c in classes if n in exc))

    # 3 DRC
    from _kicad import kicad_cli
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "drc.json"
        rc, log = _run([kicad_cli(), "pcb", "drc", "--format", "json", "--severity-all", "--refill-zones",
                        "--schematic-parity", "-o", str(out), str(board)])
        if not out.exists():
            block.append(f"DRC could not run: {log.strip()[-300:]}")
        else:
            b, n_warn = summarize_drc(json.loads(out.read_text(encoding="utf-8")))
            block += b
            rep.append(f"3 DRC: {len(b)} blocking category(ies), {n_warn} warning(s) (warnings do not block; confirm each and write them into the design document)")

    # 4, 5 Check scripts
    # route_check always runs; if the config gives parameters (e.g. --vip to waive via-in-pad) run with them, otherwise with defaults
    runs = [] if cfg and "route_check" in cfg["checks"] else [("route_check", [])]
    for name, args in (cfg["checks"].items() if cfg else []):
        # Several parameter sets (list of lists): the same check runs once per set, any failing set blocks
        runs += [(name, a) for a in args] if args and isinstance(args[0], list) else [(name, args)]
    for name, args in runs:
        rc, outp = _run([sys.executable, str(HERE / f"{name}.py"), str(board), *args])
        ok = judge(name, rc, outp)
        rep.append(f"{'4' if name == 'route_check' else '5'} {name} {' '.join(args)}: {'PASS' if ok else 'FAIL'}")
        rep += ["   " + ln for ln in outp.splitlines() if ln.strip() and "image handler" not in ln]
        if not ok:
            block.append(f"{name} failed (see the check report for details)")
    return block, rep


def run_gate(board, out, allow=None):
    """Called before export: SystemExit if it fails and is not waived; the result is written to the out directory."""
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "gate_waiver.txt").unlink(missing_ok=True)       # An old waiver record must not linger into this run's results
    block, rep = gate(board)
    body = ["Blocking items:" + ("none" if not block else "")] + [f"  - {b}" for b in block] + ["", "Details:"] + rep
    (out / "check_report.txt").write_text("\n".join(body) + "\n", encoding="utf-8")
    print("\n".join(body[:len(block) + 1]))
    if not block:
        print(f"Production gate: PASS (report {out / 'check_report.txt'})")
        return
    if not allow:
        raise SystemExit(f"Production gate: {len(block)} item(s) failed, export refused. Fix and retry; to waive, first get explicit user consent, then use --allow \"who agreed + reason\" (report {out / 'check_report.txt'})")
    (out / "gate_waiver.txt").write_text(f"Waiver reason: {allow}\nFailed items:\n" + "\n".join(f"  - {b}" for b in block) + "\n",
                                     encoding="utf-8")
    print(f"Production gate: {len(block)} item(s) failed, released by --allow; reason and list in {out / 'gate_waiver.txt'}")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("--out", default="output")
    ap.add_argument("--allow", help="Waiver reason (written to gate_waiver.txt)")
    a = ap.parse_args(argv)
    run_gate(a.board, a.out, a.allow)


if __name__ == "__main__":
    main()
