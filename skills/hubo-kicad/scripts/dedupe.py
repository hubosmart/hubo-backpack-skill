"""Delete zero-length tracks, exact duplicates of another track (keep one), and tracks fully covered by another track of the same net, layer and equal or greater width (Freerouting adds an extra segment between the two rows of same-net pads on
flow-through ESD parts, overlapping the pre-routed straight line and inflating the length statistics).
Usage: <KiCad>/bin/python dedupe.py <board.kicad_pcb>
"""
import sys

import pcbnew
from shapely.geometry import LineString, Point

b = pcbnew.LoadBoard(sys.argv[1])
mm = pcbnew.ToMM
segs = [t for t in b.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T]
geo = {}
for t in segs:
    a = (mm(t.GetStart().x), mm(t.GetStart().y))
    c = (mm(t.GetEnd().x), mm(t.GetEnd().y))
    geo[t.m_Uuid.AsString()] = (t, a, c)
dead = []
for k, (t, a, c) in geo.items():
    if a == c:
        dead.append(t)
        continue
    line = LineString([a, c])
    for k2, (t2, a2, c2) in geo.items():
        if k2 == k or t2.GetNetname() != t.GetNetname() or t2.GetLayer() != t.GetLayer() or a2 == c2:
            continue
        # Tolerance 1 um: the overlapping segment Freerouting adds is off by 100 nm in coordinates and 0.1 um in width (SES rounding); a strict comparison would miss it
        if t2.GetWidth() >= t.GetWidth() - 1000 and LineString([a2, c2]).buffer(1e-3).contains(line):
            l2 = LineString([a2, c2]).length
            # Covered by a longer segment; or two identical (equal-length) segments: delete the narrower, for equal width delete one by uuid
            if l2 > line.length + 1e-6 or (abs(l2 - line.length) <= 1e-6 and (t2.GetWidth() > t.GetWidth() + 1000 or (abs(t2.GetWidth() - t.GetWidth()) <= 1000 and k > k2))):
                dead.append(t)
                break
for t in dead:
    b.Remove(t)
b.Save(sys.argv[1])
print("Deleted duplicate/zero-length tracks:", len(dead))
