"""Turn corners > 45 degrees where two tracks meet (right and acute angles) into 45-degree polylines (SPEC-005 10.1), verify each change with DRC, and revert conflicting ones.

Method: pull back t on each side of the corner (<= 0.5 mm, and not more than 45% of the shorter segment) and replace it with the inscribed polyline of an arc tangent to both segments,
each turn <= 45 degrees (a 90-degree corner becomes one 45-degree diagonal), new segments take the narrower of the two widths. Only corners where "same net, same layer, exactly two segments meet" are changed;
by default locked tracks (frozen, pre-routed) and nets of non-Default net classes are skipped (differential pairs, RF, impedance traces: mitering each would change spacing and length,
handle them by hand with KiCad differential routing); --all-classes includes the latter;
corners whose point lies inside same-net via or pad copper are not changed (route_check does not count them either: the corner is covered by copper). The following are not changed and are listed for manual handling:
  - three or more segments meeting (acute gap: change to a T-junction or connect separately);
  - near doubling back (turn >= 170 degrees, mostly overlapping short stubs, run dedupe.py first).
Verification: after all changes run kicad-cli DRC (with zone refill, not saved); corners whose new segments add violations or unconnected items are reverted,
repeat until no new violations. DRC itself does not save the pours, so after the changes refill the pours per the project flow before saving.
Usage (KiCad's bundled python; close this board in the KiCad UI first):
  python miter.py <board.kicad_pcb> [--max 0.5] [--all-classes] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MAX_TURN_OK, SPIKE = 45.5, 170.0


def fillet(a, p, b, tmax=0.5):
    """Replacement polyline for corner a->p->b (pure function, mm). Returns [p1, ..., pk]: a->p1 ... pk->b replaces a->p->b; None if no change is needed or possible.

    p1 and pk are the tangent points of an arc tangent to both segments, intermediate points split the arc evenly; the segment count n keeps every turn <= 45 degrees:
    for a turn T <= 90 degrees n = 1 (one diagonal, T/2 at each end), otherwise n = ceil(T/45) (T/2n at the tangent points, T/n in between).
    """
    ux, uy = a[0] - p[0], a[1] - p[1]
    vx, vy = b[0] - p[0], b[1] - p[1]
    la, lb = math.hypot(ux, uy), math.hypot(vx, vy)
    if la < 1e-6 or lb < 1e-6:
        return None
    ux, uy, vx, vy = ux / la, uy / la, vx / lb, vy / lb
    alpha = math.degrees(math.acos(max(-1.0, min(1.0, ux * vx + uy * vy))))   # angle between the two segments
    turn = 180 - alpha
    if turn <= MAX_TURN_OK or turn >= SPIKE:
        return None
    t = min(tmax, 0.45 * la, 0.45 * lb)
    ta, tb = (p[0] + ux * t, p[1] + uy * t), (p[0] + vx * t, p[1] + vy * t)
    n = 1 if turn <= 90.5 else math.ceil(turn / 45 - 1e-9)
    if n == 1:
        return [ta, tb]
    half = math.radians(alpha) / 2
    r = t * math.tan(half)                       # arc radius for tangent length t
    wx, wy = ux + vx, uy + vy
    wl = math.hypot(wx, wy)
    d = r / math.sin(half)
    c = (p[0] + wx / wl * d, p[1] + wy / wl * d)
    fa = math.atan2(ta[1] - c[1], ta[0] - c[0])
    fb = math.atan2(tb[1] - c[1], tb[0] - c[0])
    sweep = (fb - fa + math.pi) % (2 * math.pi) - math.pi
    return [(c[0] + r * math.cos(fa + sweep * k / n), c[1] + r * math.sin(fa + sweep * k / n)) for k in range(n + 1)]


def corners(segs, blocked, tmax=0.5, nd=3):
    """segs: {id: (net, layer, width, (x0, y0), (x1, y1))}; blocked(net, layer, point) -> the point is inside same-net via/pad copper.

    Returns (changeable [(id1, end1, id2, end2, replacement polyline)], manual [(reason, net, layer, point)]), pure function (blocked is supplied by the caller).
    end = 0 means the segment's start is at the corner, 1 means its end.
    """
    ends = {}
    for k, (net, layer, w, s, e) in segs.items():
        if s == e:
            continue
        for end, pt in ((0, s), (1, e)):
            ends.setdefault((net, layer, round(pt[0], nd), round(pt[1], nd)), []).append((k, end))
    todo, manual = [], []
    for (net, layer, x, y), lst in ends.items():
        if len(lst) < 2:
            continue
        p = (x, y)
        if len(lst) > 2:
            continue                                     # Gaps are reported by route_check, not handled here
        (k1, e1), (k2, e2) = lst
        s1, s2 = segs[k1], segs[k2]
        far1 = s1[4] if e1 == 0 else s1[3]          # Far end: take the end point if the start is at the corner, else the start point
        far2 = s2[4] if e2 == 0 else s2[3]
        if blocked(net, layer, p):
            continue                                     # Covered by pad/via copper, not a corner
        poly = fillet(far1, p, far2, tmax)
        if poly is None:
            ux, uy = far1[0] - x, far1[1] - y
            vx, vy = far2[0] - x, far2[1] - y
            cosv = (ux * vx + uy * vy) / max(1e-9, math.hypot(ux, uy) * math.hypot(vx, vy))
            if 180 - math.degrees(math.acos(max(-1.0, min(1.0, cosv)))) >= SPIKE:
                manual.append(("doubled back", net, layer, p))
            continue
        todo.append((k1, e1, k2, e2, poly))
    return todo, manual


def run_drc(path):
    from _kicad import kicad_cli
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "drc.json")
        subprocess.run([kicad_cli(), "pcb", "drc", "--format", "json", "--severity-all", "--refill-zones",
                        "-o", out, path], capture_output=True)
        return json.load(open(out, encoding="utf-8"))


def main():
    from _kicad import load_pcbnew, origin
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("--max", type=float, default=0.5, help="Maximum length pulled back on each side of the corner, mm")
    ap.add_argument("--all-classes", action="store_true", help="Also change non-Default net classes (use with care on differential pairs and impedance traces)")
    ap.add_argument("--dry-run", action="store_true", help="List only, do not change the board")
    a = ap.parse_args()
    pcbnew = load_pcbnew()
    mm, FMM = pcbnew.ToMM, pcbnew.FromMM

    def plan(board):
        segs, objs = {}, {}
        for t in board.GetTracks():
            if t.Type() == pcbnew.PCB_TRACE_T:
                if t.IsLocked() or (not a.all_classes and t.GetNetClassName() != "Default"):
                    continue                     # Not put into segs: corners touching it are not changed
                k = t.m_Uuid.AsString()
                objs[k] = t
                segs[k] = (t.GetNetname(), t.GetLayer(), t.GetWidth(),
                           (mm(t.GetStart().x), mm(t.GetStart().y)), (mm(t.GetEnd().x), mm(t.GetEnd().y)))
        vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
        pads = list(board.GetPads())

        def blocked(net, layer, p):
            v = pcbnew.VECTOR2I(FMM(p[0]), FMM(p[1]))
            return any(x.GetNetname() == net and x.IsOnLayer(layer) and x.HitTest(v) for x in vias + pads)
        return segs, objs, corners(segs, blocked, a.max)

    def apply(board, objs, todo, skip):
        new = {}
        for i, (k1, e1, k2, e2, poly) in enumerate(todo):
            if i in skip:
                continue
            t1, t2 = objs[k1], objs[k2]
            p1, pk = poly[0], poly[-1]
            (t1.SetStart if e1 == 0 else t1.SetEnd)(pcbnew.VECTOR2I(FMM(p1[0]), FMM(p1[1])))
            (t2.SetStart if e2 == 0 else t2.SetEnd)(pcbnew.VECTOR2I(FMM(pk[0]), FMM(pk[1])))
            for q0, q1 in zip(poly, poly[1:]):
                s = pcbnew.PCB_TRACK(board)
                s.SetStart(pcbnew.VECTOR2I(FMM(q0[0]), FMM(q0[1])))
                s.SetEnd(pcbnew.VECTOR2I(FMM(q1[0]), FMM(q1[1])))
                s.SetWidth(min(t1.GetWidth(), t2.GetWidth()))
                s.SetLayer(t1.GetLayer())
                s.SetNet(t1.GetNet())
                board.Add(s)
                new[s.m_Uuid.AsString()] = i
            new[k1] = new[k2] = i
        return new

    b = pcbnew.LoadBoard(a.board)
    org = origin(b, pcbnew)
    rp = lambda p: f"({p[0] - org[0]:.2f}, {p[1] - org[1]:.2f})"  # noqa: E731
    segs, objs, (todo, manual) = plan(b)
    print(f"Corners that can be changed automatically: {len(todo)}; manual: {len(manual)}")
    for why, net, layer, p in manual:
        print(f"   manual: {why} {net} {b.GetLayerName(layer)} {rp(p)}")
    if a.dry_run or not todo:
        return

    base = run_drc(a.board)
    base_unc = len(base.get("unconnected_items", []))
    base_ids = {it.get("uuid") for v in base.get("violations", []) for it in v.get("items", [])}
    skip = set()
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, os.path.basename(a.board))
        for f in os.listdir(os.path.dirname(os.path.abspath(a.board))):
            if f.endswith((".kicad_pro", ".kicad_dru", ".kicad_sch")):
                shutil.copy(os.path.join(os.path.dirname(os.path.abspath(a.board)), f), td)
        for _ in range(5):
            bb = pcbnew.LoadBoard(a.board)
            _, objs2, (todo2, _) = plan(bb)
            new = apply(bb, objs2, todo2, skip)
            bb.Save(tmp)
            rep = run_drc(tmp)
            bad = {new[it.get("uuid")] for v in rep.get("violations", []) for it in v.get("items", [])
                   if it.get("uuid") in new and it.get("uuid") not in base_ids}
            unc = len(rep.get("unconnected_items", []))
            if unc > base_unc:            # Cannot locate a specific corner: revert by the tracks involved in the unconnected items
                bad |= {new[it.get("uuid")] for v in rep.get("unconnected_items", []) for it in v.get("items", [])
                        if it.get("uuid") in new}
                if not bad:
                    raise SystemExit(f"Unconnected items {base_unc} -> {unc}, cannot locate the corner, board not changed")
            if not bad:
                shutil.copy(tmp, a.board)
                print(f"Changed {len(todo2) - len(skip)}; reverted {len(skip)} because of DRC conflicts")
                for i in sorted(skip):
                    k1 = todo2[i][0]
                    net, layer, _, s, e = segs.get(k1, (None, 0, 0, (0, 0), (0, 0)))
                    print(f"   reverted: {net} {b.GetLayerName(layer)} corner near {rp(s if todo2[i][1] == 0 else e)}")
                print("After the change, refill the pours per the project flow and run DRC and route_check")
                return
            skip |= bad
    raise SystemExit("New DRC violations remain after 5 rounds, board not changed")


if __name__ == "__main__":
    main()
