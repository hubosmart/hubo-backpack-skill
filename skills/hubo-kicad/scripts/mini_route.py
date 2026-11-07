"""Patch the few connections left by Freerouting with grid A* (F.Cu / B.Cu, vias allowed) and write the result back to the board.

Why a script: MCP route_trace / add_via do not avoid obstacles, so patch coordinates must be computed first; here the path is searched against obstacles and all coordinates are generated from data
(hard rule 2). Avoids: other-net pads/tracks/vias (--clear + half track width; the local clearance when a pad or footprint sets a larger one),
rule areas (as set for track/via keepouts; areas that only carry .kicad_dru custom rules and forbid nothing are named with --block-area to be treated as full keepouts),
high-speed corridors (--hs/--corridor/--exempt), B-layer no-route boxes (--avoid, e.g. under a QFN thermal pad), board edge (--edge).
Usage (KiCad's bundled python):
  python mini_route.py <board.kicad_pcb> <net> <start ref.pin> [--width 0.15] [--grid 0.05]
         [--wide-nets +5V 1.1V ...] [--hs TMDS_ CSI_ --corridor 0.45 --exempt X0 Y0 X1 Y1 ...] [--avoid X0 Y0 X1 Y1 ...]
         [--block-area rule-area-name ...] [--via OD DRILL]
  Destination = all other copper of this net. Box coordinates are relative to the drill origin. --wide-nets: nets with larger clearance than default (e.g. PWR class 0.15), obstacles grown by 0.03.
  --via: via OD/drill (mm, default 0.45 0.25); used both for the avoidance radius and the size of newly placed vias, must match the project vias (e.g. 0.45 0.30 on some 8-layer boards).
  A failed search usually means the connection is enclosed by other-net copper: rip up locally first (ripup-style scripts), or route out a stub by hand and search again (pitfall in 07).
"""
import argparse
import heapq
import math
import os
import sys

from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.prepared import prep

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kicad import board_outline, boxes_abs, hs_corridors, load_pcbnew, origin  # noqa: E402

pcbnew = load_pcbnew()
mm = pcbnew.ToMM
LAYERS = (pcbnew.F_Cu, pcbnew.B_Cu)
VIA_DEFAULT = (0.45, 0.25)   # Via OD, drill (mm); override with --via when the project vias differ, do not reference it directly at module level


def rel(v):
    return (mm(v.x), mm(v.y))


def pad_poly(p):
    bb = p.GetBoundingBox()
    return box(mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom()))


def pad_clearance(default, pad_local, fp_local):
    """Clearance from a pad to other-net copper: pad-local clearance first, then footprint-local clearance (None/0 means unset); not less than the global default.
    KiCad protects pads such as antenna feed pins with local clearance; the DSN and older scripts do not read it, so patch tracks would hug the feed pin."""
    local = pad_local or fp_local
    return max(default, local or 0)


def rule_area_blocks(areas, layers, hw, via_r):
    """Rule areas -> keepout geometry. areas: [(polygon, layer set, no tracks, no vias)].
    Returns ({layer: [track-center no-go regions]}, [via-center no-go regions]); a via goes through all layers, so a no-via area on any layer blocks it."""
    track = {l: [] for l in layers}
    via = []
    for poly, on, no_track, no_via in areas:
        if no_track:
            for l in set(layers) & set(on):
                track[l].append(poly.buffer(hw))
        if no_via:
            via.append(poly.buffer(via_r))
    return track, via


def rule_areas(b, names):
    """Board rule areas -> input of rule_area_blocks; areas named in names are treated as no-track + no-via regardless of settings."""
    out, seen = [], set()
    for z in b.Zones():
        if not z.GetIsRuleArea():
            continue
        ol = z.Outline()
        on = {l for l in LAYERS if z.IsOnLayer(l)}
        forced = z.GetZoneName() in names
        seen.add(z.GetZoneName())
        for i in range(ol.OutlineCount()):
            o = ol.Outline(i)
            poly = Polygon([rel(o.CPoint(k)) for k in range(o.PointCount())]).buffer(0)
            out.append((poly, on, forced or z.GetDoNotAllowTracks(), forced or z.GetDoNotAllowVias()))
    missing = set(names) - seen
    if missing:
        raise SystemExit(f"Rule area {sorted(missing)} not found; existing: {sorted(seen)}")
    return out


def build(b, net, wide, clear):
    obst = {l: [] for l in LAYERS}
    mine = {l: [] for l in LAYERS}
    for f in b.GetFootprints():
        fl = f.GetLocalClearance()
        for p in f.Pads():
            poly = pad_poly(p)
            if p.GetNetname() != net:
                pl = p.GetLocalClearance()
                extra = pad_clearance(clear, mm(pl) if pl else None, mm(fl) if fl else None) - clear
                if extra > 0:
                    poly = poly.buffer(extra)
            for l in LAYERS:
                if p.IsOnLayer(l):
                    (mine if p.GetNetname() == net else obst)[l].append(poly.buffer(0.03) if p.GetNetname() in wide else poly)
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            g = Point(rel(t.GetPosition())).buffer(mm(t.GetWidth(pcbnew.F_Cu)) / 2)
            for l in LAYERS:
                (mine if t.GetNetname() == net else obst)[l].append(g)
        elif t.GetLayer() in LAYERS:
            g = LineString([rel(t.GetStart()), rel(t.GetEnd())]).buffer(mm(t.GetWidth()) / 2)
            (mine if t.GetNetname() == net else obst)[t.GetLayer()].append(g.buffer(0.03) if t.GetNetname() in wide else g)
    return obst, mine


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("net")
    ap.add_argument("src", help="Start pad ref.pin; destination = all other copper of this net")
    ap.add_argument("--width", type=float, default=0.15)
    ap.add_argument("--grid", type=float, default=0.1)
    ap.add_argument("--clear", type=float, default=0.125, help="Clearance to other-net copper (Default net class + rounding margin)")
    ap.add_argument("--edge", type=float, default=0.5, help="Distance to the board edge")
    ap.add_argument("--wide-nets", nargs="*", default=[])
    ap.add_argument("--hs", nargs="*", default=[])
    ap.add_argument("--corridor", type=float, default=0.0)
    ap.add_argument("--exempt", type=float, nargs=4, action="append", default=[])
    ap.add_argument("--avoid", type=float, nargs=4, action="append", default=[], metavar=("X0", "Y0", "X1", "Y1"),
                    help="B.Cu no-route boxes (relative to the drill origin), repeatable")
    ap.add_argument("--block-area", nargs="*", default=[], help="Rule area names treated as full keepouts (tracks + vias)")
    ap.add_argument("--via", type=float, nargs=2, default=list(VIA_DEFAULT), metavar=("OD", "DRILL"),
                    help="Via OD/drill mm: used for the avoidance radius and for new vias, must match the project via spec")
    return ap.parse_args(argv)


def make_via(b, netinfo, x_mm, y_mm, via_d, via_h):
    """Create a via with the given OD/drill (all via placement goes through here, so the size matches the parameters used for avoidance)."""
    via = pcbnew.PCB_VIA(b)
    via.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x_mm), pcbnew.FromMM(y_mm)))
    via.SetWidth(pcbnew.FromMM(via_d))
    via.SetDrill(pcbnew.FromMM(via_h))
    via.SetNet(netinfo)
    return via


def main():
    a = parse_args()
    via_d, via_h = a.via
    b = pcbnew.LoadBoard(a.board)
    org = origin(b, pcbnew)
    edge = board_outline(b, pcbnew).buffer(-a.edge)
    obst, mine = build(b, a.net, set(a.wide_nets), a.clear)
    cor = hs_corridors(b, pcbnew, a.hs, a.corridor, boxes_abs(a.exempt, org)) if a.corridor > 0 else {}
    CLR = a.clear
    hw = a.width / 2
    # Only grid points are checked, and diagonal segments would cut corners of keepouts: grow by 0.75 cell (>= cell/sqrt2) to compensate
    area_trk, area_via = rule_area_blocks(rule_areas(b, set(a.block_area)), LAYERS, hw + 0.75 * a.grid, via_d / 2)
    block = {}
    for l in LAYERS:
        g = unary_union(obst[l]).buffer(CLR + hw)
        if l in cor:
            g = g.union(cor[l].buffer(hw + 0.03))   # Corridor edges are measured to track center, so grow by half the track width; +0.03 offsets the grid diagonal error
        if l == pcbnew.B_Cu:
            for bx in boxes_abs(a.avoid, org):
                g = g.union(bx.buffer(hw))
        if area_trk[l]:
            g = g.union(unary_union(area_trk[l]))
        block[l] = prep(g)
    via_block = prep(unary_union(obst[pcbnew.F_Cu] + obst[pcbnew.B_Cu]).buffer(CLR + via_d / 2).union(
        unary_union(list(cor.values())).buffer(via_d / 2 + 0.01) if cor else Point(1e9, 1e9)).union(
        unary_union(area_via) if area_via else Point(1e9, 1e9)))   # Vias must not enter the corridor or no-via rule areas
    edge_p = prep(edge)
    ref, num = a.src.split(".")
    sp = next(p for p in b.FindFootprintByReference(ref).Pads() if p.GetNumber() == num)
    spoly = pad_poly(sp)
    # Connectivity analysis: merge this net's copper per layer by intersection; vias and pads appear on both layers and naturally join them
    items = [(l, g) for l in LAYERS for g in mine[l]]
    parent = list(range(len(items)))

    def fnd(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            li, gi = items[i]
            lj, gj = items[j]
            same_shape = gi.equals(gj)
            if (li == lj and gi.intersects(gj)) or (li != lj and same_shape):
                parent[fnd(i)] = fnd(j)
    roots = {fnd(i) for i, (l, g) in enumerate(items) if g.intersects(spoly) and sp.IsOnLayer(l)}
    src = [items[i] for i in range(len(items)) if fnd(i) in roots]
    dst = [items[i] for i in range(len(items)) if fnd(i) not in roots]
    if not dst:
        print(a.net, "already connected")
        return
    G = a.grid

    def q(v):
        return round(v / G)

    def free(x, y, l):
        p = Point(x * G, y * G)
        return edge_p.contains(p) and not block[l].contains(p)

    # Pre-merge the two groups per layer: deciding per point which group it falls in is the search hot spot, iterating geometry by geometry makes long path searches time out
    grp_u = {id(grp): {l: prep(unary_union([gg for gl, gg in grp if gl == l] or [Point(1e6, 1e6)])) for l in LAYERS}
             for grp in (src, dst)}

    def in_group(grp, x, y, l):
        return grp_u[id(grp)][l].contains(Point(x * G, y * G))

    # Start: a representative point of each geometry in the src group; destination test: falls inside the dst group
    starts = []
    for l, g in src:
        c = g.centroid
        starts.append((q(c.x), q(c.y), l))
    # The heuristic target is the target-group geometry nearest the start (it used to be fixed at dst[0], which misguided the search when the target group was large)
    s0 = src[0][1].centroid
    tx, ty = min((g for _, g in dst), key=lambda g: g.distance(s0)).representative_point().coords[0]
    dirs = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
    # State includes direction: every 45-degree turn adds cost BEND to avoid zigzag tracks; a layer change (via) costs VIA_COST
    BEND, VIA_COST = 3.0, 40.0
    openq, came, cost = [], {}, {}
    for s0 in starts:
        st = s0 + (-1,)
        cost[st] = 0
        heapq.heappush(openq, (0, st))
    goal = None
    n = 0
    while openq and n < 4_000_000:
        _, cur = heapq.heappop(openq)
        n += 1
        x, y, l, d = cur
        if in_group(dst, x, y, l) and d != -1:
            goal = cur
            break
        nb = []
        for k, (dx, dy) in enumerate(dirs):
            turn = 0 if d == -1 else min((k - d) % 8, (d - k) % 8)
            if turn > 2:
                continue
            nb.append(((x + dx, y + dy, l, k), math.hypot(dx, dy) + BEND * turn))
        other = LAYERS[1] if l == LAYERS[0] else LAYERS[0]
        nb.append(((x, y, other, -1 if d == -1 else d), VIA_COST))
        for nxt, c in nb:
            nx, ny, nl, _ = nxt
            if nl != l:
                if via_block.contains(Point(x * G, y * G)) or in_group(src, x, y, l):   # No via-in-pad
                    continue
            elif not free(nx, ny, nl) and not in_group(src, nx, ny, nl) and not in_group(dst, nx, ny, nl):
                continue
            nc = cost[cur] + c
            if nc < cost.get(nxt, 1e18):
                cost[nxt] = nc
                came[nxt] = cur
                h = math.hypot(nx * G - tx, ny * G - ty) / G
                heapq.heappush(openq, (nc + h, nxt))
    if goal is None:
        print("No path found", n)
        sys.exit(1)
    path = [goal]
    while path[-1] in came:
        path.append(came[path[-1]])
    path.reverse()
    path = [p[:3] for p in path]
    # Merge collinear points, output
    netinfo = b.FindNet(a.net)
    segs, vias = [], []
    run = [path[0]]
    for p in path[1:]:
        if p[2] != run[-1][2]:
            segs.append(run)
            vias.append(p)
            run = [p]
        else:
            run.append(p)
    segs.append(run)
    total = 0
    for run in segs:
        pts = [run[0]]
        for k in range(1, len(run) - 1):
            d1 = (run[k][0] - pts[-1][0], run[k][1] - pts[-1][1])
            d2 = (run[k + 1][0] - run[k][0], run[k + 1][1] - run[k][1])
            if d1[0] * d2[1] - d1[1] * d2[0] != 0:
                pts.append(run[k])
        pts.append(run[-1])
        for s, e in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(b)
            t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(s[0] * G), pcbnew.FromMM(s[1] * G)))
            t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(e[0] * G), pcbnew.FromMM(e[1] * G)))
            t.SetWidth(pcbnew.FromMM(a.width))
            t.SetLayer(s[2])
            t.SetNet(netinfo)
            b.Add(t)
            total += 1
    for v in vias:
        b.Add(make_via(b, netinfo, v[0] * G, v[1] * G, via_d, via_h))
    b.Save(a.board)
    print(f"{a.net}: {total} segment(s), {len(vias)} via(s), searched {n} step(s)")


if __name__ == "__main__":
    main()
