"""High-speed differential guard-ground via check (SPEC-005 2.9): guard copper must be tied to the reference ground with vias along its whole length; grounding at the two ends only is forbidden.

For each differential net (_P/_N or +/- suffix) sample along the trace every --step mm, looking only at the outer side (the side facing away from the other trace of the pair):
  - Same-layer GND fill within --probe mm on the outer side -> the point is "guarded", record the pour island; otherwise "unguarded" (length only counted as a hint,
    matching 2.9 "narrow guard copper with no room for ground vias should be deleted and replaced with >= 3W"; 3W is handled by hs_review)
  - Within a continuous guarded run on the same layer and the same pour island, project GND vias / GND through-hole pads onto the trace: lateral distance <= --radius,
    on the outer side, and the via sits on that pour island to count as a ground via of the run; if the maximum along-trace gap between run start, adjacent vias and run end exceeds the
    target spacing it is "relaxed" (hint only); only above the limit spacing it is "NOT MET". A run with no via is "pour island without ground via", vias bunched at both ends is "grounded at both ends only"
  - Sample points inside --exempt boxes are not judged (chip fan-out areas, connector and ESD pad areas, listed in the design document)
Two spacing tiers (SPEC-005 2.9): target lambda/20 (lambda from 2.5x the bit rate) - MIPI 3, HDMI TMDS 4, USB 2.0 HS 6 mm;
when space is tight it may relax up to the limit lambda/10 (default 2x the target); only above the limit is it a violation. See references/09 section 3.
Usage (KiCad's bundled python; fill the pours first):
  python fence_check.py <board.kicad_pcb> --net CSI_=3 TMDS_=4 USB_D=6      (prefix=target[/limit], e.g. CSI_=3/6)
         [--exempt X0 Y0 X1 Y1 ...] [--radius 2.0] [--gnd GND] [-v]
Box coordinates are relative to the drill origin. One line per pair, a summary on the last line; the exit code is 1 if any "NOT MET" appears.
"""
import argparse
import math
import os
import sys

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kicad import boxes_abs, load_pcbnew, origin, zone_fills  # noqa: E402
from impedance_check import pair_of  # noqa: E402

EX = "EX"   # sample point in an exempt area


def chains(segs, tol=0.01):
    """segs: [(layer, (x0, y0), (x1, y1), width)]. Chain segments into ordered polylines by touching endpoints (endpoints of the two sides of a layer-change via coincide and also touch).

    Returns [[(layer, a, c, width), ...], ...], direction consistent within each; start from degree-1 endpoints, start a new chain at branches. Pure function.
    """
    left = [s for s in segs if math.dist(s[1], s[2]) > 1e-6]
    near = lambda p, q: math.dist(p, q) < tol  # noqa: E731
    out = []
    while left:
        deg = lambda p: sum(near(p, s[1]) + near(p, s[2]) for s in left)  # noqa: E731
        start = next((p for s in left for p in (s[1], s[2]) if deg(p) == 1), left[0][1])
        cur, ch = start, []
        while True:
            s = next((s for s in left if near(s[1], cur) or near(s[2], cur)), None)
            if s is None:
                break
            left.remove(s)
            if near(s[1], cur):
                ch.append(s)
                cur = s[2]
            else:
                ch.append((s[0], s[2], s[1], s[3]))
                cur = s[1]
        out.append(ch)
    return out


def sample_chain(chain, mate, step=0.25):
    """Sample along one ordered polyline. mate: the other trace of the pair (shapely line, may be empty).

    Returns [(position s along the line, (x, y), outward normal (nx, ny), layer, width)]; the outward normal points away from the nearest point on mate. Pure function.
    """
    out, s0 = [], 0.0
    for layer, a, c, w in chain:
        L = math.dist(a, c)
        ux, uy = (c[0] - a[0]) / L, (c[1] - a[1]) / L
        k = max(1, round(L / step))
        for i in range(0 if not out else 1, k + 1):
            p = (a[0] + ux * L * i / k, a[1] + uy * L * i / k)
            nx, ny = -uy, ux
            if mate is not None and not mate.is_empty:
                q = nearest_points(mate, Point(p))[0]
                if (q.x - p[0]) * nx + (q.y - p[1]) * ny > 0:
                    nx, ny = -nx, -ny
            out.append((s0 + L * i / k, p, (nx, ny), layer, w))
        s0 += L
    return out


def classify(samples, islands, exempt=None, probe=0.8):
    """Outer-side guard of each sample point: pour island index, None (unguarded) or EX (exempt area). Pure function.

    islands: {layer: [GND fill polygons, ...]}; a short probe segment of length probe mm from the trace edge counts as whichever island it hits.
    """
    out = []
    for _, p, n, layer, w in samples:
        if exempt is not None and exempt.contains(Point(p)):
            out.append(EX)
            continue
        ray = LineString([(p[0] + n[0] * w / 2, p[1] + n[1] * w / 2),
                          (p[0] + n[0] * (w / 2 + probe), p[1] + n[1] * (w / 2 + probe))])
        out.append(next((i for i, g in enumerate(islands.get(layer, [])) if g.intersects(ray)), None))
    return out


def stations(samples, vias, radius):
    """Ground via projection: vias [(x, y, radius, layer set)] -> [(s, via index)].

    A via projects to the nearest sample point; it must be on that point's layer, lateral distance <= radius, on the outer side (component along the outward normal > 0). Pure function.
    """
    out = []
    if not samples:
        return out
    x0, x1 = min(p[0] for _, p, *_ in samples) - radius, max(p[0] for _, p, *_ in samples) + radius
    y0, y1 = min(p[1] for _, p, *_ in samples) - radius, max(p[1] for _, p, *_ in samples) + radius
    for j, (x, y, _, layers) in enumerate(vias):
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            continue
        best = None
        for i, (s, p, n, layer, _) in enumerate(samples):
            if layer not in layers:
                continue
            d = math.hypot(x - p[0], y - p[1])
            if d <= radius and (best is None or d < best[0]):
                best = (d, i)
        if best is None:
            continue
        i = best[1]
        s, p, n = samples[i][:3]
        if (x - p[0]) * n[0] + (y - p[1]) * n[1] <= 0:
            continue
        # Refine the along-line position along the tangent to within the sample step, so via spacing is not quantized to multiples of step
        k = i + 1 if i + 1 < len(samples) else i - 1
        ds = samples[k][0] - s
        if k >= 0 and abs(ds) > 1e-9:
            q = samples[k][1]
            t = ((q[0] - p[0]) / ds, (q[1] - p[1]) / ds)
            s += max(-abs(ds) / 2, min(abs(ds) / 2, (x - p[0]) * t[0] + (y - p[1]) * t[1]))
        out.append((s, j))
    return out


def max_gap(s0, s1, pos, spacing):
    """Maximum ground-via gap within guarded run [s0, s1]. pos: along-line positions of ground vias on this run's pour island (may extend slightly past either end).

    The gap is the maximum of run start -> first via, between adjacent vias, last via -> run end (an end whose via lies outside the run counts as 0).
    Returns (max gap mm, start s of that gap, category); category: "", "pour island without ground via", "grounded at both ends only". Pure function.
    """
    pos = sorted(p for p in pos if s0 - spacing <= p <= s1 + spacing)
    if not pos:
        return s1 - s0, s0, "pour island without ground via"
    pts = [min(s0, pos[0])] + pos + [max(s1, pos[-1])]
    gap, at = max((pts[i + 1] - pts[i], pts[i]) for i in range(len(pts) - 1))
    kind = ""
    if gap > spacing and all(p - s0 <= spacing / 2 or s1 - p <= spacing / 2 for p in pos):
        kind = "grounded at both ends only"
    return gap, at, kind


def runs(samples, keys):
    """Cut continuous guarded runs by (layer, pour island): [(layer, island, first index, last index)]. Unguarded and exempt points break a run. Pure function."""
    out, cur = [], None
    for i, ((_, _, _, layer, _), k) in enumerate(zip(samples, keys)):
        key = None if k is None or k == EX else (layer, k)
        if cur and key == tuple(cur[:2]):
            cur[3] = i
        else:
            if cur:
                out.append(tuple(cur))
            cur = [layer, k, i, i] if key else None
    if cur:
        out.append(tuple(cur))
    return out


def judge_net(samples, keys, sta, vias, islands, spacing, step=0.25):
    """Judge one trace. Returns (guarded length, unguarded length, [(gap, position (x, y), layer, category)], only over-limit runs). Pure function."""
    covered = sum(step for k in keys if k is not None and k != EX)
    bare = sum(step for k in keys if k is None)
    bad = []
    for layer, isl, i0, i1 in runs(samples, keys):
        s0, s1 = samples[i0][0], samples[i1][0]
        poly = islands[layer][isl]
        pos = [s for s, j in sta if poly.distance(Point(vias[j][:2])) <= vias[j][2] + 0.05]
        gap, at, kind = max_gap(s0, s1, pos, spacing)
        if gap > spacing + 1e-6:
            p = min(samples[i0:i1 + 1], key=lambda x: abs(x[0] - at))[1]
            bad.append((gap, p, layer, kind))
    return covered, bare, bad


def grade(gap, target, limit):
    """Via-spacing grading (pure function): <= target -> None; target to limit -> "relaxed" (hint); > limit -> "exceeded" (NOT MET)."""
    if gap <= target + 1e-6:
        return None
    return "relaxed" if gap <= limit + 1e-6 else "exceeded"


def parse_limit(v):
    """"3" -> (3, 6); "3/5" -> (3, 5). The limit defaults to 2x the target (lambda/20 -> lambda/10). Pure function."""
    t, _, l = v.partition("/")
    return float(t), float(l) if l else 2 * float(t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--net", nargs="+", required=True, help="Net prefix=target spacing[/limit spacing] mm; the limit defaults to 2x the target")
    ap.add_argument("--exempt", type=float, nargs=4, action="append", default=[])
    ap.add_argument("--radius", type=float, default=2.0, help="Maximum lateral distance from a ground via to the trace center, mm")
    ap.add_argument("--probe", type=float, default=0.8, help="How far beyond the trace edge GND fill counts as guard, mm")
    ap.add_argument("--step", type=float, default=0.25)
    ap.add_argument("--gnd", default="GND")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    limits = [(k, parse_limit(v)) for k, v in (x.rsplit("=", 1) for x in a.net)]
    pcbnew = load_pcbnew()
    mm = pcbnew.ToMM
    b = pcbnew.LoadBoard(a.board)
    org = origin(b, pcbnew)
    rp = lambda p: f"({p[0] - org[0]:.2f}, {p[1] - org[1]:.2f})"  # noqa: E731
    exempt = unary_union(boxes_abs(a.exempt, org)) if a.exempt else Polygon()

    segs = {}
    for t in b.GetTracks():
        n = t.GetNetname()
        if t.Type() == pcbnew.PCB_VIA_T or not n.startswith(tuple(k for k, _ in limits)):
            continue
        pts = [t.GetStart(), t.GetMid(), t.GetEnd()] if t.Type() == pcbnew.PCB_ARC_T else [t.GetStart(), t.GetEnd()]
        pts = [(mm(v.x), mm(v.y)) for v in pts]
        segs.setdefault(n, []).extend((t.GetLayer(), p, q, mm(t.GetWidth())) for p, q in zip(pts, pts[1:]))

    cu = list(b.GetEnabledLayers().CuStack())
    vias = [(mm(t.GetPosition().x), mm(t.GetPosition().y), mm(t.GetWidth(pcbnew.F_Cu)) / 2,
             {l for l in cu if t.IsOnLayer(l)}) for t in b.GetTracks()
            if t.Type() == pcbnew.PCB_VIA_T and t.GetNetname() == a.gnd]
    vias += [(mm(p.GetPosition().x), mm(p.GetPosition().y), min(mm(p.GetSize(pcbnew.F_Cu).x), mm(p.GetSize(pcbnew.F_Cu).y)) / 2,
              set(cu)) for p in b.GetPads() if p.GetNetname() == a.gnd and p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH]
    layers = {l for ss in segs.values() for l, *_ in ss}
    islands = {}
    for l in layers:
        u = unary_union([g for n, g in zone_fills(b, l, pcbnew) if n == a.gnd])
        islands[l] = [g for g in getattr(u, "geoms", [u]) if not g.is_empty]

    print(f"Guard-ground vias (SPEC-005 2.9): same-layer GND fill within {a.probe:g} mm on the outer side counts as guard; ground vias within {a.radius:g} mm laterally, on the outer side and on the same pour island; "
          f"{len(a.exempt)} exempt box(es)")
    n_pair = n_bad = n_relax = 0
    bare_all = 0.0
    for n in sorted(segs):
        pr = pair_of(n)
        if not pr or pr[1] not in segs:
            continue
        name, nn = pr
        spacing, limit = next(v for k, v in limits if n.startswith(k))
        n_pair += 1
        cov_t = bare_t = 0.0
        bad = []
        for me, mate in ((n, nn), (nn, n)):
            mate_g = unary_union([LineString([p, q]) for _, p, q, _ in segs[mate]])
            for ch in chains(segs[me]):
                smp = sample_chain(ch, mate_g, a.step)
                keys = classify(smp, islands, exempt, a.probe)
                cov, bare, bd = judge_net(smp, keys, stations(smp, vias, a.radius), vias, islands, spacing, a.step)
                cov_t, bare_t = cov_t + cov, bare_t + bare
                bad += [(g, me, p, l, k) for g, p, l, k in bd]
        bare_all += bare_t
        bad.sort(key=lambda x: -x[0])
        hard = [x for x in bad if grade(x[0], spacing, limit) == "exceeded"]
        soft = [x for x in bad if grade(x[0], spacing, limit) == "relaxed"]
        head = f"{name} guarded {cov_t:.1f} mm / unguarded {bare_t:.1f} mm"
        if hard:
            n_bad += 1
            g, me, p, l, k = hard[0]
            print(f"{head}, max via gap {g:.2f} mm (target <= {spacing:g}, limit <= {limit:g}) {me} {b.GetLayerName(l)} {rp(p)}"
                  f"{' (' + k + ')' if k else ''}, exceeded {len(hard)} segment(s) NOT MET")
            for g, me, p, l, k in hard[1:] if a.v else hard[1:6]:
                print(f"   {g:.2f} mm {me} {b.GetLayerName(l)} {rp(p)}{' (' + k + ')' if k else ''}")
        else:
            print(f"{head}, ground-via spacing <= {limit if soft else spacing:g} mm")
        if soft:
            n_relax += 1
            print(f"   relaxed {len(soft)} segment(s) (target {spacing:g} mm to limit {limit:g} mm, list in the design document):")
            for g, me, p, l, k in soft if a.v else soft[:6]:
                print(f"   {g:.2f} mm {me} {b.GetLayerName(l)} {rp(p)}{' (' + k + ')' if k else ''}")
    print(f"Summary: {n_pair} pair(s), exceeded {n_bad} pair(s), relaxed {n_relax} pair(s); unguarded total {bare_all:.1f} mm (spacing not judged, handled as 3W by hs_review)")
    sys.exit(1 if n_bad else 0)


if __name__ == "__main__":
    main()
