"""Pre-fabrication check for P4 30-Pin backpack boards, against assets/p4_backpack_baseline.json (from SPEC-003/004).

Usage:
  KiCad board (must run with KiCad's bundled python, which provides pcbnew):
    python backpack_check.py <board.kicad_pcb> [--header H1] [--expect-address 0x53] [--baseline <json>]
  EasyEDA Pro project (any python 3; reads the zip offline, no EDA client needed):
    python backpack_check.py <project.epro2> [--board PCB1] [--header H1] [--expect-address 0x53]
  The loader is chosen by file extension; when an .epro2 holds several PCBs, pick one with --board
  (PCB name, board name or uuid).

Checks: board outline size and corner radius, layer count, 4 mounting holes (position / drill / pad / ground),
30-Pin header (bottom side, every pad position), header pad nets (5V/3V3/GND groups, 5V != 3V3, default-NC
pads unused), ID card (AT24C02, address derived from A0-A2 wiring, WP not tied directly to ground),
silkscreen reference/value text height and stroke width.
Coordinates are relative to the drill/place origin; if none is set the board outline center is used (for .epro2
the canvas origin (0,0) must be at the outline center, otherwise the outline center is used as well).
Structure: load_kicad / load_epro2 read a project into one neutral board model (mm, relative to the origin,
viewed from the top with X right and Y down); run_checks reads only that model to reach its verdicts. The two
do not depend on each other, so the verdict logic is independent of the board source.
Items that need 3D or load data (height limit, current budget, enclosure) are only listed for manual review.
Exit code: 1 if any FAIL.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import zipfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
MIL = 0.0254  # EasyEDA Pro PCB coordinate/size unit is mil (measured: 83 mm board width = 3267.717)
ORIGIN_TOL_MM = 0.05  # same as tolerance_mm in the baseline: tolerance for canvas origin vs outline center


# ---------------- pure functions ----------------

def expected_header_pads(h):
    """{pad number: (x, y)}: odd pads sit at odd_column_x, even pads at even_column_x, two pads per row."""
    out = {}
    for n in range(1, h["pads"] + 1):
        row = (n - 1) // 2
        x = h["odd_column_x"] if n % 2 else h["even_column_x"]
        out[n] = (x, round(h["pad1"][1] + row * h["row_pitch"], 4))
    return out


def eeprom_address(pin_nets, high_nets, gnd_net, pins, base=0x50):
    """Derive the 7-bit address from the nets A0-A2 are wired to; returns None and a reason if the wiring is ambiguous (floating, or on a signal)."""
    addr = base
    for bit, name in enumerate(("A0", "A1", "A2")):
        net = pin_nets.get(pins[name], "")
        if net in high_nets:
            addr |= 1 << bit
        elif net != gnd_net:
            return None, f"{name} is on {net or 'floating'}, level undetermined (must be tied to 3V3 or GND)"
    return addr, ""


def check_pin_nets(pad_nets, pins, gnd="GND"):
    """Return [(level, message)]. pad_nets: {pad number: net name}."""
    res = []
    v5 = {pad_nets.get(p, "") for p in pins["5V"]}
    v3 = {pad_nets.get(p, "") for p in pins["3V3"]}
    if len(v5) != 1 or "" in v5:
        res.append(("FAIL", f"5V pads {pins['5V']} on inconsistent nets or floating: {sorted(v5)}"))
    if len(v3) != 1 or "" in v3:
        res.append(("FAIL", f"3V3 pads {pins['3V3']} on inconsistent nets or floating: {sorted(v3)}"))
    if v5 & v3 - {""}:
        res.append(("FAIL", "5V and 3V3 pads are on the same net"))
    bad = [p for p in pins["GND"] if pad_nets.get(p) != gnd]
    if bad:
        res.append(("FAIL", f"GND pads not on {gnd}: {bad}"))
    used = [p for p in pins["NC_DEFAULT"] if pad_nets.get(p, "") and not pad_nets[p].startswith("unconnected-")]
    if used:
        res.append(("WARN", f"default-NC pads in use: {used} (review per SPEC-003 3.4.5; CH32_ADC pads 16 and 19 need a 1 kOhm series resistor)"))
    if not res:
        res.append(("OK", "5V/3V3/GND groups correct, default-NC pads unused"))
    return res




# ---------------- neutral board model ----------------
# load_kicad / load_epro2 both emit this structure and run_checks only understands it (lengths in mm,
# coordinates already relative to the origin, viewed from the top with X right and Y down, as in KiCad):
#   origin_desc   text describing the coordinate reference (where the origin comes from)
#   outline       {"width", "height", "radii"} outline centerline size and corner radii; None if no outline found
#   copper_layers number of copper layers
#   holes         [{"ref", "x", "y", "drill", "pad", "net"}] all pads with a hole
#   components    [{"ref", "value", "side": "top"|"bottom", "pads": [{"num", "x", "y", "net"}]}]
#   header        {"candidates": [ref, ...]} candidate 30-Pin headers (only the given ref when --header is used)
#   silk          {"items": [{"ref", "height", "thickness"}], "height_verified": bool}
#                 height_verified False means the text height is only estimated from a source-file field,
#                 not measured from the rendered glyphs

# ---------------- read KiCad (pcbnew) ----------------

def load_kicad(path, header=None):
    """Read a .kicad_pcb with pcbnew and emit the neutral board model."""
    try:
        import pcbnew
    except ImportError:
        raise SystemExit("import pcbnew failed: run with KiCad's bundled python")
    mm = pcbnew.ToMM
    b = pcbnew.LoadBoard(path)
    ao = b.GetDesignSettings().GetAuxOrigin()
    bb = b.GetBoardEdgesBoundingBox()
    if (ao.x, ao.y) != (0, 0):
        ox, oy = mm(ao.x), mm(ao.y)
        origin_desc = f"drill/place origin ({ox:.3f}, {oy:.3f})"
    else:
        c = bb.GetCenter()
        ox, oy = mm(c.x), mm(c.y)
        origin_desc = f"no origin set, using outline center ({ox:.3f}, {oy:.3f})"

    lw = 0.0
    for d in b.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts:
            lw = mm(d.GetWidth())
            break
    outline = {
        "width": mm(bb.GetWidth()) - lw,
        "height": mm(bb.GetHeight()) - lw,
        "radii": sorted({round(mm(d.GetRadius()), 2) for d in b.GetDrawings()
                         if d.GetLayer() == pcbnew.Edge_Cuts and d.GetShape() == pcbnew.SHAPE_T_ARC}),
    }

    components, holes, silk = [], [], []
    for f in b.GetFootprints():
        ref = f.GetReference()
        pads = []
        for p in f.Pads():
            x, y = mm(p.GetPosition().x) - ox, mm(p.GetPosition().y) - oy
            pads.append({"num": p.GetNumber(), "x": x, "y": y, "net": p.GetNetname()})
            if p.HasHole():
                holes.append({"ref": ref, "x": x, "y": y, "drill": mm(p.GetDrillSize().x),
                              "pad": mm(p.GetSize(pcbnew.F_Cu).x), "net": p.GetNetname()})
        components.append({"ref": ref, "value": f.GetValue(), "pads": pads,
                           "side": "bottom" if f.GetLayer() == pcbnew.B_Cu else "top"})
        for t in (f.Reference(), f.Value()):
            if t.IsVisible() and t.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS):
                silk.append({"ref": ref, "height": mm(t.GetTextHeight()), "thickness": mm(t.GetTextThickness())})
    return {
        "origin_desc": origin_desc, "outline": outline, "copper_layers": b.GetCopperLayerCount(),
        "holes": holes, "components": components, "header": _header_candidates(components, header),
        "silk": {"items": silk, "height_verified": True},
    }


def _header_candidates(components, header):
    """If --header gives a ref use only it, otherwise take components with exactly 30 distinct pad numbers."""
    return {"candidates": [c["ref"] for c in components
                           if (header and c["ref"] == header)
                           or (not header and len({p["num"] for p in c["pads"]}) == 30)]}


# ---------------- read EasyEDA Pro .epro2 (fully offline) ----------------
# Format (measured on editorVersion 3.x): .epro2 is a zip holding project2.json, IMAGE/ and *.epru;
# each .epru line is `{header}||{body}|`, and a line with type == "DOCHEAD" starts a new document
# (FOOTPRINT/PCB/BOARD/DEVICE...).
# PCB coordinates are in mil with Y up; after converting to mm, Y is flipped to match KiCad's
# "viewed from the top, Y down".

def _read_epru_docs(path):
    """Read the .epru files inside the zip in memory only, without extracting to disk. Returns [{"meta", "records": [(header, body)]}]."""
    dec = json.JSONDecoder()
    docs, cur = [], None
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise SystemExit(f"cannot open {path}: {e}")
    with z:
        names = [n for n in z.namelist() if n.endswith(".epru")]
        if not names:
            raise SystemExit(f"no .epru file in {path}: not an EasyEDA Pro project?")
        for n in names:
            for line in z.read(n).decode("utf-8-sig").split("\n"):
                line = line.rstrip("\r")
                try:
                    h, i = dec.raw_decode(line)
                except ValueError:
                    continue
                rest = line[i:]
                if not rest.startswith("||"):
                    continue
                rest = rest[2:]
                if rest.endswith("|"):
                    rest = rest[:-1]
                try:
                    body = json.loads(rest) if rest.strip() else None
                except ValueError:
                    body = None
                if h.get("type") == "DOCHEAD":
                    cur = {"meta": body or {}, "records": []}
                    docs.append(cur)
                elif cur is not None:
                    cur["records"].append((h, body))
    return docs


def eda_pad_xy(cx, cy, angle, bottom, px, py):
    """Footprint pad (px, py) -> absolute canvas coordinates (mil, Y up, unconverted).
    Top layer: rotate counter-clockwise by angle degrees about the component origin. Bottom layer: first negate the
    pad y (mirror), then rotate counter-clockwise by angle degrees.
    Basis (measured): on 3 samples the bottom-layer 30-Pin header (angle 270) gives 30 pads with zero error against
    the baseline positions; for the other bottom-layer components, the number of pads matching same-net trace
    endpoints is also highest under this convention. angle is in degrees (both the component angle and the
    footprint padAngle)."""
    if bottom:
        py = -py
    a = math.radians(angle or 0)
    return (cx + px * math.cos(a) - py * math.sin(a),
            cy + px * math.sin(a) + py * math.cos(a))


def _arc_geometry(p, q, sweep_deg):
    """EasyEDA arc (start, end, sweep angle; positive = counter-clockwise, Y up) -> (center, radius, start angle in radians)."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    chord = math.hypot(dx, dy)
    th = math.radians(sweep_deg)
    r = chord / (2 * abs(math.sin(th / 2)))
    # for a counter-clockwise sweep the center is left of the chord, for clockwise it is right
    sgn = 1 if th > 0 else -1
    h = sgn * r * math.cos(th / 2)
    c = ((p[0] + q[0]) / 2 - dy / chord * h, (p[1] + q[1]) / 2 + dx / chord * h)
    return c, r, math.atan2(p[1] - c[1], p[0] - c[0])


def eda_outline_geom(path):
    """Outline POLY path -> (point set, list of corner radii in mil). The point set includes the quadrant points the arcs pass through, for the bounding box.
    Supports L, ARC/CARC, R (rectangle; numbers are x y width height rotation [isCCW] corner radius) and CIRCLE;
    a cubic Bezier C is rejected outright, never guessed."""
    if path and isinstance(path[0], list):
        path = path[0]
    if not path:
        return [], []
    if path[0] == "R":
        x, y, w, h = path[1:5]
        rot = path[5] if len(path) > 5 else 0
        if round(rot) % 180:
            w, h = h, w
        return [(x, y), (x + w, y - h)], ([path[-1]] if len(path) > 6 and path[-1] else [])
    if path[0] == "CIRCLE":
        cx, cy, r = path[1:4]
        return [(cx - r, cy - r), (cx + r, cy + r)], []
    pts, radii = [(path[0], path[1])], []
    cur, mode, i = pts[0], "L", 2
    while i < len(path):
        t = path[i]
        if t in ("L", "ARC", "CARC", "C"):
            if t == "C":
                raise SystemExit("outline contains a cubic Bezier curve (C), which this script does not parse; use a straight-line/arc outline")
            mode, i = t, i + 1
        elif mode == "L":
            cur = (path[i], path[i + 1])
            pts.append(cur)
            i += 2
        else:
            sweep, q = path[i], (path[i + 1], path[i + 2])
            c, r, a0 = _arc_geometry(cur, q, sweep)
            radii.append(r)
            for k in range(4):
                ak = k * math.pi / 2
                # is the quadrant point inside [a0, a0+sweep]?
                d = (ak - a0) % (2 * math.pi) if sweep > 0 else (a0 - ak) % (2 * math.pi)
                if d <= abs(math.radians(sweep)):
                    pts.append((c[0] + r * math.cos(ak), c[1] + r * math.sin(ak)))
            pts.append(q)
            cur = q
            i += 3
    return pts, radii


def _meta(doc):
    for h, b in doc["records"]:
        if h.get("type") == "META":
            return b or {}
    return {}


def _select_pcb(docs, board):
    boards = {d["meta"].get("uuid"): _meta(d).get("title") for d in docs if d["meta"].get("docType") == "BOARD"}
    pcbs = [d for d in docs if d["meta"].get("docType") == "PCB"]

    def label(d):
        return f"{_meta(d).get('title')} (board {boards.get(_meta(d).get('board'))}, uuid {d['meta'].get('uuid')})"

    if not pcbs:
        raise SystemExit("no PCB document in the project")
    if board is None:
        if len(pcbs) > 1:
            raise SystemExit("several PCBs in the project, pick one with --board:\n  " + "\n  ".join(label(d) for d in pcbs))
        return pcbs[0]
    hit = [d for d in pcbs if board in (_meta(d).get("title"), boards.get(_meta(d).get("board")), d["meta"].get("uuid"))]
    if len(hit) != 1:
        raise SystemExit(f"--board {board} matches {len(hit)} PCBs, choices:\n  " + "\n  ".join(label(d) for d in pcbs))
    return hit[0]


def load_epro2(path, header=None, board=None):
    """Read an .epro2 offline and emit the neutral board model. board: PCB name / board name / uuid; may be omitted if the project has only one PCB."""
    docs = _read_epru_docs(path)
    footprints, devices = {}, {}
    for d in docs:
        dt = d["meta"].get("docType")
        if dt == "FOOTPRINT":
            footprints[d["meta"].get("uuid")] = [b for h, b in d["records"] if h.get("type") == "PAD" and b]
        elif dt == "DEVICE":
            devices[d["meta"].get("uuid")] = {"title": _meta(d).get("title") or "", "attrs": _meta(d).get("attributes") or {}}
    pcb = _select_pcb(docs, board)

    attrs, comps, pad_net, free_pads, outline_polys, layers, silk_attrs = {}, {}, {}, [], [], [], []
    for h, b in pcb["records"]:
        t = h.get("type")
        if not b:
            continue
        if t == "COMPONENT":
            comps[h["id"]] = b
        elif t == "ATTR" and b.get("parentId"):
            attrs.setdefault(b["parentId"], {})[b.get("key")] = b.get("value")
            if b.get("valueVisible") and b.get("layerId") in (3, 4):
                silk_attrs.append(b)
        elif t == "PAD_NET":
            i = json.loads(h["id"]) if isinstance(h["id"], str) else h["id"]
            pad_net[(i[1], str(i[2]))] = b.get("padNet") or ""
        elif t == "PAD":
            free_pads.append(b)
        elif t == "POLY" and b.get("layerId") == 11:
            outline_polys.append(b)
        elif t == "LAYER":
            layers.append(b)

    # outline -> shape, corner radii, origin
    pts, radii = [], []
    for b in outline_polys:
        p, r = eda_outline_geom(b.get("path") or [])
        pts += p
        radii += r
    outline, ox, oy = None, 0.0, 0.0
    origin_desc = "no outline found, using canvas origin (0, 0)"
    if pts:
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        outline = {"width": (max(xs) - min(xs)) * MIL, "height": (max(ys) - min(ys)) * MIL,
                   "radii": sorted({round(r * MIL, 2) for r in radii})}
        cx, cy = (max(xs) + min(xs)) / 2 * MIL, -(max(ys) + min(ys)) / 2 * MIL
        if abs(cx) <= ORIGIN_TOL_MM and abs(cy) <= ORIGIN_TOL_MM:
            origin_desc = "canvas origin (0.000, 0.000) at outline center"
        else:
            ox, oy = cx, cy
            origin_desc = f"canvas origin not at outline center, using outline center ({ox:.3f}, {oy:.3f})"

    def to_mm(x, y):
        return x * MIL - ox, -y * MIL - oy

    components, holes = [], []
    for cid, c in comps.items():
        a = attrs.get(cid, {})
        ref = a.get("Designator") or ""
        bottom = c.get("layerId") == 2
        dev = devices.get(a.get("Device"), {"title": "", "attrs": {}})
        # An EasyEDA PCB component has no Value field like KiCad: join the designator attributes, device name and
        # part number so the part can be matched by model
        value = " ".join(str(v) for v in (a.get("Value"), a.get("Name"), dev["title"], dev["attrs"].get("Value"),
                                          dev["attrs"].get("Manufacturer Part")) if v)
        pads = []
        for p in footprints.get(a.get("Footprint"), []):
            x, y = to_mm(*eda_pad_xy(c["x"], c["y"], c.get("angle"), bottom, p["centerX"], p["centerY"]))
            num = str(p.get("num"))
            net = pad_net.get((cid, num), "")
            pads.append({"num": num, "x": x, "y": y, "net": net})
            hole = p.get("hole") or {}
            if hole.get("width"):
                holes.append({"ref": ref, "x": x, "y": y, "drill": hole["width"] * MIL,
                              "pad": (p.get("defaultPad") or {}).get("width", 0) * MIL, "net": net})
        components.append({"ref": ref, "value": value, "side": "bottom" if bottom else "top", "pads": pads})
    # holed pads that belong to no component (mounting holes are usually drawn this way)
    for p in free_pads:
        hole = p.get("hole") or {}
        if hole.get("width"):
            x, y = to_mm(p["centerX"], p["centerY"])
            holes.append({"ref": "hole", "x": x, "y": y, "drill": hole["width"] * MIL,
                          "pad": (p.get("defaultPad") or {}).get("width", 0) * MIL, "net": p.get("netName") or ""})

    # Silkscreen: designator-style text is an ATTR in the EDA (valueVisible and on a silk layer). strokeWidth is in
    # mil and reliable; fontSize is used as the text height in mil, but the rendered glyph height is not
    # measured, so height_verified=False
    silk = [{"ref": attrs.get(b["parentId"], {}).get("Designator") or "", "height": (b.get("fontSize") or 0) * MIL,
             "thickness": (b.get("strokeWidth") or 0) * MIL} for b in silk_attrs if b["parentId"] in comps]
    n_cu = sum(1 for l in layers if l.get("use") and l.get("layerType") in ("TOP", "BOTTOM", "SIGNAL", "INNER", "PLANE"))
    return {
        "origin_desc": origin_desc, "outline": outline, "copper_layers": n_cu, "holes": holes,
        "components": components, "header": _header_candidates(components, header),
        "silk": {"items": silk, "height_verified": False},
    }


# ---------------- verdicts (reads the model only, never touches EDA files) ----------------

def run_checks(model, base, expect_address, out=print):
    """Judge the neutral board model against the baseline, passing each line to out (default print); returns the exit code (1 if any FAIL)."""
    tol = base["tolerance_mm"]
    fails = 0

    def rep(level, msg):
        nonlocal fails
        fails += level == "FAIL"
        out(f"  [{level:<4}] {msg}")

    out(f"Baseline: {base['spec_version']}")
    out(f"Coordinate reference: {model['origin_desc']}")

    out("== Outline and layers")
    o = base["outline"]
    ol = model["outline"]
    if ol is None:
        rep("FAIL", "no board outline found (nothing on the outline layer)")
    else:
        w, h = ol["width"], ol["height"]
        rep("OK" if abs(w - o["width"]) <= tol and abs(h - o["height"]) <= tol else "FAIL",
            f"outline {w:.2f} x {h:.2f} mm (required {o['width']} x {o['height']})")
        radii = ol["radii"]
        rep("OK" if radii == [o["corner_radius"]] else "WARN", f"corner radius {radii or 'none'} (required R{o['corner_radius']})")
    n_cu = model["copper_layers"]
    rep("OK" if n_cu in base["layer_counts"] else "FAIL", f"{n_cu} layers (allowed {base['layer_counts']}, record the reason in the design doc)")

    out("== Mounting holes")
    mh = base["mounting_holes"]
    for x, y in mh["positions"]:
        hit = [p for p in model["holes"] if abs(p["x"] - x) <= tol and abs(p["y"] - y) <= tol]
        if not hit:
            rep("FAIL", f"no mounting hole at ({x}, {y})")
            continue
        p = hit[0]
        drill, pad = p["drill"], p["pad"]
        ok = abs(drill - mh["drill"]) <= tol and pad >= mh["pad_min"] - tol and p["net"] == mh["net"]
        rep("OK" if ok else "FAIL", f"{p['ref']} ({x}, {y}): drill {drill:.2f}, pad {pad:.2f}, net {p['net'] or 'none'}"
            f" (required drill {mh['drill']}, pad >= {mh['pad_min']}, on {mh['net']})")

    out("== 30-Pin header")
    hd = base["header"]
    cands = model["header"]["candidates"]
    if len(cands) != 1:
        rep("FAIL", f"cannot find a unique 30-pad header (candidates {cands}), specify with --header")
        return 1
    hf = next(c for c in model["components"] if c["ref"] == cands[0])
    on_bottom = hf["side"] == "bottom"
    rep("OK" if on_bottom == (hd["side"] == "bottom") else "FAIL",
        f"{hf['ref']} is on the {'bottom' if on_bottom else 'top'} side (required: bottom)")
    want = expected_header_pads(hd)
    pads = {int(p["num"]): p for p in hf["pads"] if p["num"].isdigit()}
    off = []
    for n, (x, y) in want.items():
        p = pads.get(n)
        if p is None or abs(p["x"] - x) > tol or abs(p["y"] - y) > tol:
            off.append(n)
    rep("OK" if not off else "FAIL", "all 30 pad positions match the baseline" if not off else f"pad positions off: {off}")
    pad_nets = {n: p["net"] for n, p in pads.items()}
    for level, msg in check_pin_nets(pad_nets, base["pins"]):
        rep(level, msg)

    out("== ID card")
    idc = base["id_card"]
    ee = [c for c in model["components"] if idc["part_pattern"] in c["value"].upper()]
    if len(ee) != 1:
        rep("FAIL", f"found {len(ee)} {idc['part_pattern']} (expected 1): {[c['ref'] for c in ee]}")
    else:
        e = ee[0]
        pn = {p["num"]: p["net"] for p in e["pads"]}
        v3_net = pad_nets.get(base["pins"]["3V3"][0], "")
        addr, why = eeprom_address(pn, {v3_net, pn.get(idc["pins"]["VCC"], "")} - {""}, "GND", idc["pins"], idc["address_base"])
        if addr is None:
            rep("FAIL", f"{e['ref']}: {why}")
        else:
            # SPEC-004 V2.2: no programming by default, the model is told apart by address (table 2.2); 0x57 is the programmed card
            model_name = idc["address_table"].get(str(addr), "not in the address table")
            exp = int(expect_address, 16) if expect_address else None
            if exp is None:
                level, tip = "WARN", "; --expect-address not given, confirm by hand that the address matches this board's model (SPEC-004 2.2)"
            elif exp != addr:
                level, tip = "FAIL", f"; this board should be 0x{exp:02X} ({idc['address_table'].get(str(exp), 'not in the address table')})"
            else:
                level, tip = "OK", ""
            if addr == idc["address_burn"] and level != "FAIL":
                tip += "; programmed card: write the 12-byte descriptor at the factory and register the product_id"
            rep(level, f"{e['ref']} address 0x{addr:02X}: {model_name}{tip}")
        wp = pn.get(idc["pins"]["WP"], "")
        rep("FAIL" if wp in ("GND", "") else "OK", f"WP is on {wp or 'floating'} (required: pulled up to 3V3 with a test point, never tied directly to ground)")
        if pn.get(idc["pins"]["VCC"]) != v3_net:
            rep("FAIL", f"ID card VCC is on {pn.get(idc['pins']['VCC'])}, should be on the 30-Pin 3V3 ({v3_net})")

    lim = base["limits"]
    out("== Silkscreen")
    small = [t["ref"] for t in model["silk"]["items"]
             if t["height"] < lim["silk_text_height_mm"] - 1e-3 or t["thickness"] < lim["silk_text_thickness_mm"] - 1e-3]
    msg = f"silk text height >= {lim['silk_text_height_mm']} mm, stroke width >= {lim['silk_text_thickness_mm']} mm"
    if small:
        rep("FAIL", msg + f": NOT MET {sorted(set(small))}")
    elif model["silk"]["height_verified"]:
        rep("OK", msg)
    else:
        # The relation between EasyEDA fontSize and the rendered glyph height is not measured, so do not give a seemingly passing verdict
        rep("WARN", msg + ": stroke width met; text height is only estimated from fontSize (mil) as met, the rendered glyph height is not measured, measure it once in the EDA before ordering")

    out("== Manual review (not judged by this script)")
    for s in (f"Top-side parts <= {lim['top_height_mm']} mm above the board; bottom side excluding the header <= {lim['bottom_height_mm_excl_header']} mm (measured on the 3D models)",
              f"5V continuous <= {lim['current_5v_continuous_a']} A; 3V3 <= {lim['current_3v3_ma']} mA; all heavy loads on 5V",
              "With only 3V3 powered and 5V absent, nothing back-feeds through IO; total 5V input capacitance is recorded in the design doc",
              "8 multiplexed-zone lines have series resistors near the header; the ID card I2C has no pull-ups on the backpack, 100 Ohm in series each; ESP_INT is open-drain, active low",
              "product_id has been requested per SPEC-004 and recorded in the design doc and the registry",
              "With impedance-controlled lines: PCB platform and stackup recorded in the design doc and recomputed at the check points, trace width/spacing per SPEC-003 6.5; hubo-kicad impedance_check.py has no FAIL; impedance control ticked when ordering",
              "With signals above class L: the design doc lists signal classes, hubo-kicad hs_review.py per SPEC-005 chapter 9 has no NOT MET, multiplexed-zone signal vias have a ground via within 1 mm (SPEC-003 6.6, 6.2.5)",
              "Enclosure fits without interference; test points (WP etc.) reachable once enclosed"):
        out(f"  [ ]    {s}")
    out("Result: " + ("has FAIL" if fails else "no FAIL in automatic checks"))
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", help=".kicad_pcb or .epro2")
    ap.add_argument("--board", dest="pcb", default=None, help="pick the PCB when an .epro2 holds several (PCB name, board name or uuid)")
    ap.add_argument("--header", default=None, help="30-Pin header reference; by default the footprint with 30 pads is found automatically")
    ap.add_argument("--expect-address", "--allow-address", dest="expect_address", default=None,
                    help="address of this board's model in the SPEC-004 2.2 no-programming address table (e.g. 0x53); --allow-address is the old name")
    ap.add_argument("--baseline", default=str(HERE.parent / "assets" / "p4_backpack_baseline.json"))
    a = ap.parse_args(argv)
    base = json.load(open(a.baseline, encoding="utf-8"))
    ext = pathlib.Path(a.board).suffix.lower()
    if ext == ".kicad_pcb":
        if a.pcb:
            raise SystemExit("--board is only for .epro2")
        model = load_kicad(a.board, a.header)
    elif ext == ".epro2":
        model = load_epro2(a.board, a.header, a.pcb)
    else:
        raise SystemExit(f"unrecognized extension {ext}: only .kicad_pcb and .epro2 are supported")
    return run_checks(model, base, a.expect_address)


if __name__ == "__main__":
    sys.exit(main())
