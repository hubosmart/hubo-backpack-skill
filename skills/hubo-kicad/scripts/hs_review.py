"""High-speed differential pair geometry review: everything other than trace width/gap (impedance_check.py) and length, with the measured value for each item.

  1 Corners: turn between adjacent segments <= 45 degrees (interior angle >= 135 degrees)
  2 Ground via near layer-change vias: distance from each differential via to the nearest GND via / through-hole pad
  3 Pour clearance: minimum distance from same-layer GND fill to the differential trace (--zone-clear prefix=mm gives the requirement)
  4 Keep away from aggressors: edge distance to copper of given nets (--keep-from net,net=mm); distance to pads of given parts (--far ref,ref=mm)
  5 GND between pairs: outside the fan-out area, is there same-layer GND fill between two adjacent pairs of one interface (midpoint of the inner traces) (--gnd-between prefix)
  6 Reference plane: is the adjacent layer directly below the differential trace (4-layer: F->In1, B->In2) a GND fill; count the unsupported length (listed separately inside --exempt)
  7 Differential vias: both traces have the same via count (SPEC-005 2.3); per-trace via limit (--max-vias, USB high speed uses 2)
  8 Intra-pair length difference: P/N total length difference (--skew prefix=mil, USB high speed 5, MIPI 10); --skew-target pair=mil gives a P-N target, judged by the difference from the target
    (for when the intra-pair skew must replicate a fixed target; state target and basis in the design document)
  9 Test points: parts whose ref starts with TP and whose pad is on a high-speed net (SPEC-005 2.6)
 10 No-via nets: given nets must have no vias (--no-via, crystal clock, RF lines)
 11 Crystal distance: minimum distance from the crystal pad to the chip pad on the same net (clock pin) (--xtal crystal,chip=mm, P4 4.5, S3 2.0)
 12 Via stub: Gbps-class differential (MIPI, HDMI) must not change layers within a pair; for a through via connected to only one outer layer the stub is counted by board thickness (--stub prefix=mil, SPEC-005 2.8 uses 15)
 13 Pair-to-pair length difference: maximum difference of mean lengths of the pairs of one interface (including layer-change via barrels, one board thickness per via) (--pair-skew prefix=mil, SPEC-005 appendix C; MIPI pair-to-pair is the stricter 30)
Usage (KiCad's bundled python; fill the pours first):
  python hs_review.py <board.kicad_pcb> --hs TMDS_ CSI_ [--gnd GND] [--via-gnd 1.0]
         [--zone-clear TMDS_=0.432 CSI_=0.508] [--keep-from XTALI,XTALO=0.65] [--far U1,L3=7]
         [--gnd-between CSI_] [--exempt X0 Y0 X1 Y1 ...] [-v]
         [--max-vias 2] [--skew USB_=5 CSI_=10] [--skew-target HDMI_D1=-61.6] [--no-via XTAL_P XTAL_N RF_ANT] [--xtal Y1,U1=4.5]
         [--stub TMDS_=15 CSI_=15] [--pair-skew CSI_=30 TMDS_=30]
Box coordinates are relative to the drill origin. See references/08_impedance_control.md for usage examples.
Limits are sourced in SPEC-005 chapter 9. A line containing "NOT MET" means failure.
"""
import argparse
import math
import os
import sys

from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kicad import boxes_abs, load_pcbnew, origin  # noqa: E402
from impedance_check import pair_of  # noqa: E402
from route_check import turns  # noqa: E402


def kv(items):
    out = []
    for it in items:
        k, v = it.rsplit("=", 1)
        out.append((k.split(","), float(v)))
    return out


MIL = 0.0254


def pair_report(vias, lengths, max_vias=None, skew=(), target=None):
    """Differential via count and intra-pair length difference judgement (pure function, self-testable).

    vias / lengths: {net name: via count} / {net name: total trace length mm}; skew: [(prefix, limit mil)];
    target: {pair name: P-N target mil}; pairs with a target are judged by |P-N - target| (for when the intra-pair skew must replicate a fixed target).
    Returns a list of output lines; failing lines contain "NOT MET".
    """
    target = target or {}
    out = []
    for n in sorted(lengths):
        pr = pair_of(n)
        if not pr or pr[1] not in lengths:
            continue
        name, nn = pr
        vp, vn = vias.get(n, 0), vias.get(nn, 0)
        bad = vp != vn or (max_vias is not None and max(vp, vn) > max_vias)
        lim = f", limit {max_vias}" if max_vias is not None else ""
        out.append(f"7 {name} vias P {vp} / N {vn}{lim}{' NOT MET' if bad else ''}")
        for pre, mil in skew:
            if n.startswith(pre):
                pn = (lengths[n] - lengths[nn]) / MIL
                if name in target:
                    d = abs(pn - target[name])
                    out.append(f"8 {name} length P {lengths[n]:.3f} / N {lengths[nn]:.3f} mm, P-N {pn:+.1f} mil, "
                               f"target {target[name]:+.1f}, off by {d:.1f} mil (required <= {mil:g}){'' if d <= mil else ' NOT MET'}")
                else:
                    d = abs(pn)
                    out.append(f"8 {name} length P {lengths[n]:.3f} / N {lengths[nn]:.3f} mm, off by {d:.1f} mil"
                               f" (required <= {mil:g}){'' if d <= mil else ' NOT MET'}")
                break
    return out


def stub_report(vias, limits):
    """Through-via stub judgement (pure function). vias: [(net name, position text, number of outer layers connected, board thickness mm)]; limits: [(prefix, limit mil)].

    For a through via connected to only one outer layer, the whole barrel on the other side is a stub, counted by board thickness (inner-layer traces are not on high-speed lines of a 4-layer board, not counted separately); 0 if both outer layers are connected.
    Vias with 0 outer layers connected (connected through the pad only) are not judged. Failing lines contain "NOT MET".
    """
    out = []
    for n, where, outer, thick in vias:
        for pre, mil in limits:
            if n.startswith(pre) and outer > 0:
                s = 0.0 if outer >= 2 else thick / MIL
                out.append(f"12 {n} via {where} stub {s:.0f} mil (required <= {mil:g}){'' if s <= mil else ' NOT MET'}")
                break
    return out


def pair_gap_report(lengths, limits):
    """Pair-to-pair length difference judgement (pure function). Each pair's length is the mean of P and N; pairs with the same prefix take the maximum difference."""
    out = []
    for pre, mil in limits:
        per = {}
        for n in sorted(lengths):
            pr = pair_of(n)
            if pr and n.startswith(pre) and pr[1] in lengths:
                per[pr[0]] = (lengths[n] + lengths[pr[1]]) / 2
        if len(per) < 2:
            continue
        hi, lo = max(per, key=per.get), min(per, key=per.get)
        d = (per[hi] - per[lo]) / MIL
        out.append(f"13 {pre} pair-to-pair length difference {d:.1f} mil ({hi} longer than {lo}; required <= {mil:g}){'' if d <= mil else ' NOT MET'}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--hs", nargs="+", required=True)
    ap.add_argument("--gnd", default="GND")
    ap.add_argument("--zone-clear", nargs="*", default=[])
    ap.add_argument("--keep-from", nargs="*", default=[])
    ap.add_argument("--far", nargs="*", default=[])
    ap.add_argument("--gnd-between", nargs="*", default=[])
    ap.add_argument("--exempt", type=float, nargs=4, action="append", default=[])
    ap.add_argument("-v", action="store_true")
    ap.add_argument("--via-gnd", type=float, default=1.0, help="Maximum distance from a layer-change via to the nearest ground via, mm (SPEC-003 6.2.5)")
    ap.add_argument("--max-vias", type=int, help="Maximum vias per differential trace")
    ap.add_argument("--skew", nargs="*", default=[], help="prefix=maximum intra-pair length difference, mil")
    ap.add_argument("--skew-target", nargs="*", default=[],
                    help="pair=intra-pair P-N target mil (signed; pairs with a target are judged by |P-N - target| against the --skew limit)")
    ap.add_argument("--no-via", nargs="*", default=[], help="Net names that must have no vias")
    ap.add_argument("--xtal", nargs="*", default=[], help="crystal ref,chip ref=minimum distance mm")
    ap.add_argument("--stub", nargs="*", default=[], help="prefix=maximum via stub, mil (Gbps-class differential)")
    ap.add_argument("--pair-skew", nargs="*", default=[], help="prefix=maximum pair-to-pair length difference, mil")
    a = ap.parse_args()
    pcbnew = load_pcbnew()
    mm = pcbnew.ToMM
    b = pcbnew.LoadBoard(a.board)
    org = origin(b, pcbnew)
    pos = lambda v: (mm(v.x), mm(v.y))  # noqa: E731
    rp = lambda p: f"({p[0] - org[0]:.2f}, {p[1] - org[1]:.2f})"  # noqa: E731
    fanout = unary_union(boxes_abs(a.exempt, org)) if a.exempt else Polygon()
    lines, widths, vias, lengths = {}, {}, [], {}
    through = []     # Through vias: (net, position), for item 12
    for t in b.GetTracks():
        n = t.GetNetname()
        if t.Type() == pcbnew.PCB_VIA_T:
            vias.append((n, pos(t.GetPosition())))
            if t.GetViaType() == pcbnew.VIATYPE_THROUGH:
                through.append((n, pos(t.GetPosition())))
        elif n.startswith(tuple(a.hs)):
            lines.setdefault(n, []).append((t.GetLayer(), LineString([pos(t.GetStart()), pos(t.GetEnd())])))
            widths[n] = mm(t.GetWidth())
            lengths[n] = lengths.get(n, 0.0) + mm(t.GetLength())     # Arcs are counted by arc length

    # 1 Corners (same algorithm as route_check.py)
    worst = [(ang, n, rp(p)) for kind, ang, n, _, p in turns([(n, l, *g.coords) for n, ls in lines.items() for l, g in ls])
             if kind != "acute gap"]
    print(f"1 corners > 45 degrees: {len(worst)}", sorted(worst, reverse=True)[:6])

    # 2 Ground via near layer-change vias
    gv = [p for n, p in vias if n == a.gnd]
    gv += [pos(p.GetPosition()) for f in b.GetFootprints() for p in f.Pads()
           if p.GetNetname() == a.gnd and p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH]
    for n, p in vias:
        if n.startswith(tuple(a.hs)) and gv:
            d = min(math.dist(p, g) for g in gv)
            print(f"2 {n} via {rp(p)} nearest ground via {d:.2f} mm (required <= {a.via_gnd:g}){'' if d <= a.via_gnd else ' NOT MET'}")

    # GND fill
    fills = {}
    for z in b.Zones():
        if z.GetNetname() != a.gnd or z.GetIsRuleArea():
            continue
        for lid in b.GetEnabledLayers().CuStack():
            if not z.IsOnLayer(lid):
                continue
            ps = pcbnew.SHAPE_POLY_SET(z.GetFilledPolysList(lid))
            ps.Unfracture()
            polys = []
            for i in range(ps.OutlineCount()):
                o = ps.Outline(i)
                hs_ = [[pos(ps.Hole(i, h).CPoint(k)) for k in range(ps.Hole(i, h).PointCount())] for h in range(ps.HoleCount(i))]
                polys.append(Polygon([pos(o.CPoint(k)) for k in range(o.PointCount())], hs_).buffer(0))
            fills[lid] = unary_union(polys + ([fills[lid]] if lid in fills else []))

    # 3 Pour clearance
    for pre, lim in kv(a.zone_clear):
        ds = [g.distance(fills[l]) - widths[n] / 2 for n, ls in lines.items() if n.startswith(tuple(pre))
              for l, g in ls if l in fills]
        if ds:
            d = min(ds)
            print(f"3 {'/'.join(pre)} minimum to same-layer GND fill {d:.3f} mm (required >= {lim:.3f}){'' if d >= lim - 0.001 else ' NOT MET'}")

    # 4 Aggressors
    allhs = unary_union([g.buffer(widths[n] / 2) for n, ls in lines.items() for _, g in ls])

    def copper(nets):
        gs = [LineString([pos(t.GetStart()), pos(t.GetEnd())]).buffer(mm(t.GetWidth()) / 2)
              for t in b.GetTracks() if t.GetNetname() in nets and t.Type() != pcbnew.PCB_VIA_T]
        for f in b.GetFootprints():
            for p in f.Pads():
                if p.GetNetname() in nets:
                    bb = p.GetBoundingBox()
                    gs.append(box(mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())))
        return unary_union(gs)
    for nets, lim in kv(a.keep_from):
        d = allhs.distance(copper(set(nets)))
        print(f"4 minimum edge distance to {','.join(nets)} {d:.2f} mm (required >= {lim:.2f}){'' if d >= lim else ' NOT MET'}")
    for refs, lim in kv(a.far):
        for ref in refs:
            f = b.FindFootprintByReference(ref)
            pads = unary_union([box(mm(p.GetBoundingBox().GetLeft()), mm(p.GetBoundingBox().GetTop()),
                                    mm(p.GetBoundingBox().GetRight()), mm(p.GetBoundingBox().GetBottom())) for p in f.Pads()])
            d = allhs.distance(pads)
            print(f"4 to pads of {ref} {d:.2f} mm (required >= {lim:.2f}){'' if d >= lim else ' NOT MET'}")

    # 5 GND between pairs
    for pre in a.gnd_between:
        pairs = {}
        for n, ls in lines.items():
            pr = pair_of(n)
            if n.startswith(pre) and pr and pr[1] in lines:
                pairs[pr[0]] = {"P": ls, "N": lines[pr[1]]}
        names = sorted(pairs)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                A, B = names[i], names[j]
                for lay in fills:
                    aP = [g for l, g in pairs[A]["P"] if l == lay]
                    aN = unary_union([g for l, g in pairs[A]["N"] if l == lay])
                    bAll = unary_union([g for k in ("P", "N") for l, g in pairs[B][k] if l == lay])
                    if not aP or aN.is_empty or bAll.is_empty:
                        continue
                    tot = cov = 0.0
                    miss = []
                    for g in aP:
                        ns = max(1, int(g.length / 0.1))
                        for k in range(ns + 1):
                            p = g.interpolate(k / ns, normalized=True)
                            pn = aN.interpolate(aN.project(p))
                            inner = min((p, pn), key=lambda x: bAll.distance(x))
                            q = bAll.interpolate(bAll.project(inner))
                            gap = inner.distance(q)
                            m = Point((inner.x + q.x) / 2, (inner.y + q.y) / 2)
                            if gap > 2.5 or gap < 0.3 or fanout.contains(m) or allhs.contains(m):
                                continue
                            tot += g.length / ns
                            hit = fills[lay].contains(m)
                            cov += g.length / ns if hit else 0
                            if not hit:
                                miss.append(rp((m.x, m.y)))
                    if tot > 0.5:
                        print(f"5 {A}-{B} {b.GetLayerName(lay)} parallel {tot:.1f} mm: GND between {cov / tot * 100:.0f}%")
                        if miss and a.v:
                            print("   where GND is missing:", sorted(set(miss))[:: max(1, len(set(miss)) // 20)])

    # 6 Reference plane
    cu = list(b.GetEnabledLayers().CuStack())
    ref_of = {cu[0]: cu[1], cu[-1]: cu[-2]} if len(cu) >= 4 else {}
    bad_any = False
    for n in sorted(lines):
        bad = ex = 0.0
        for l, g in lines[n]:
            r = fills.get(ref_of.get(l))
            if r is None:
                continue
            miss = g.difference(r)
            if not miss.is_empty:
                bad += miss.difference(fanout).length
                ex += miss.intersection(fanout).length
        if bad > 0.05:
            bad_any = True
            print(f"6 {n}: reference layer unsupported {bad:.2f} mm (another {ex:.2f} mm inside exempt areas)")
    print("6 reference plane: " + ("see above" if bad_any else "complete everywhere outside exempt areas" if ref_of else "board has fewer than 4 layers, not checked"))

    # 7, 8 Differential vias and intra-pair length difference
    vcount = {}
    for n, _ in vias:
        vcount[n] = vcount.get(n, 0) + 1
    skew = [(pre, lim) for pres, lim in kv(a.skew) for pre in pres]
    target = {n: v for ns, v in kv(a.skew_target) for n in ns}
    for line in pair_report(vcount, lengths, a.max_vias, skew, target):
        print(line)

    # 12 Via stub, 13 pair-to-pair length difference
    if a.stub:
        outer_ids = {cu[0], cu[-1]}
        thick = mm(b.GetDesignSettings().GetBoardThickness())
        stub_vias = []
        for n, p in through:
            used = {l for l, g in lines.get(n, []) if min(math.dist(p, c) for c in g.coords) < 0.05}
            stub_vias.append((n, rp(p), len(used & outer_ids), thick))
        for line in stub_report(stub_vias, [(pre, lim) for pres, lim in kv(a.stub) for pre in pres]):
            print(line)
    # Pair-to-pair length difference includes the layer-change via barrel length (one board thickness per via); otherwise pairs compensating length with layer-change vias would be misjudged
    thick_all = mm(b.GetDesignSettings().GetBoardThickness())
    total = {n: l + vcount.get(n, 0) * thick_all for n, l in lengths.items()}
    for line in pair_gap_report(total, [(pre, lim) for pres, lim in kv(a.pair_skew) for pre in pres]):
        print(line)

    # 9 Test points
    tps = sorted({f"{f.GetReference()}({p.GetNetname()})" for f in b.GetFootprints() if f.GetReference().upper().startswith("TP")
                  for p in f.Pads() if p.GetNetname().startswith(tuple(a.hs))})
    print(f"9 test points on high-speed nets: {len(tps)}{' NOT MET ' + ', '.join(tps) if tps else ''}")

    # 10 No-via nets
    for n in a.no_via:
        k = vcount.get(n, 0)
        print(f"10 {n} vias {k}{' NOT MET' if k else ''}")

    # 11 Crystal to chip clock pin
    for refs, lim in kv(a.xtal):
        xf, cf = (b.FindFootprintByReference(r) for r in refs)
        if xf is None or cf is None:
            print(f"11 ref {','.join(refs)} not found NOT MET")
            continue
        xnets = {p.GetNetname() for p in xf.Pads() if p.GetNetname() and p.GetNetname() != a.gnd}
        # A series resistor is often between the crystal and the chip (Espressif requires it close to the chip): the net across one two-pin part also counts as a clock line;
        # the other end of the load capacitor is GND, which is not merged in
        direct = set(xnets)
        for f in b.GetFootprints():
            ns = [p.GetNetname() for p in f.Pads()]
            if f.GetReference() != refs[1] and len(ns) == 2 and a.gnd not in ns and (ns[0] in direct) != (ns[1] in direct):
                xnets.update(n for n in ns if n)
        pbox = lambda p: box(mm(p.GetBoundingBox().GetLeft()), mm(p.GetBoundingBox().GetTop()),  # noqa: E731
                             mm(p.GetBoundingBox().GetRight()), mm(p.GetBoundingBox().GetBottom()))
        cpads = [pbox(p) for p in cf.Pads() if p.GetNetname() in xnets]
        if not cpads:
            print(f"11 no clock pin of {refs[1]} connected to {refs[0]} (directly or through one two-pin series part) NOT MET")
            continue
        d = unary_union([pbox(p) for p in xf.Pads()]).distance(unary_union(cpads))
        print(f"11 {refs[0]} to clock pin of {refs[1]} {d:.2f} mm (required >= {lim:g}){'' if d >= lim else ' NOT MET'}")


if __name__ == "__main__":
    main()
