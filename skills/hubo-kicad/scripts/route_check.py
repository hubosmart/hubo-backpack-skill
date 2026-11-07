"""Basic routing checks (all nets): geometry items DRC does not check, corresponding to SPEC-005 chapter 10.

  1 Corners (10.1): turn > 45 degrees where two tracks meet (right angle, acute angle); angle between two tracks < 90 degrees where three or more meet (acute gap).
    Junctions inside same-net pad or via copper are not counted: the corner is covered by pad/via copper, no track-to-track angle is visible on the board
  2 Via-in-pad (10.7): a via lands inside an SMD pad; same-net thermal/ground vias with drill <= 0.3 mm on a chip's bottom thermal pad are waived under the 10.7 exception,
    listed separately for review
  3 Pad overlap (10.12): pads of different components with edge-to-edge distance < 0.1 mm (including overlapping and shared), the same net is no exception - DRC does not check same-net pad spacing,
    two overlapping components cannot be assembled together
Track width and clearance, drill size, board edge and silkscreen are left to KiCad DRC (rule template assets/jlc_base.kicad_dru); power-net track width (10.8)
is generated as DRC rules by power_rules.py from the project config.
Usage (KiCad's bundled python):
  python route_check.py <board.kicad_pcb> [-v] [--vip U1 U2]   # --vip: BGAs ordered with the via-in-pad process, same-net vias in their pads are waived
A line containing "NOT MET" means failure; the exit code is 1 when anything fails. Coordinates are relative to the drill origin.
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def turns(segs, nd=3):
    """segs: [(net, layer, (x0, y0), (x1, y1))], mm. Returns [(category, angle in degrees, net, layer, (x, y))], pure function.

    Segments of the same net and layer whose endpoints coincide (to nd decimals) are joined:
      two segments: turn = 180 - angle between them; > 45 is recorded as "right angle" or (> 90) "acute angle"
      three or more: any two segments with angle < 90 are recorded as "acute gap" (a T-junction at 90 does not count)
    """
    ends = {}
    for net, layer, a, b in segs:
        if a == b:
            continue
        for p, q in ((a, b), (b, a)):
            key = (net, layer, round(p[0], nd), round(p[1], nd))
            ends.setdefault(key, []).append(math.atan2(q[1] - p[1], q[0] - p[0]))
    out = []
    for (net, layer, x, y), angs in ends.items():
        if len(angs) < 2:
            continue
        gaps = [math.degrees(abs((angs[i] - angs[j] + math.pi) % (2 * math.pi) - math.pi))
                for i in range(len(angs)) for j in range(i + 1, len(angs))]
        if len(angs) == 2:
            turn = 180 - gaps[0]
            if turn > 90.5:
                out.append(("acute angle", round(turn, 1), net, layer, (x, y)))
            elif turn > 45.5:
                out.append(("right angle", round(turn, 1), net, layer, (x, y)))
        elif min(gaps) < 89.5:
            out.append(("acute gap", round(min(gaps), 1), net, layer, (x, y)))
    return out


EP_MIN_MM, EP_MIN_PADS, EP_MAX_DRILL = 1.0, 8, 0.3


PAD_GAP_MM = 0.1


def pad_overlaps(pads, gap=PAD_GAP_MM):
    """Overlap / too-close check for pads of different components (pure function, mm).

    pads: [(ref, pad number, layer set, (x0, y0, x1, y1))], the layer set holds the copper layer names the pad is on (through-hole pads: all layers).
    Returns [(ref A, pad A, ref B, pad B, distance mm)]; recorded when distance < gap, overlap records 0. Pads within the same component are not compared.
    """
    out = []
    for i in range(len(pads)):
        ra, na, la, a = pads[i]
        for j in range(i + 1, len(pads)):
            rb, nb, lb, b = pads[j]
            if ra == rb or not (la & lb):
                continue
            dx = max(a[0] - b[2], b[0] - a[2], 0.0)
            dy = max(a[1] - b[3], b[1] - a[3], 0.0)
            d = math.hypot(dx, dy)
            if d < gap - 1e-9:
                out.append((ra, na, rb, nb, d))
    return out


def ep_exempt(via_net, via_drill, pad_net, pad_size, n_pads, largest):
    """Whether a via inside a pad falls under the 10.7 exception "thermal/ground vias on a chip's bottom thermal pad" (pure function, mm).

    What counts as a thermal pad: the footprint has >= 8 pads, this is the largest SMD pad in it, short side >= 1.0 mm - this excludes the pads of resistors/capacitors and
    two- or three-pin parts (vias on such pads must be moved out). The via must also be on the pad's net (signal vias and other-net vias are not waived) and drill <= 0.3 mm
    (the smaller the hole the less solder wicks away, TI SLUA271C).
    """
    return (bool(via_net) and via_net == pad_net and via_drill <= EP_MAX_DRILL + 1e-6 and largest
            and n_pads >= EP_MIN_PADS and min(pad_size) >= EP_MIN_MM - 1e-6)


def vip_exempt(via_net, pad_net, ref, vip_refs):
    """Whether a via inside a pad is waived by the via-in-pad process (pure function): the part is in the --vip list (fine-pitch BGA, via-in-pad process chosen when ordering and stated in the design document)
    and the via is on the pad's net."""
    return bool(via_net) and via_net == pad_net and ref in vip_refs


def main():
    from _kicad import load_pcbnew, origin
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("-v", action="store_true", help="List all positions (default: first 10 per category)")
    ap.add_argument("--vip", nargs="*", default=[], help="Refs of BGAs ordered with the via-in-pad process: same-net vias on their pads are waived (state in the design document)")
    a = ap.parse_args()
    pcbnew = load_pcbnew()
    mm = pcbnew.ToMM
    b = pcbnew.LoadBoard(a.board)
    org = origin(b, pcbnew)
    rp = lambda p: f"({p[0] - org[0]:.2f}, {p[1] - org[1]:.2f})"  # noqa: E731
    pos = lambda v: (mm(v.x), mm(v.y))  # noqa: E731
    bad = 0

    segs, vias = [], []
    for t in b.GetTracks():
        n = t.GetNetname()
        if t.Type() == pcbnew.PCB_VIA_T:
            vias.append((n, t.GetPosition(), mm(t.GetDrillValue())))
        elif t.Type() == pcbnew.PCB_TRACE_T:          # An arc has no corner itself, not involved
            segs.append((n, b.GetLayerName(t.GetLayer()), pos(t.GetStart()), pos(t.GetEnd())))

    # 1 Corners
    covers = [t for t in b.GetTracks() if t.Type() == pcbnew.PCB_VIA_T] + list(b.GetPads())

    def covered(net, layer, p):
        v = pcbnew.VECTOR2I(pcbnew.FromMM(p[0]), pcbnew.FromMM(p[1]))
        lid = b.GetLayerID(layer)
        return any(c.GetNetname() == net and c.IsOnLayer(lid) and c.HitTest(v) for c in covers)
    found = [x for x in turns(segs) if not covered(x[2], x[3], x[4])]
    for kind in ("acute angle", "right angle", "acute gap"):
        hits = sorted((x for x in found if x[0] == kind), key=lambda x: -x[1] if kind != "acute gap" else x[1])
        bad += bool(hits)
        print(f"1 {kind}: {len(hits)}{' NOT MET' if hits else ''}")
        for _, ang, n, layer, p in hits if a.v else hits[:10]:
            print(f"   {n} {layer} {rp(p)} {ang}°")

    # 2 Via-in-pad
    hits, exempt, vip = [], [], []
    vip_refs = set(a.vip)
    for f in b.GetFootprints():
        smd = [p for p in f.Pads() if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD]
        big = max(smd, key=lambda p: p.GetSize().x * p.GetSize().y, default=None)
        for p in smd:
            for n, v, drill in vias:
                if not p.HitTest(v):
                    continue
                size = (mm(p.GetSize().x), mm(p.GetSize().y))
                if vip_exempt(n, p.GetNetname(), f.GetReference(), vip_refs):
                    vip.append((n, v, f.GetReference()))
                    continue
                ok = ep_exempt(n, drill, p.GetNetname(), size, len(f.Pads()), p is big)
                (exempt if ok else hits).append((n, v, f.GetReference()))
    bad += bool(hits)
    print(f"2 via-in-pad: {len(hits)}{' NOT MET (move signal vias out of pads; if unavoidable, order with the via-in-pad process and state it in the design document)' if hits else ''}")
    for n, v, ref in hits if a.v else hits[:10]:
        print(f"   {n} via {rp(pos(v))} on a pad of {ref}")
    if exempt:
        refs = sorted({r for *_, r in exempt})
        print(f"   Also {len(exempt)} thermal-pad ground via(s) waived under the 10.7 exception: {', '.join(refs)}")
    if vip:
        refs = sorted({r for *_, r in vip})
        print(f"   Also {len(vip)} BGA same-net via-in-pad(s) waived under the via-in-pad process (--vip; order with via-in-pad and state it in the design document): {', '.join(refs)}")

    # 3 Pad overlap (bounding rectangle, slightly conservative)
    pads = []
    for f in b.GetFootprints():
        for p in f.Pads():
            bb = p.GetBoundingBox()
            layers = {b.GetLayerName(l) for l in p.GetLayerSet().CuStack()}
            pads.append((f.GetReference(), p.GetNumber(), layers, (mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom()))))
    hits = pad_overlaps(pads)
    bad += bool(hits)
    print(f"3 pad overlap: {len(hits)}{' NOT MET (pads of different components must be >= 0.1 mm edge to edge, the same net is no exception; move the parts apart or change the footprint)' if hits else ''}")
    for ra, na, rb, nb, d in hits if a.v else hits[:10]:
        print(f"   {ra}.{na} and {rb}.{nb} distance {d:.3f} mm")

    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
