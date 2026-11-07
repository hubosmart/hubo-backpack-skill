"""Write the PCB stackup, copper layer display names and the impedance note on the board (rerunnable).

Usage (KiCad's bundled python):
  python stackup.py <board.kicad_pcb> --stackup JLC04161H-3313 --names TopLayer,GND,GND2,BottomLayer \
      --note "RF antenna trace: 50 ohm coplanar single-ended, L1 referenced to L2, width 5.9 mil, 10.0 mil to ground copper on both sides" --note-at=-45,26
  For boards with no impedance traces (e.g. LED boards) add --no-impedance: item 1 is rewritten as "order without impedance control" to avoid paying the impedance fee

What it does:
- Stackup: write the stack of that stackup from assets/impedance_table.json into (setup (stackup ...)), with layer thickness and Dk matching the board house,
  so KiCad's own impedance calculator and length-delay get correct parameters. JLCPCB manufactures by the stackup chosen when ordering and does not read this, so this step only affects calculations inside KiCad;
- Layer names: copper layers are renamed in the order F.Cu, In1.Cu ... B.Cu (SPEC-003 6.1.1). Gerber file names change accordingly; the board house recognizes them by extension;
- Note: on the Cmts.User layer (not in Gerber) write "[PCB impedance and stackup notes]", coordinates relative to the drill origin. On rerun the old note is deleted first.
Make sure the board is closed in the KiCad UI before changing the file.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

TABLE = pathlib.Path(__file__).resolve().parents[1] / "assets" / "impedance_table.json"
TITLE = "[PCB impedance and stackup notes]"
LEGACY_TITLE = "【PCB阻抗控制与层叠说明】"   # legacy marker, kept for compatibility: reruns must delete notes written by earlier versions
MASK_TYPE = {"F.Mask": "Top Solder Mask", "B.Mask": "Bottom Solder Mask"}


def is_note(text):
    """Whether board text is a note written by this script, under the current or the legacy title (pure function)."""
    return text.startswith((TITLE, LEGACY_TITLE))


def find_stack(name):
    for plat in json.loads(TABLE.read_text(encoding="utf-8"))["platforms"].values():
        st = plat.get("stackups", {}).get(name)
        if st:
            if "stack" not in st:
                raise SystemExit(f"{name} has no stack in impedance_table.json (per-layer thickness and Dk), add the data first")
            return st
    raise SystemExit(f"impedance_table.json has no stackup {name}")


def stackup_sexpr(stack):
    """stack -> KiCad (stackup ...) text (tab indentation, same as KiCad's save format)."""
    t2, t3, t4 = "\t\t", "\t\t\t", "\t\t\t\t"
    out = [f"{t2}(stackup", f'{t3}(layer "F.SilkS"', f'{t4}(type "Top Silk Screen")', f"{t3})",
           f'{t3}(layer "F.Paste"', f'{t4}(type "Top Solder Paste")', f"{t3})"]
    n = 0
    for e in stack:
        if "layer" in e and e["layer"] in MASK_TYPE:
            out += [f'{t3}(layer "{e["layer"]}"', f'{t4}(type "{MASK_TYPE[e["layer"]]}")',
                    f'{t4}(thickness {e["thickness"]})', f'{t4}(epsilon_r {e["epsilon_r"]})', f"{t3})"]
        elif "layer" in e:
            out += [f'{t3}(layer "{e["layer"]}"', f'{t4}(type "copper")', f'{t4}(thickness {e["thickness"]})', f"{t3})"]
        else:
            n += 1
            out += [f'{t3}(layer "dielectric {n}"', f'{t4}(type "{e["type"]}")', f'{t4}(color "FR4 natural")',
                    f'{t4}(thickness {e["thickness"]})', f'{t4}(material "{e["material"]}")',
                    f'{t4}(epsilon_r {e["epsilon_r"]})', f'{t4}(loss_tangent 0.02)', f"{t3})"]
    out += [f'{t3}(layer "B.Paste"', f'{t4}(type "Bottom Solder Paste")', f"{t3})",
            f'{t3}(layer "B.SilkS"', f'{t4}(type "Bottom Silk Screen")', f"{t3})",
            f"{t3}(dielectric_constraints yes)", f"{t2})"]
    return "\n".join(out) + "\n"


def set_stackup_text(path, block):
    s = path.read_text(encoding="utf-8")
    s = re.sub(r"\t\t\(stackup\n.*?\n\t\t\)\n", "", s, count=1, flags=re.S)
    assert s.count("\t(setup\n") == 1, "(setup not found"
    path.write_text(s.replace("\t(setup\n", "\t(setup\n" + block, 1), encoding="utf-8", newline="\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("--stackup", required=True, help="Stackup model in impedance_table.json, e.g. JLC04161H-3313")
    ap.add_argument("--names", required=True, help="Copper layer display names in the order F.Cu, In1.Cu ... B.Cu, comma-separated")
    ap.add_argument("--note", action="append", default=[], help="Impedance entry in the note, one per line, repeatable")
    ap.add_argument("--note-at", default="0,0", help="Top-left X,Y of the note (mm, relative to the drill origin), placed outside the outline; write negatives as --note-at=-45,26")
    ap.add_argument("--no-impedance", action="store_true",
                    help="Board has no impedance-controlled nets (e.g. LED board): item 1 of the note says to order without impedance control; the stackup is for KiCad calculation reference only")
    a = ap.parse_args(argv)
    st = find_stack(a.stackup)
    from _kicad import load_pcbnew, origin
    pcbnew = load_pcbnew()
    path = pathlib.Path(a.board)
    b = pcbnew.LoadBoard(str(path))

    cu = [pcbnew.F_Cu] + [pcbnew.BOARD.GetLayerID(b, f"In{i}.Cu") for i in range(1, b.GetCopperLayerCount() - 1)] + [pcbnew.B_Cu]
    names = a.names.split(",")
    if len(names) != len(cu):
        raise SystemExit(f"Board has {len(cu)} copper layers, --names gave {len(names)}")
    for lid, n in zip(cu, names):
        b.SetLayerName(lid, n)

    for d in list(b.GetDrawings()):
        if isinstance(d, pcbnew.PCB_TEXT) and is_note(d.GetText()):
            b.Remove(d)
    total = sum(e["thickness"] for e in st["stack"])
    laminate = sum(e["thickness"] for e in st["stack"] if e.get("layer") not in MASK_TYPE)   # The board house's "finished thickness" excludes solder mask
    ask = "This board has no impedance-controlled nets; order without impedance control (the stackup is for KiCad calculation reference only)" if a.no_impedance else "Request impedance control from the board house"
    lines = [TITLE, f"1. Laminate structure: {a.stackup} ({st['layers']} layers, finished thickness about {laminate:.2f} mm), {ask}"]
    lines += [f"{i}. {n}" for i, n in enumerate(a.note, 2)]
    ox, oy = origin(b, pcbnew)
    x, y = (float(v) for v in a.note_at.split(","))
    t = pcbnew.PCB_TEXT(b)
    t.SetText("\n".join(lines))
    t.SetLayer(pcbnew.Cmts_User)
    t.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(ox + x), pcbnew.FromMM(oy + y)))
    t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(1.0), pcbnew.FromMM(1.0)))
    t.SetTextThickness(pcbnew.FromMM(0.15))
    t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
    t.SetVertJustify(pcbnew.GR_TEXT_V_ALIGN_TOP)
    b.Add(t)
    b.Save(str(path))

    set_stackup_text(path, stackup_sexpr(st["stack"]))
    # Read-back check: KiCad can parse it and keeps the stackup after re-saving (the stackup descriptor has no Python binding, so only the file can be compared)
    b = pcbnew.LoadBoard(str(path))
    got = [b.GetLayerName(l) for l in cu]
    probe = path.with_suffix(".stackup_probe.kicad_pcb")
    b.Save(str(probe))
    kept = re.findall(r"\(thickness ([\d.]+)\)", re.search(r"\t\t\(stackup\n.*?\n\t\t\)\n", probe.read_text(encoding="utf-8"),
                                                           flags=re.S).group(0))
    for f in probe.parent.glob(probe.stem + ".*"):     # Save also generates a same-name .kicad_pro etc.
        f.unlink()
    print(f"Layer names {got}; stackup {a.stackup} keeps {len(kept)} thickness item(s) after re-save, total {sum(map(float, kept)):.4f} mm"
          f" (table {total:.4f}; board thickness setting {pcbnew.ToMM(b.GetDesignSettings().GetBoardThickness()):.2f} mm)")
    print("Note:\n  " + "\n  ".join(lines))
    if got != names:
        raise SystemExit("Layer name read-back mismatch")


if __name__ == "__main__":
    main()
