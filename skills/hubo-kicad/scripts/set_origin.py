"""Set the drill/placement origin (aux origin) and the grid origin.

Usage (KiCad's bundled python):
  python set_origin.py <board.kicad_pcb>                 # set at the center of the outline bounding box
  python set_origin.py <board.kicad_pcb> --at X,Y        # set at the given absolute coordinates (mm)

Why: after the board is shifted as a whole in the KiCad UI, check scripts that work in absolute coordinates, enclosure coordinates and production files
are all off. Set the origin back to "where the original board center is now", then export with export_prod.py (--origin auto) and
kicad-cli pcb export glb --drill-origin, and the coordinates match those before the shift.
Make sure the board is closed in the KiCad UI before changing the file, or the UI will overwrite it on save.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("--at", default=None, help="X,Y (mm, absolute coordinates); defaults to the outline center")
    a = ap.parse_args(argv)
    from _kicad import load_pcbnew
    pcbnew = load_pcbnew()
    b = pcbnew.LoadBoard(a.board)
    if a.at:
        x, y = (float(v) for v in a.at.split(","))
        c = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
    else:
        c = b.GetBoardEdgesBoundingBox().GetCenter()
    ds = b.GetDesignSettings()
    ds.SetAuxOrigin(c)
    ds.SetGridOrigin(c)
    pcbnew.SaveBoard(a.board, b)
    print(f"Drill/placement origin and grid origin set to ({pcbnew.ToMM(c.x):.6f}, {pcbnew.ToMM(c.y):.6f}) mm")


if __name__ == "__main__":
    main()
