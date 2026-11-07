"""Inner plane (power split layer) check: split gap width, vias crossing split lines, plane necks.

Usage (KiCad's bundled python):
  python plane_check.py <board.kicad_pcb> [--layer In2.Cu] [--gap-min-mil 10] [--gap-max-mil 20]
                        [--neck 1,2,3] [--edge-inset 0.6] [--bga-box X0 Y0 X1 Y1 ... --hs CSI_ ...]

Rules (see hubo-kicad references/05):
  - The split gap between planes of different nets must lie within [gap-min, gap-max] (only adjacent pairs are compared: where a third plane sits in between at the nearest point it is not a split gap;
    above the upper limit only wide gaps whose facing length is >= FACE_MIN count; point-like neighbouring voids are INFO, and those whose nearest point falls inside --bga-box are WARN;
    below the lower limit is always FAIL);
  - Signal vias must not cross a split line (FAIL); GND stitching vias crossing the line must be confirmed one by one (WARN); vias of this layer's own plane net crossing are INFO only.
    A split gap = void within gap-max of two different net planes at once; voids next to only one plane (around local power patches on a signal layer,
    mounting-hole notches at the board edge) do not count;
  - Power planes are shrunk inward by several widths; if one disconnects and the disconnected piece contains vias/pads of that net, there is a neck on the current path (WARN);
    a disconnected piece with no load is a no-load stub (INFO).
Exit code: 1 if there is any FAIL.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union

MIL = 0.0254
MIN_PIECE = 2.0  # mm^2, pour pieces smaller than this are treated as via annuli, not planes
FACE_MIN = 3.0   # mm, when two planes face each other across a void for less than this, an over-wide gap is not FAIL (see facing_length)


def outlines_by_net(fills):
    """[(net, poly)] -> {net: union of outer outlines} (holes removed: split-gap detection needs the plane outline, antipad holes must not count as gap)."""
    from shapely.geometry import Polygon
    out = {}
    for net, poly in fills:
        for g in getattr(poly, "geoms", [poly]):
            if g.area >= MIN_PIECE:
                out.setdefault(net, []).append(Polygon(g.exterior))
    return {n: unary_union(ps) for n, ps in out.items()}


def nested_holes(outlines, fills):
    """outlines_by_net fills all holes; when a hole encloses another net's plane (e.g. a 3V3 patch inside a big GND area) the hole must be cut back,
    otherwise the outer outline covers the inner one, the distance comes out 0 and the split gap between them is lost."""
    from shapely.geometry import Polygon
    out = dict(outlines)
    for net, poly in fills:
        others = [g for n, g in outlines.items() if n != net]
        for g in getattr(poly, "geoms", [poly]):
            for ring in g.interiors:
                h = Polygon(ring)
                if h.area >= MIN_PIECE and any(h.intersection(o).area > 0.1 for o in others):
                    out[net] = out[net].difference(h)
    return out


def split_gaps(inner, outlines, min_area=0.5, band=20 * MIL):
    """Split gap = void within band of two different net planes at once (including parts widened by vias).
    Voids next to only one plane do not count: large voids on local-pour layers (power patches on a signal layer) and mounting-hole notches at the board edge are not split lines."""
    nets = sorted(outlines)
    near = [outlines[a].buffer(band).intersection(outlines[b].buffer(band))
            for i, a in enumerate(nets) for b in nets[i + 1:]]
    if not near:
        return Polygon()
    g = inner.intersection(unary_union(near)).difference(unary_union(list(outlines.values())))
    return unary_union([p for p in getattr(g, "geoms", [g]) if p.area >= min_area])


def plane_spacing(outlines, near=2.0):
    """Minimum distance between adjacent (distance < near mm) planes of different nets: {(a, b): mm}.
    Pairs with a third plane in between at the nearest point are not adjacent (e.g. patches A|B|C side by side under a BGA: the A<->C distance is not a split gap) and are not counted."""
    nets = sorted(outlines)
    res = {}
    for i, a in enumerate(nets):
        for b in nets[i + 1:]:
            d = outlines[a].distance(outlines[b])
            if d >= near:
                continue
            seg = LineString(nearest_points(outlines[a], outlines[b]))
            if d > 0 and any(seg.intersection(outlines[c]).length > 1e-6 for c in nets if c not in (a, b)):
                continue
            res[(a, b)] = d
    return res


def facing_length(outlines, a, b, near=2.0):
    """Length (mm) over which a and b face each other across a void = area left over after growing each by near/2, overlapping, and belonging to no plane, divided by the nearest distance.
    Parallel facing wide gaps give the gap length; patches under a BGA that meet near a point across via antipads give only a few tenths of a millimetre."""
    d = outlines[a].distance(outlines[b])
    zone = outlines[a].buffer(near / 2).intersection(outlines[b].buffer(near / 2)).difference(unary_union(list(outlines.values())))
    return zone.area / max(d, 0.05)


def classify_hole(net, plane_nets, ground="GND", in_bga=False, hs=()):
    """in_bga: the via is inside the --bga-box area. In a BGA fan-out area the via pitch is below the ~1.2 mm needed for "two vias on two planes, each fully enclosed"
    (2 x (via radius + clearance + min copper width) + split gap), so the split line necessarily crosses vias; here vias of non-high-speed nets are lowered to WARN for one-by-one confirmation, high-speed nets stay FAIL."""
    if net == ground:
        return "WARN"
    if net in plane_nets:
        return "INFO"
    if in_bga and not net.startswith(tuple(hs)):
        return "WARN"
    return "FAIL"


def necks(poly, widths, loads):
    """Shrink the plane by widths step by step; at the first disconnection return (width, neck position, load points in the disconnected piece); None if it never disconnects.
    loads: via/pad coordinates of the net, used to decide whether the disconnected piece carries current."""
    for w in widths:
        e = poly.buffer(-w / 2)
        parts = sorted([g for g in getattr(e, "geoms", [e]) if g.area > 0.2], key=lambda g: -g.area)
        if len(parts) > 1:
            a, b = nearest_points(parts[0], parts[1])
            where = ((a.x + b.x) / 2, (a.y + b.y) / 2)
            cut = unary_union(parts[1:]).buffer(w / 2 + 0.5)
            n_loads = sum(1 for p in loads if cut.contains(Point(p)))
            return w, where, n_loads
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("--layer", default="In2.Cu", help="Power split layer to check (standard layer name), default In2.Cu")
    ap.add_argument("--ground", default="GND")
    ap.add_argument("--gap-min-mil", type=float, default=10)
    ap.add_argument("--gap-max-mil", type=float, default=20)
    ap.add_argument("--neck", default="1,2,3", help="Widths for the shrink check (mm, comma-separated)")
    ap.add_argument("--edge-inset", type=float, default=0.6, help="Board-outline inset (mm), removes the pour's edge pullback band")
    ap.add_argument("--bga-box", type=float, nargs=4, action="append", default=[], metavar=("X0", "Y0", "X1", "Y1"),
                    help="BGA fan-out area (relative to the drill origin, mm, repeatable): vias in the area on non---hs nets crossing a line are WARN; if used, state it in the design document")
    ap.add_argument("--hs", nargs="*", default=[], help="High-speed net name prefixes: still FAIL when crossing a line inside --bga-box")
    a = ap.parse_args(argv)

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from _kicad import board_outline, boxes_abs, holes, load_pcbnew, origin, zone_fills
    pcbnew = load_pcbnew()
    b = pcbnew.LoadBoard(a.board)
    lid = b.GetLayerID(a.layer)
    outline = board_outline(b, pcbnew)
    fills = zone_fills(b, lid, pcbnew)
    if not fills:
        raise SystemExit(f"No copper pour on {a.layer} (fill first with kicad-cli pcb drc --refill-zones --save-board)")
    outl = nested_holes(outlines_by_net(fills), fills)
    fails = 0
    print(f"== {a.layer} (display name {b.GetLayerName(lid)}) planes")
    for net, g in sorted(outl.items()):
        blocks = list(getattr(g, "geoms", [g]))
        print(f"  {net}: {len(blocks)} piece(s), " + ", ".join(f"{p.area:.1f}" for p in sorted(blocks, key=lambda p: -p.area)) + " mm^2")

    print("== Split gap width")
    lo, hi = a.gap_min_mil * MIL, a.gap_max_mil * MIL
    org = origin(b, pcbnew)   # Output coordinates are always relative to the drill origin, same system as route_check / hs_review
    bga = unary_union(boxes_abs(a.bga_box, org)) if a.bga_box else None
    for (n1, n2), d in sorted(plane_spacing(outl).items()):
        st = "OK  " if lo - 0.002 <= d <= hi + 0.002 else "FAIL"
        note = ""
        if d > hi + 0.002:
            face = facing_length(outl, n1, n2)
            if face < FACE_MIN:      # Over-wide counts only for continuous split gaps; point-like neighbours (via voids where patches meet under a BGA) do not
                st, note = "INFO", f"; adjacent across a void at one spot only (facing length {face:.1f} mm < {FACE_MIN:g}), not a split gap"
            elif bga is not None and all(bga.contains(p) for p in nearest_points(outl[n1], outl[n2])):
                st, note = "WARN", "; inside a BGA fan-out area (the via array antipads widen the fill edge), confirm one by one"
        fails += st == "FAIL"
        print(f"  [{st}] {n1} <-> {n2}: {d:.3f} mm ({d / MIL:.1f} mil), required {a.gap_min_mil:g} to {a.gap_max_mil:g} mil{note}")

    print("== Vias crossing split lines")
    gap = split_gaps(outline.buffer(-a.edge_inset), outl, band=hi)
    plane_nets = set(outl)
    n_on = 0
    for name, net, x, y, r in holes(b, pcbnew):
        if Point(x, y).buffer(r).intersects(gap):
            in_bga = bga is not None and bga.contains(Point(x, y))
            st = classify_hole(net, plane_nets, a.ground, in_bga, a.hs)
            fails += st == "FAIL"
            n_on += 1
            note = {"FAIL": "signal via crosses the line, must be moved", "INFO": "via of this layer's plane net",
                    "WARN": "GND stitching via, confirm one by one" if net == a.ground else "non-high-speed via crossing the line in a BGA fan-out area, confirm one by one"}[st]
            print(f"  [{st}] {name} {net} ({x - org[0]:.2f}, {y - org[1]:.2f}): {note}")
    if not n_on:
        print("  [OK  ] none")

    print("== Necks (power planes shrunk by width)")
    widths = [float(w) for w in a.neck.split(",")]
    pads = {}
    for name, net, x, y, r in holes(b, pcbnew):
        pads.setdefault(net, []).append((x, y))
    for net, poly in sorted(fills_by_net(fills).items()):
        if net == a.ground:
            continue
        res = necks(poly, widths, pads.get(net, []))
        if res is None:
            print(f"  [OK  ] {net}: still connected when shrunk to {widths[-1]:g} mm")
            continue
        w, (x, y), n = res
        st = "WARN" if n else "INFO"
        why = f"the disconnected piece has {n} via(s)/pad(s) of this net, on the current path" if n else "the disconnected piece has no load (no-load stub)"
        print(f"  [{st}] {net}: disconnects at width {w:g} mm, neck near ({x - org[0]:.1f}, {y - org[1]:.1f}); {why}")
    print("Result: " + ("has FAIL items" if fails else "no FAIL items"))
    return 1 if fails else 0


def fills_by_net(fills):
    """{net: largest pour piece with holes} - the neck check only looks at each net's main plane."""
    best = {}
    for net, poly in fills:
        for g in getattr(poly, "geoms", [poly]):
            if net not in best or g.area > best[net].area:
                best[net] = g
    return best


if __name__ == "__main__":
    sys.exit(main())
