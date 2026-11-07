"""Differential impedance trace check: are the actual trace width and intra-pair gap equal to the design-document values, and do necked-down sections stay within the limit.

Why: after the width/gap is fixed by the PCB vendor calculator it must be written both into the EDA net class and into the differential-pair generation script; if either is missed,
or Freerouting rounds, or traces are patched by hand, the actual geometry drifts while DRC stays silent (DRC only checks the lower limit).
Method: pair `<name>_P` / `<name>_N` (or `+` / `-`); sample along the P trace every step and measure the center distance to the N trace on the same layer,
gap = center distance - trace width, then classify each point:
  ok      gap within gap +/- tol
  neck    gap too small but center distance >= neck_pitch (necked down in a QFN/BGA breakout, continuous length <= neck_len)
  narrow  center distance < neck_pitch: FAIL
  split   gap too large (at pads, flow-through ESD, connector fan-out); only the length is reported
Usage (KiCad's bundled python):
  python impedance_check.py <board.kicad_pcb> --width 0.1295 --gap 0.2662 [--nets TMDS_ CSI_]
         [--neck-pitch 0.30] [--neck-len 1.5] [--tol 0.005] [--layers F.Cu B.Cu] [--exempt X0 Y0 X1 Y1 ...]
  --layers: only check segments on these layers (standard layer names) - the same pair has different impedance widths as outer microstrip and inner stripline, so check each group separately;
  --exempt: segments entirely inside the box are skipped (fine traces kept as in the reference board in BGA / connector fan-out areas); box coordinates are relative to the drill origin and must be listed in the design document.
"""
import argparse
import math
import re
import sys

from shapely.geometry import LineString, Point
from shapely.ops import unary_union

PAIR_RE = (re.compile(r"^(.*)_P$"), re.compile(r"^(.*)\+$"))


def select_segs(segs, widths, layers=(), exempt=()):
    """Filter segments by layer and exempt boxes: no layer restriction when layers is empty; drop segments entirely inside any exempt box (shapely polygon). Returns (segments, trace width)."""
    keep_s, keep_w = [], []
    for seg, w in zip(segs, widths):
        layer, a, b = seg
        if layers and layer not in layers:
            continue
        if any(bx.contains(LineString([a, b])) for bx in exempt):
            continue
        keep_s.append(seg)
        keep_w.append(w)
    return keep_s, keep_w


def pair_of(net):
    """Return (pair name, N net name); None if this is not a P net."""
    for rx in PAIR_RE:
        m = rx.match(net)
        if m:
            return m.group(1), m.group(1) + ("_N" if net.endswith("_P") else "-")
    return None


def profile(p_segs, n_segs, step=0.05):
    """p_segs / n_segs: [(layer, (x0, y0), (x1, y1))]. Returns [(layer, position along P in mm, center distance in mm)]; the center distance is inf when there is no N on the same layer."""
    n_by_layer = {}
    for layer, a, b in n_segs:
        n_by_layer.setdefault(layer, []).append(LineString([a, b]))
    n_union = {k: unary_union(v) for k, v in n_by_layer.items()}
    out, s0 = [], 0.0
    for layer, a, b in p_segs:
        L = math.dist(a, b)
        k = max(1, int(L / step))
        for i in range(k + 1):
            t = i / k
            pt = Point(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            d = n_union[layer].distance(pt) if layer in n_union else math.inf
            out.append((layer, s0 + L * t, d))
        s0 += L
    return out


def classify(prof, width, gap, tol, neck_pitch):
    res = []
    for layer, s, cc in prof:
        g = cc - width
        if abs(g - gap) <= tol:
            c = "ok"
        elif g > gap + tol:
            c = "split"
        elif cc >= neck_pitch - 1e-9:
            c = "neck"
        else:
            c = "narrow"
        res.append((s, c))
    return res


def runs(cls, kind):
    """List of lengths of consecutive runs of kind (accumulated from sample-position differences)."""
    out, start, last = [], None, None
    for s, c in cls:
        if c == kind:
            if start is None:
                start = s
            last = s
        elif start is not None:
            out.append(last - start)
            start = None
    if start is not None:
        out.append(last - start)
    return out


def check_pair(p_segs, n_segs, widths, width, gap, tol, neck_pitch, neck_len, step=0.05):
    """Return (FAIL list, info lines). widths: all trace widths of the pair."""
    fails = []
    bad_w = sorted({round(w, 4) for w in widths if abs(w - width) > max(tol / 5, 0.001)})
    if bad_w:
        fails.append(f"trace width {bad_w} != {width}")
    cls = classify(profile(p_segs, n_segs, step), width, gap, tol, neck_pitch)
    neck = runs(cls, "neck")
    narrow = runs(cls, "narrow")
    split = runs(cls, "split")
    if any(c == "narrow" for _, c in cls):
        fails.append(f"{len(narrow)} section(s) with center distance < {neck_pitch}")
    long_neck = [r for r in neck if r > neck_len + step]
    if long_neck:
        fails.append(f"necked section longer than {neck_len} mm: {[round(r, 2) for r in long_neck]}")
    total = cls[-1][0] if cls else 0
    ok_len = sum(step for _, c in cls if c == "ok")
    info = (f"total {total:.2f} mm, conforming {min(ok_len, total):.2f}; necked {len(neck)} section(s) (longest {max(neck, default=0):.2f}); "
            f"split {len(split)} section(s) (longest {max(split, default=0):.2f})")
    return fails, info


def main():
    import pcbnew
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--width", type=float, required=True)
    ap.add_argument("--gap", type=float, required=True)
    ap.add_argument("--nets", nargs="*", default=[], help="Only check differential pairs starting with these prefixes")
    ap.add_argument("--neck-pitch", type=float, default=0.30)
    ap.add_argument("--neck-len", type=float, default=1.5)
    ap.add_argument("--tol", type=float, default=0.005)
    ap.add_argument("--layers", nargs="*", default=[], help="Only check these layers (standard layer names)")
    ap.add_argument("--exempt", type=float, nargs=4, action="append", default=[], metavar=("X0", "Y0", "X1", "Y1"))
    a = ap.parse_args()
    import pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from _kicad import boxes_abs, origin
    b = pcbnew.LoadBoard(a.board)
    mm = pcbnew.ToMM
    exempt = boxes_abs(a.exempt, origin(b, pcbnew))
    segs, widths = {}, {}
    for t in b.GetTracks():
        if t.Type() != pcbnew.PCB_TRACE_T:
            continue
        n = t.GetNetname()
        segs.setdefault(n, []).append((pcbnew.BOARD.GetStandardLayerName(t.GetLayer()),
                                       (mm(t.GetStart().x), mm(t.GetStart().y)), (mm(t.GetEnd().x), mm(t.GetEnd().y))))
        widths.setdefault(n, []).append(mm(t.GetWidth()))
    n_fail = 0
    for net in sorted(segs):
        pr = pair_of(net)
        if not pr or pr[1] not in segs or (a.nets and not net.startswith(tuple(a.nets))):
            continue
        name, nnet = pr
        ps, pw = select_segs(segs[net], widths[net], a.layers, exempt)
        ns, nw = select_segs(segs[nnet], widths[nnet], a.layers, exempt)
        if not ps or not ns:
            continue
        fails, info = check_pair(ps, ns, pw + nw, a.width, a.gap, a.tol, a.neck_pitch, a.neck_len)
        print(f"{'FAIL' if fails else 'OK  '} {name}: {info}" + ("; " + "; ".join(fails) if fails else ""))
        n_fail += bool(fails)
    print(f"Result: {'has FAIL items' if n_fail else 'no FAIL items'} (width {a.width}, gap {a.gap}, necked center distance >= {a.neck_pitch}, single section <= {a.neck_len} mm)")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
