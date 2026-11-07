"""STEP solid bounding box (only points referenced by VERTEX_POINT; coordinate systems, planes and other helper geometry are excluded).

Why: JLCPCB (EasyEDA) 3D models are positioned by the model bounding box - the XY box center aligns with `c_origin` and z puts the lowest point on the board surface;
KiCad uses the STEP origin directly. If the STEP box is asymmetric or the origin is not on the bottom face, copying the offset as-is misplaces the part or sinks it into the board.
Conversion: KiCad offset x = JLCPCB value - box center x; y = JLCPCB value - box center y; z = JLCPCB z - lowest point z.
Note: for STEP files with assembly transforms (rotation inside ASSEMBLY) this gives the part's own coordinates; verify with a 3D render.
Usage: python step_bbox.py <a.step> [...]   (pcbnew not needed)
"""
import re
import sys

NUM = r"([-\d.Ee+]+)"


def bbox(text):
    """Return ((xmin, xmax), (ymin, ymax), (zmin, zmax)); None if there are no solid vertices."""
    pts = {m.group(1): tuple(map(float, m.groups()[1:])) for m in re.finditer(
        r"#(\d+)\s*=\s*CARTESIAN_POINT\s*\(\s*'[^']*',\s*\(\s*%s\s*,\s*%s\s*,\s*%s\s*\)" % (NUM, NUM, NUM), text)}
    vs = [pts[i] for i in re.findall(r"VERTEX_POINT\s*\(\s*'[^']*',\s*#(\d+)", text) if i in pts]
    if not vs:
        return None
    return tuple((round(min(p[k] for p in vs), 3), round(max(p[k] for p in vs), 3)) for k in range(3))


def main(paths):
    for f in paths:
        bb = bbox(open(f, encoding="latin1").read())
        name = f.replace("\\", "/").split("/")[-1]
        print(name, "no solid vertices" if bb is None else f"x {bb[0]} y {bb[1]} z {bb[2]}")


if __name__ == "__main__":
    main(sys.argv[1:])
