"""3D model audit: list the 3D models per footprint and check that model files resolve on disk.

Why: when a model is missing, kicad-cli STEP/GLB export prints only one warning line and the part vanishes from 3D, so height and enclosure interference checks miss it.
Usage (KiCad's bundled python):
  python model_audit.py <board.kicad_pcb> [--no-model-ok TestPoint: MountingHole] [--var NAME=path ...]
  --no-model-ok: footprint library-name prefixes whose footprints are fine without a model (test points and mounting holes are pads only).
  ${KIPRJMOD} is the board file's directory; ${KICADn_3DMODEL_DIR} defaults to share/kicad/3dmodels next to kicad-cli.
"""
import argparse
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kicad import kicad_cli, load_pcbnew  # noqa: E402


def resolve(path, env):
    return re.sub(r"\$\{([^}]+)\}", lambda m: env.get(m.group(1), m.group(0)), path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--no-model-ok", nargs="*", default=["TestPoint:", "MountingHole"])
    ap.add_argument("--var", nargs="*", default=[], help="Extra path variables NAME=path")
    a = ap.parse_args()
    pcbnew = load_pcbnew()
    b = pcbnew.LoadBoard(a.board)
    share = pathlib.Path(kicad_cli()).resolve().parent.parent / "share" / "kicad" / "3dmodels"
    env = {k: v for k, v in os.environ.items()}
    env["KIPRJMOD"] = os.path.dirname(os.path.abspath(a.board))
    for n in range(6, 12):
        env.setdefault(f"KICAD{n}_3DMODEL_DIR", str(share))
    for kv in a.var:
        k, v = kv.split("=", 1)
        env[k] = v
    seen = {}
    for f in sorted(b.GetFootprints(), key=lambda f: f.GetReference()):
        fid = f.GetFPIDAsString()
        if fid in seen:
            seen[fid][0].append(f.GetReference())
            continue
        models = [m.m_Filename for m in f.Models() if m.m_Show]
        if not models:
            st = "OK (no model needed)" if any(x in fid for x in a.no_model_ok) else "FAIL no model"
        else:
            miss = [m for m in models if not os.path.isfile(resolve(m, env))]
            st = "FAIL file not found: " + ", ".join(miss) if miss else "OK"
        seen[fid] = [[f.GetReference()], st, ", ".join(os.path.basename(m) for m in models) or "—"]
    print("| Footprint | Ref | Model | Result |\n|---|---|---|---|")
    bad = 0
    for fid, (refs, st, mdl) in seen.items():
        print(f"| {fid} | {', '.join(refs)} | {mdl} | {st} |")
        bad += st.startswith("FAIL")
    print(f"\n{len(seen)} footprint(s), FAIL {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
