"""Export production files for JLCPCB ordering: Gerber + drill (zipped), BOM, placement (KiCad original + JLCPCB CPL).

Usage (KiCad's bundled python):
  python export_prod.py <board.kicad_pcb> <schematic.kicad_sch> [--out output] [--name prefix]
                        [--origin auto|drill|absolute] [--lcsc-field LCSC] [--allow "waiver reason"]

- The production gate (prod_gate.py: project config, DRC, routing and high-speed checks) runs first; export is refused if it fails;
  waiving requires explicit user consent first, then add --allow "who agreed + reason"; the reason and failed items go to <out>/gate_waiver.txt;

- Copper layers are listed from the board's actual layer count (2 / 4 ...); solder mask, silkscreen, stencil and outline are exported too, with silkscreen minus mask openings;
- --origin auto: if the board has a drill/placement origin (not 0,0) export relative to it, else use absolute coordinates. If the board was shifted as a whole in the UI,
  set the origin at the original board center (set_origin.py) to keep production-file coordinates unchanged;
- The drill map *-drl_map.gbr is for viewing only and is not put into the order zip;
- BOM is grouped by value + footprint + part number, with the column names JLCPCB requires: Comment / Designator / Footprint / LCSC Part #;
  MCP export_sch_bom drops empty-string parameters (designators come out as ranges like C16-C19), so kicad-cli is called directly here.
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import subprocess
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

TECH = ["F.Mask", "B.Mask", "F.Silkscreen", "B.Silkscreen", "F.Paste", "B.Paste", "Edge.Cuts"]


def copper_layers(n):
    return ["F.Cu"] + [f"In{i}.Cu" for i in range(1, n - 1)] + ["B.Cu"]


def pos_to_jlc(rows):
    """KiCad placement CSV row -> JLCPCB CPL row."""
    return [(r["Ref"], f"{r['PosX']}mm", f"{r['PosY']}mm", r["Side"].capitalize(), r["Rot"]) for r in rows]


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"Command failed: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("schematic")
    ap.add_argument("--out", default="output")
    ap.add_argument("--name", default=None, help="File name prefix, defaults to the board file name")
    ap.add_argument("--origin", choices=("auto", "drill", "absolute"), default="auto")
    ap.add_argument("--lcsc-field", default="LCSC")
    ap.add_argument("--allow", help="Waiver reason used when the production gate fails")
    a = ap.parse_args(argv)

    from prod_gate import run_gate
    run_gate(a.board, a.out, a.allow)

    from _kicad import kicad_cli, load_pcbnew
    pcbnew = load_pcbnew()
    k = kicad_cli()
    b = pcbnew.LoadBoard(a.board)
    n_cu = b.GetCopperLayerCount()
    ao = b.GetDesignSettings().GetAuxOrigin()
    use_drill = a.origin == "drill" or (a.origin == "auto" and (ao.x, ao.y) != (0, 0))
    name = a.name or pathlib.Path(a.board).stem
    out = pathlib.Path(a.out)
    gdir = out / "gerber"
    gdir.mkdir(parents=True, exist_ok=True)
    for f in gdir.iterdir():
        f.unlink()

    layers = ",".join(copper_layers(n_cu) + TECH)
    run([k, "pcb", "export", "gerbers", "--subtract-soldermask", *(["--use-drill-file-origin"] if use_drill else []),
         "-l", layers, "-o", str(gdir) + "/", a.board])
    run([k, "pcb", "export", "drill", "--drill-origin", "plot" if use_drill else "absolute", "--excellon-separate-th",
         "--generate-map", "--map-format", "gerberx2", "-o", str(gdir) + "/", a.board])
    zpath = out / f"{name}-gerber.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(gdir.iterdir()):
            if "drl_map" not in f.name:
                z.write(f, f.name)

    run([k, "sch", "export", "bom", "--fields", f"Value,Reference,Footprint,{a.lcsc_field},${{QUANTITY}}",
         "--labels", "Comment,Designator,Footprint,LCSC Part #,Quantity",
         "--group-by", f"Value,Footprint,{a.lcsc_field}", "--ref-range-delimiter", "", "--exclude-dnp",
         "-o", str(out / f"{name}-bom.csv"), a.schematic])
    pos = out / f"{name}-pos.csv"
    run([k, "pcb", "export", "pos", "--format", "csv", "--units", "mm", "--exclude-dnp",
         *(["--use-drill-file-origin"] if use_drill else []), "-o", str(pos), a.board])
    rows = list(csv.DictReader(open(pos, encoding="utf-8")))
    with open(out / f"{name}-cpl-jlc.csv", "w", newline="", encoding="utf-8") as f:
        f.write("Designator,Mid X,Mid Y,Layer,Rotation\n")
        for r in pos_to_jlc(rows):
            f.write(",".join(r) + "\n")

    n_files = len(zipfile.ZipFile(zpath).namelist())
    print(f"{n_cu}-layer board; coordinate reference: {'drill/placement origin' if use_drill else 'absolute coordinates'}")
    print(f"Exported to {out}: Gerber/drill {n_files} files ({zpath.name}), BOM, placement {len(rows)} SMD parts")
    if not rows:
        print("Warning: the placement file is empty - imported footprints may be typed \"unspecified\"; set them to SMD with MCP set_footprint_type and re-export")


if __name__ == "__main__":
    main()
