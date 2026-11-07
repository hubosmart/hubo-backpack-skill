"""Freerouting pre/post-processing (works with KiCAD MCP export_dsn / import_ses).

Usage (KiCad's bundled python):
  python route_prep.py copy  <board> <out> [--keepout area-name ...] [--gap In2.Cu:B.Cu ...] [--edge-band 1.5]
      Routing copy: lock all tracks/vias/footprints; copy the given rule areas (e.g. directly under a module) into outer-layer track + via keepouts;
      for each "plane layer:outer layer", add a keepout on the outer layer directly above split gaps between planes of different nets on the plane layer (prevents crossing a split); board-edge band keepout (left for stitching vias).
  python route_prep.py strip <in.dsn> <out.dsn> --nets GND,+5V,3V3[,frozen RF nets] [--plane-layers In1.Cu,In2.Cu]
      DSN post-processing: clear the pin lists of these nets; mark all existing copper protect; set inner layers to power (no routing on plane layers).
      --plane-layers accepts standard names or display names (GND, PWR); layer names not found in the DSN are an error.
  python route_prep.py dump  <board> <out.json>
  python route_prep.py merge <board> <orig.json> <routed.json>
      Add tracks/vias in routed that are not in orig to board.

Full flow (references/04): copy -> MCP reload_board(copy) -> export_dsn (without boardPath) -> strip -> Freerouting
-> MCP import_ses(trial copy) -> dump the real board and the trial copy -> merge(real board).
Why the SES cannot be imported straight into the real board: import_ses clears all tracks on the board and rebuilds them from the SES, and Freerouting does not write protect
tracks into the SES - RF and hand-routed tracks would all be lost. Loading two boards in one process gives untyped SWIG objects, so merging goes through JSON in separate processes.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

from shapely.geometry import box
from shapely.ops import unary_union

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


# ---------------- Pure functions (self-testable without pcbnew) ----------------

def strip_nets(dsn, nets):
    """Clear the (pins ...) of the given nets in the network section; the nets themselves stay (routed tracks/vias still reference them).
    If pour-connected power nets are not cleared, Freerouting connects all ground/power pads with tracks."""
    def repl(m):
        return m.group(0) if m.group(2) not in nets else m.group(1) + "(pins)"
    return re.sub(r'(\(net\s+"?([^"\s()]+)"?\s*\n\s*)\(pins[^)]*\)', repl, dsn)


def dsn_layers(dsn):
    """Copper layer names of the DSN structure section, in stackup order (F.Cu, In1.Cu ... B.Cu).
    When copper layers were renamed (stackup.py writes TopLayer/GND/PWR ...), the DSN exported by KiCad uses the display names."""
    return [n.strip('"') for n in re.findall(r'\(layer\s+("[^"]+"|[^\s()]+)\s*\n\s*\(type (?:signal|power)\)', dsn)]


def inner_layers(dsn):
    return dsn_layers(dsn)[1:-1]


def resolve_layers(dsn, names):
    """Convert user-given layer names to DSN layer names: names present in the DSN are used as-is; standard names (F.Cu/In<k>.Cu/B.Cu) are converted by stackup position.
    Unmatched names raise ValueError - silently ignoring them would let Freerouting route on plane layers."""
    have = dsn_layers(dsn)
    std = {"F.Cu": 0, "B.Cu": len(have) - 1, **{f"In{k}.Cu": k for k in range(1, len(have) - 1)}}
    out, bad = [], []
    for n in names:
        if n in have:
            out.append(n)
        elif n in std and have:
            out.append(have[std[n]])
        else:
            bad.append(n)
    if bad:
        raise ValueError(f"Layer {bad} not found in the DSN; DSN layers (stackup order): {have}")
    return out


def plane_layers(dsn, layers):
    """KiCad exports inner layers as signal; after changing to power, Freerouting does not route on whole-layer ground / split power. layers use DSN layer names."""
    for lay in layers:
        dsn = re.sub(r'(\(layer\s+"?%s"?\s*\n\s*)\(type signal\)' % re.escape(lay), r"\1(type power)", dsn)
    return dsn


def protect_wiring(dsn):
    """KiCad does not write "locked" as protect in the DSN; existing copper is always (type route) and Freerouting would rip it up and reroute."""
    return dsn.replace("(type route)", "(type protect)")


def plane_gaps(board, fills, edge_inset=1.0, min_area=0.5, grow=0.3, near=1.0):
    """Split gaps between planes of different nets on a plane layer. fills: [(net, polygon)].
    Inset the board outline by edge_inset, subtract all pours, keep only the parts within near of copper of two different nets at once, and grow each piece by grow.
    Voids next to only one net (slots left by this layer's signal tracks, via antipads, sharp corners) are not split gaps: both sides of a slot are the same plane,
    return current on an outer signal crossing it stays continuous, and keeping them out would needlessly cut the outer layer."""
    by_net = {}
    for n, p in fills:
        by_net.setdefault(n, []).append(p)
    by_net = {n: unary_union(ps) for n, ps in by_net.items()}
    nets = sorted(by_net)
    between = [by_net[a].buffer(near).intersection(by_net[b].buffer(near)) for i, a in enumerate(nets) for b in nets[i + 1:]]
    if not between:
        return []
    inner = board.buffer(-edge_inset)
    g = inner.difference(unary_union(list(by_net.values()))).buffer(-0.05).buffer(0.05)
    g = g.intersection(unary_union(between))
    parts = list(getattr(g, "geoms", [g]))
    return [p.buffer(grow).intersection(board) for p in parts if not p.is_empty and p.area >= min_area]


def edge_band(board, width):
    """Ring of width width inside the board edge, cut by a center cross into four hole-free polygons (DSN keepouts do not support polygons with holes)."""
    ring = board.difference(board.buffer(-width, join_style=2))
    x0, y0, x1, y1 = board.bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    out = []
    for q in (box(x0, y0, cx, cy), box(cx, y0, x1, cy), box(x0, cy, cx, y1), box(cx, cy, x1, y1)):
        part = ring.intersection(q)
        out += [g for g in getattr(part, "geoms", [part]) if g.area > 1e-9]
    return out


def _key(x):
    if x[0] == "trk":
        return ("trk", x[1], *sorted([tuple(x[2:4]), tuple(x[4:6])]), x[7])
    return ("via", x[1], x[2], x[5])


def new_items(orig, routed):
    """Items in routed whose geometry (tracks are direction-less) and net are both not in orig."""
    have = {_key(x) for x in orig}
    return [x for x in routed if _key(x) not in have]


# ---------------- pcbnew part ----------------

def dump(board_path, out_json):
    from _kicad import load_pcbnew
    pcbnew = load_pcbnew()
    b = pcbnew.LoadBoard(board_path)
    out = []
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            out.append(["via", t.GetPosition().x, t.GetPosition().y, t.GetWidth(pcbnew.F_Cu), t.GetDrill(), t.GetNetname()])
        else:  # Use standard layer names: copper layers may have been renamed (e.g. TopLayer), and GetLayerName would return the display name
            out.append(["trk", pcbnew.BOARD.GetStandardLayerName(t.GetLayer()), t.GetStart().x, t.GetStart().y,
                        t.GetEnd().x, t.GetEnd().y, t.GetWidth(), t.GetNetname()])
    json.dump(out, open(out_json, "w", encoding="utf-8"))
    print(f"Dumped {len(out)} item(s)")


def merge(board_path, orig_json, routed_json):
    from _kicad import load_pcbnew
    pcbnew = load_pcbnew()
    items = new_items(json.load(open(orig_json, encoding="utf-8")), json.load(open(routed_json, encoding="utf-8")))
    b = pcbnew.LoadBoard(board_path)
    nets = b.GetNetsByName()
    for x in items:
        if x[0] == "via":
            t = pcbnew.PCB_VIA(b)
            t.SetPosition(pcbnew.VECTOR2I(x[1], x[2]))
            t.SetWidth(x[3])
            t.SetDrill(x[4])
        else:
            t = pcbnew.PCB_TRACK(b)
            t.SetLayer(b.GetLayerID(x[1]))
            t.SetStart(pcbnew.VECTOR2I(x[2], x[3]))
            t.SetEnd(pcbnew.VECTOR2I(x[4], x[5]))
            t.SetWidth(x[6])
        t.SetNet(nets[x[-1]])
        b.Add(t)
    pcbnew.SaveBoard(board_path, b)
    print(f"Merged {len(items)} item(s) (then run kicad-cli pcb drc --refill-zones --save-board to recheck)")


def _rule_area(board, pcbnew, name, layers, pts_mm):
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    z.SetDoNotAllowTracks(True)
    z.SetDoNotAllowVias(True)  # A rule area that only forbids tracks is not exported to the DSN (measured), vias must be forbidden at the same time
    z.SetDoNotAllowPads(False)
    z.SetDoNotAllowZoneFills(False)
    z.SetDoNotAllowFootprints(False)
    ls = pcbnew.LSET()
    for lid in layers:
        ls.AddLayer(lid)
    z.SetLayerSet(ls)
    z.SetZoneName(name)
    poly = pcbnew.SHAPE_POLY_SET()
    poly.NewOutline()
    for x, y in pts_mm:
        poly.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
    poly.thisown = 0
    z.SetOutline(poly)
    board.Add(z)


def make_copy(board_path, out_path, keepouts, gaps, edge):
    from _kicad import board_outline, load_pcbnew, zone_fills
    pcbnew = load_pcbnew()
    b = pcbnew.LoadBoard(board_path)
    mm = pcbnew.ToMM
    outline = board_outline(b, pcbnew)  # Must be taken before any modification
    outer = (pcbnew.F_Cu, pcbnew.B_Cu)
    # Read everything first, then modify: after modifying/deleting in SWIG, fetching objects again may give untyped SwigPyObject
    areas = {z.GetZoneName(): z.GetBoundingBox() for z in b.Zones() if z.GetIsRuleArea()}
    gap_polys = []
    for spec in gaps:
        plane, side = spec.split(":")
        gap_polys += [(side, g) for g in plane_gaps(outline, zone_fills(b, b.GetLayerID(plane), pcbnew))]
    for t in b.GetTracks():
        t.SetLocked(True)
    for f in b.GetFootprints():
        f.SetLocked(True)
    for name in keepouts:
        if name not in areas:
            raise SystemExit(f"Rule area {name} not found; existing: {sorted(areas)}")
        bb = areas[name]
        x0, y0, x1, y1 = mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())
        _rule_area(b, pcbnew, f"ROUTE_{name}", outer, ((x0, y0), (x1, y0), (x1, y1), (x0, y1)))
        print(f"Keepout ROUTE_{name}: x {x0:.2f}~{x1:.2f}, y {y0:.2f}~{y1:.2f}")
    for n, (side, g) in enumerate(gap_polys):
        for k, p in enumerate(getattr(g, "geoms", [g])):
            _rule_area(b, pcbnew, f"ROUTE_GAP_{n}_{k}", (b.GetLayerID(side),), list(p.simplify(0.02).exterior.coords)[:-1])
    print(f"Split-gap keepouts: {len(gap_polys)} piece(s)")
    if edge > 0:
        for k, p in enumerate(edge_band(outline, edge)):
            _rule_area(b, pcbnew, f"ROUTE_EDGE_{k}", outer, list(p.exterior.coords)[:-1])
        print(f"Board-edge {edge} mm band keepout")
    pcbnew.SaveBoard(out_path, b)
    print(f"Routing copy saved: {out_path}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("copy")
    c.add_argument("board")
    c.add_argument("out")
    c.add_argument("--keepout", nargs="*", default=[], help="Existing rule area names, copied as outer-layer track + via keepouts")
    c.add_argument("--gap", nargs="*", default=[], help="plane layer:outer layer, e.g. In2.Cu:B.Cu")
    c.add_argument("--edge-band", type=float, default=1.5)
    s = sub.add_parser("strip")
    s.add_argument("dsn")
    s.add_argument("out")
    s.add_argument("--nets", required=True, help="Nets whose pin lists are cleared (pour-connected power, frozen RF), comma-separated")
    s.add_argument("--plane-layers", default=None, help="Inner layers to change to power, comma-separated; standard names (In1.Cu) or DSN display names (GND); default all inner layers")
    d = sub.add_parser("dump")
    d.add_argument("board")
    d.add_argument("out")
    m = sub.add_parser("merge")
    m.add_argument("board")
    m.add_argument("orig")
    m.add_argument("routed")
    a = ap.parse_args(argv)
    if a.cmd == "copy":
        make_copy(a.board, a.out, a.keepout, a.gap, a.edge_band)
    elif a.cmd == "strip":
        txt = open(a.dsn, encoding="utf-8").read()
        try:
            layers = resolve_layers(txt, a.plane_layers.split(",")) if a.plane_layers else inner_layers(txt)
        except ValueError as e:
            raise SystemExit(str(e))
        txt = plane_layers(protect_wiring(strip_nets(txt, set(a.nets.split(",")))), layers)
        open(a.out, "w", encoding="utf-8").write(txt)
        print(f"Done: cleared pin lists of {a.nets}; existing copper protected; inner layers {layers} set to power")
    elif a.cmd == "dump":
        dump(a.board, a.out)
    else:
        merge(a.board, a.orig, a.routed)


if __name__ == "__main__":
    main()
