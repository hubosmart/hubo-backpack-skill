#!/usr/bin/env python3
"""Component height check for Hubo-Tack 30-pin backpacks (SPEC-003 2.6/2.7).

Reads the 3D model (STEP) height of every footprint in the PCB: top side <= 3.9 mm, bottom side <= 1.7 mm
(header excluded). A footprint with no model, a non-STEP model, or a model rotated about X/Y is reported as
UNCHECKED and needs manual confirmation; it does not count as a pass.

Usage: python height_check.py board.kicad_pcb [--models-dir <KiCad 3dmodels dir>] [--header H1]
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "hubo-kicad" / "scripts"))
from step_bbox import bbox  # noqa: E402

MODEL = re.compile(r'\(model "([^"]+)"')
NUM = r"([-\d.eE+]+)"


def _xyz(block, key, default):
    """Take the offset/scale/rotate triple from a model block; KiCad 10 writes it over several lines, hence \\s* to match across lines."""
    m = re.search(r"\(%s\s*\(xyz %s %s %s\)" % (key, NUM, NUM, NUM), block)
    return tuple(float(v) for v in m.groups()) if m else default


def _blocks(text, head='(footprint "'):
    out, i = [], 0
    while True:
        j = text.find(head, i)
        if j < 0:
            return out
        depth, k, in_str = 0, j, False
        while k < len(text):
            c = text[k]
            if in_str:
                if c == "\\":
                    k += 1
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        out.append(text[j:k + 1])
        i = k + 1


def footprints(text):
    res = []
    for b in _blocks(text):
        ref = re.search(r'\(property "Reference" "([^"]+)"', b)
        layer = re.search(r'\(layer "([FB])\.Cu"\)', b)  # the footprint's own layer; pads use (layers ...), so no false match
        m = MODEL.search(b)
        mb = b[m.start():] if m else ""
        offset = _xyz(mb, "offset", (0.0, 0.0, 0.0))
        scale = _xyz(mb, "scale", (1.0, 1.0, 1.0))
        rotate = _xyz(mb, "rotate", (0.0, 0.0, 0.0))
        res.append({"ref": ref.group(1) if ref else "?",
                    "side": "top" if not layer or layer.group(1) == "F" else "bottom",
                    "model": m.group(1) if m else None,
                    "oz": offset[2], "sz": scale[2], "rx": rotate[0], "ry": rotate[1]})
    return res


def model_height(fp, board_dir, models_dir):
    if not fp["model"] or fp["rx"] or fp["ry"]:
        return None
    path = fp["model"].replace("${KIPRJMOD}", str(board_dir))
    path = re.sub(r"\$\{KICAD\d*_3DMODEL_DIR\}", str(models_dir).replace("\\", "/"), path)
    p = Path(path)
    if p.suffix.lower() not in (".step", ".stp") or not p.exists():
        return None
    bb = bbox(p.read_text(encoding="latin1"))
    return None if bb is None else round(bb[2][1] * fp["sz"] + fp["oz"], 3)


def judge(fp, height, limits, header_ref):
    if fp["ref"] == header_ref:
        return "SKIP"
    if height is None:
        return "UNCHECKED"
    lim = limits["top_height_mm"] if fp["side"] == "top" else limits["bottom_height_mm_excl_header"]
    return "OK" if height <= lim + 1e-6 else "FAIL"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("--models-dir", default=os.environ.get("KICAD10_3DMODEL_DIR", ""))
    ap.add_argument("--header", default="H1")
    ap.add_argument("--baseline", default=str(HERE.parent / "assets" / "p4_backpack_baseline.json"))
    a = ap.parse_args(argv)
    limits = json.loads(Path(a.baseline).read_text(encoding="utf-8"))["limits"]
    board = Path(a.board)
    fails = unchecked = 0
    for fp in footprints(board.read_text(encoding="utf-8")):
        h = model_height(fp, board.parent, a.models_dir)
        v = judge(fp, h, limits, a.header)
        fails += v == "FAIL"
        unchecked += v == "UNCHECKED"
        if v != "OK":
            print(f"  [{v:<9}] {fp['ref']} {fp['side']} {'' if h is None else f'{h} mm'}")
    print(f"Result: {'has FAIL' if fails else 'no FAIL'}; "
          f"{unchecked} unchecked, review by hand")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
