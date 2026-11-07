"""Shared helpers: locate kicad-cli, read the board outline and copper pour polygons.

All scripts in this directory must run with **KiCad's bundled Python** (pcbnew is only available there). Coordinates are in mm.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def kicad_cli() -> str:
    """Prefer the KICAD_CLI environment variable, then the directory of the current python (true for KiCad's bundled python), then PATH."""
    env = os.environ.get("KICAD_CLI")
    if env and pathlib.Path(env).exists():
        return env
    exe = pathlib.Path(sys.executable).with_name("kicad-cli.exe" if os.name == "nt" else "kicad-cli")
    if exe.exists():
        return str(exe)
    found = shutil.which("kicad-cli")
    if found:
        return found
    raise SystemExit("kicad-cli not found: set the KICAD_CLI environment variable, or run this script with KiCad's bundled python")


def load_pcbnew():
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        raise SystemExit("import pcbnew failed: run with KiCad's bundled python (e.g. <KiCad>/bin/python.exe) and run doctor.py first")
    return pcbnew


def board_outline(board, pcbnew):
    """Board outline polygon (shapely). Must be called before any modification of the board."""
    from shapely.geometry import Polygon
    mm = pcbnew.ToMM
    ps = pcbnew.SHAPE_POLY_SET()
    board.GetBoardPolygonOutlines(ps, True)
    o = ps.Outline(0)
    return Polygon([(mm(o.CPoint(j).x), mm(o.CPoint(j).y)) for j in range(o.PointCount())])


def zone_fills(board, layer, pcbnew):
    """Return [(net name, polygon)]; polygons carry holes.

    KiCad saves pours as "fractured" polygons: a hole is merged into the outer outline through a slit, so reading it directly
    yields "0 holes" and the plane looks intact. Unfracture first, then read.
    """
    from shapely.geometry import Polygon
    mm = pcbnew.ToMM
    out = []
    for z in board.Zones():
        if z.GetIsRuleArea() or not z.IsOnLayer(layer):
            continue
        ps = pcbnew.SHAPE_POLY_SET(z.GetFilledPolysList(layer))
        ps.Unfracture()
        for i in range(ps.OutlineCount()):
            o = ps.Outline(i)
            ext = [(mm(o.CPoint(k).x), mm(o.CPoint(k).y)) for k in range(o.PointCount())]
            holes = []
            for h in range(ps.HoleCount(i)):
                hh = ps.Hole(i, h)
                holes.append([(mm(hh.CPoint(k).x), mm(hh.CPoint(k).y)) for k in range(hh.PointCount())])
            if len(ext) >= 3:
                out.append((z.GetNetname(), Polygon(ext, holes).buffer(0)))
    return out


def holes(board, pcbnew):
    """All vias and plated/non-plated holes: [(name, net, x, y, outer radius)]."""
    mm = pcbnew.ToMM
    out = []
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            p = t.GetPosition()
            out.append(("via", t.GetNetname(), mm(p.x), mm(p.y), mm(t.GetWidth(pcbnew.F_Cu)) / 2))
    for f in board.GetFootprints():
        for p in f.Pads():
            if p.HasHole():
                q = p.GetPosition()
                r = max(mm(p.GetSize(pcbnew.F_Cu).x), mm(p.GetSize(pcbnew.F_Cu).y), mm(p.GetDrillSize().x)) / 2
                out.append((f"{f.GetReference()}.{p.GetNumber()}", p.GetNetname(), mm(q.x), mm(q.y), r))
    return out


def origin(board, pcbnew):
    """Drill/aux origin (mm). User-supplied coordinates are relative to it; with no origin set (0, 0) they are absolute."""
    o = board.GetDesignSettings().GetAuxOrigin()
    return pcbnew.ToMM(o.x), pcbnew.ToMM(o.y)


def boxes_abs(boxes, org):
    """[(x0, y0, x1, y1) relative to origin] -> shapely polygons (absolute coordinates)."""
    from shapely.geometry import box
    return [box(x0 + org[0], y0 + org[1], x1 + org[0], y1 + org[1]) for x0, y0, x1, y1 in boxes]


def hs_corridors(board, pcbnew, prefixes, gap, exempt=()):
    """High-speed corridor: tracks on nets starting with prefixes, grown by "half track width + gap" (the 3W-rule spacing band), minus exempt areas.
    Returns {layer ID: polygon} (absolute coordinates, mm). Returns {} when prefixes is empty."""
    from shapely.geometry import LineString
    from shapely.ops import unary_union
    if not prefixes:
        return {}
    mm = pcbnew.ToMM
    out = {}
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T or not t.GetNetname().startswith(tuple(prefixes)):
            continue
        g = LineString([(mm(t.GetStart().x), mm(t.GetStart().y)), (mm(t.GetEnd().x), mm(t.GetEnd().y))])
        out.setdefault(t.GetLayer(), []).append(g.buffer(mm(t.GetWidth()) / 2 + gap))
    ex = unary_union(list(exempt)) if exempt else None
    res = {}
    for lid, gs in out.items():
        u = unary_union(gs)
        res[lid] = u.difference(ex) if ex is not None else u
    return res
