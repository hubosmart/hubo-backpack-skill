"""3D board-edge overhang check: orthographic top/bottom renders, counting component pixels that fall outside the board outline.

Why: DRC only checks copper-to-edge distance, not component bodies; the footprint's own courtyard is often shorter than the real body (FPC1 is 1.6 mm short),
so connectors and side-button switches overhanging the board edge can only be found in 3D.
Method: render twice from the same viewpoint - with 3D models, and with all models removed (bare board); the non-background area of the bare-board image is the board outline, non-background pixels of the model image outside it are overhang.
The camera frames automatically from the board outline (models do not affect framing), so the two images match pixel for pixel.
Usage: <KiCad>/bin/python edge_check.py <board.kicad_pcb> [--tol 0.1]
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kicad import kicad_cli, load_pcbnew  # noqa: E402

pcbnew = load_pcbnew()
CLI = kicad_cli()
W, H = 3000, 1600


def render(board, side, out):
    subprocess.run([CLI, "pcb", "render", "--side", side, "-w", str(W), "-h", str(H), "--quality", "basic",
                    "--background", "opaque", "-o", out, board], check=True, capture_output=True)
    return np.asarray(Image.open(out).convert("RGB")).astype(int)


def objects(a):
    """The background is a vertical gradient: take the leftmost pixel of each row as that row's background; a difference above the threshold is an object."""
    return np.abs(a - a[:, :1, :]).sum(axis=2) > 30


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--tol", type=float, default=0.1, help="Allowed overhang in mm (render antialiasing is about 1 pixel)")
    a = ap.parse_args()
    # Put the temporary board inside the project directory: ${KIPRJMOD} resolves to the board's directory; a system temp dir would lose project-local 3D models
    tmp = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(a.board)), prefix="_edge_")
    full = os.path.join(tmp, "..", "_edge_full.kicad_pcb")
    bare = os.path.join(tmp, "..", "_edge_bare.kicad_pcb")
    shutil.copy(a.board, full)
    b = pcbnew.LoadBoard(full)
    bb = b.GetBoardEdgesBoundingBox()
    bw, bh = pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())
    x0, y0 = pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop())
    # Component outline (no text); overhanging pixels are attributed to the nearest component outline
    refs = []
    for f in b.GetFootprints():
        if not len(f.Models()):
            continue
        fb = f.GetBoundingBox(False)
        refs.append((f.GetReference(), pcbnew.ToMM(fb.GetLeft()), pcbnew.ToMM(fb.GetTop()),
                     pcbnew.ToMM(fb.GetRight()), pcbnew.ToMM(fb.GetBottom())))

    def owner(X, Y):
        return min(refs, key=lambda r: max(r[1] - X, 0, X - r[3]) ** 2 + max(r[2] - Y, 0, Y - r[4]) ** 2)[0]
    for f in b.GetFootprints():
        f.Models().clear()
    b.Save(bare)
    over = {}
    for side in ("top", "bottom"):
        i_full = render(full, side, os.path.join(tmp, f"f_{side}.png"))
        i_bare = render(bare, side, os.path.join(tmp, f"b_{side}.png"))
        m_bare = objects(i_bare)
        ys, xs = np.nonzero(m_bare)
        px0, px1, py0, py1 = xs.min(), xs.max(), ys.min(), ys.max()
        sx = (px1 - px0) / bw
        sy = (py1 - py0) / bh
        tol_px = max(2, int(a.tol * sx))
        out = np.abs(i_full - i_bare).sum(axis=2) > 30
        out[py0 - tol_px:py1 + tol_px + 1, px0 - tol_px:px1 + tol_px + 1] = False
        if not out.any():
            continue
        ys, xs = np.nonzero(out)
        # Map pixel position back to board coordinates (the bottom view is mirrored left-right), attribute to the nearest component
        mx = (xs - px0) / sx
        if side == "bottom":
            mx = bw - mx
        mx = mx + x0
        my = (ys - py0) / sy + y0
        for X, Y in zip(mx, my):
            d = max(x0 - X, X - (x0 + bw), y0 - Y, Y - (y0 + bh))
            ref = owner(X, Y)
            over[ref] = max(over.get(ref, 0), d)
    shutil.rmtree(tmp, ignore_errors=True)
    for f in (full, bare):
        for ext in (".kicad_pcb", ".kicad_prl", ".kicad_pro"):
            q = f[:-len(".kicad_pcb")] + ext
            if os.path.exists(q):
                os.remove(q)
    for ref, d in sorted(over.items(), key=lambda kv: -kv[1]):
        print(f"{ref} overhangs the board edge by {d:.2f} mm")
    print("FAIL" if over else f"PASS: no component overhangs the board edge (tolerance {a.tol} mm)")
    return 1 if over else 0


if __name__ == "__main__":
    raise SystemExit(main())
