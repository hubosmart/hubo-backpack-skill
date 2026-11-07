"""Add ground vias to isolated outer-layer GND pour islands: when an island has a GND pad but no GND via / through-hole pad, that pad is not actually grounded.

Why a script: such islands are often wedged under high-speed traces or between QFN breakouts, and MCP add_gnd_stitching_vias picks grid points that cannot land inside;
here, inside the island, find the usable area that satisfies "other-layer copper, high-speed corridor and via spacing" and place the via at the point farthest from the boundary (polylabel).
First run `kicad-cli pcb drc --refill-zones --save-board` to fill the pours, then run this.
Usage (KiCad's bundled python):
  python island_via.py <board.kicad_pcb> [--gnd GND] [--via 0.4 0.2] [--clear 0.12]
         [--hs TMDS_ CSI_ --hs-clear 0.15 --corridor 0.45 --exempt X0 Y0 X1 Y1 ...]
  --hs: high-speed net name prefixes; via spacing to these nets uses --hs-clear, and the via must stay out of their --corridor (3W);
  --exempt: corridor exempt boxes (relative to the drill origin, repeatable), e.g. QFN breakout area, connector pad area.
If no position is found it only reports and does not change the board; reroute to free up space (e.g. remove the power track segment surrounding the island and route around it).
"""
import argparse
import os
import sys

from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import polylabel, unary_union

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kicad import boxes_abs, hs_corridors, load_pcbnew, origin  # noqa: E402


def spot(island, blk, pads, vd, clear):
    """Point inside the island for a via (farthest from the usable-area boundary); None if none found.
    pads: all outer-layer pads (including same-net GND) - an island often has only the pad itself; without excluding same-net pads the via lands at the pad center and becomes via-in-pad.
    The via annulus keeps clear from the pads."""
    free = island.buffer(-vd / 2 - 0.005).difference(blk)
    if pads:
        free = free.difference(unary_union(pads).buffer(clear + vd / 2 + 0.005))
    if free.is_empty:
        return None
    return polylabel(max(getattr(free, "geoms", [free]), key=lambda g: g.area), 0.005)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--gnd", default="GND")
    ap.add_argument("--via", type=float, nargs=2, default=[0.4, 0.2], metavar=("OD", "DRILL"))
    ap.add_argument("--clear", type=float, default=0.12)
    ap.add_argument("--hole-gap", type=float, default=0.25)
    ap.add_argument("--hs", nargs="*", default=[])
    ap.add_argument("--hs-clear", type=float, default=0.15)
    ap.add_argument("--corridor", type=float, default=0.0)
    ap.add_argument("--exempt", type=float, nargs=4, action="append", default=[])
    a = ap.parse_args()
    pcbnew = load_pcbnew()
    mm = pcbnew.ToMM
    b = pcbnew.LoadBoard(a.board)
    org = origin(b, pcbnew)
    vd, vh = a.via
    pos = lambda v: (mm(v.x), mm(v.y))  # noqa: E731
    anchors = [Point(pos(t.GetPosition())) for t in b.GetTracks() if t.Type() == pcbnew.PCB_VIA_T and t.GetNetname() == a.gnd]
    anchors += [Point(pos(p.GetPosition())) for f in b.GetFootprints() for p in f.Pads()
                if p.GetNetname() == a.gnd and p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH]
    outer = (pcbnew.F_Cu, pcbnew.B_Cu)
    obst, holes = [], []
    pad_polys = []   # All pads on both outer layers: a via goes through both sides, so landing in a pad on either side is via-in-pad
    clr = lambda n: a.hs_clear if a.hs and n.startswith(tuple(a.hs)) else a.clear  # noqa: E731
    for t in b.GetTracks():
        n = t.GetNetname()
        if t.Type() == pcbnew.PCB_VIA_T:
            holes.append(Point(pos(t.GetPosition())).buffer(mm(t.GetDrillValue()) / 2))
            if n != a.gnd:
                obst.append(Point(pos(t.GetPosition())).buffer(mm(t.GetWidth(pcbnew.F_Cu)) / 2 + clr(n)))
        elif n != a.gnd:
            # A via goes through all copper layers: inner-layer tracks must clear it too (8-layer test run: a via pressed an In5 track by 0.027 mm, DRC clearance)
            obst.append(LineString([pos(t.GetStart()), pos(t.GetEnd())]).buffer(mm(t.GetWidth()) / 2 + clr(n)))
    for f in b.GetFootprints():
        for p in f.Pads():
            bb = p.GetBoundingBox()
            g = box(mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom()))
            if p.GetDrillSize().x > 0:
                holes.append(g)
            if p.GetNetname() != a.gnd and any(p.IsOnLayer(l) for l in outer):
                obst.append(g.buffer(clr(p.GetNetname())))
            if any(p.IsOnLayer(l) for l in outer):
                pad_polys.append(g)
    # Via-prohibited rule areas (e.g. around FID_MARK): the via must not land inside, else items_not_allowed + solder-mask bridge
    for z in b.Zones():
        if z.GetIsRuleArea() and z.GetDoNotAllowVias():
            bb = z.GetBoundingBox()
            obst.append(box(mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())))
    blk = unary_union(obst).buffer(vd / 2 + 0.005).union(unary_union(holes).buffer(a.hole_gap + vh / 2 + 0.005))
    cor = hs_corridors(b, pcbnew, a.hs, a.corridor, boxes_abs(a.exempt, org)) if a.corridor > 0 else {}
    if cor:
        blk = blk.union(unary_union(list(cor.values())).buffer(vd / 2))
    added = 0
    for z in b.Zones():
        if z.GetNetname() != a.gnd or z.GetIsRuleArea():
            continue
        for lid in outer:
            if not z.IsOnLayer(lid):
                continue
            ps = pcbnew.SHAPE_POLY_SET(z.GetFilledPolysList(lid))
            ps.Unfracture()
            for i in range(ps.OutlineCount()):
                o = ps.Outline(i)
                hs_ = [[pos(ps.Hole(i, h).CPoint(k)) for k in range(ps.Hole(i, h).PointCount())] for h in range(ps.HoleCount(i))]
                poly = Polygon([pos(o.CPoint(k)) for k in range(o.PointCount())], hs_).buffer(0)
                if any(poly.contains(x) for x in anchors):
                    continue
                pads = [f"{f.GetReference()}.{p.GetNumber()}" for f in b.GetFootprints() for p in f.Pads()
                        if p.GetNetname() == a.gnd and p.IsOnLayer(lid) and poly.intersects(Point(pos(p.GetPosition())))]
                if not pads:
                    continue   # Floating copper without pads is not handled
                c = spot(poly, blk, pad_polys, vd, a.clear)
                if c is None:
                    print(f"{b.GetLayerName(lid)} island ({', '.join(pads)}): no usable position (via must not land in a pad)")
                    continue
                v = pcbnew.PCB_VIA(b)
                v.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(c.x), pcbnew.FromMM(c.y)))
                v.SetWidth(pcbnew.FromMM(vd))
                v.SetDrill(pcbnew.FromMM(vh))
                v.SetNet(b.FindNet(a.gnd))
                b.Add(v)
                anchors.append(c)
                added += 1
                print(f"{b.GetLayerName(lid)} island ({', '.join(pads)}): ground via ({c.x - org[0]:.3f}, {c.y - org[1]:.3f}) (relative to origin)")
    if added:
        b.Save(a.board)
    print("Ground vias added:", added)


if __name__ == "__main__":
    main()
