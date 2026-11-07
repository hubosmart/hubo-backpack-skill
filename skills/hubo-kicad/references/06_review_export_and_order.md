# 06 Review, export and ordering

## Review (DRC 0 errors does not mean ready to order)

- `kicad-cli pcb drc --refill-zones --save-board --schematic-parity --severity-all`: the goal is **0 errors, 0 warnings, 0 unconnected, 0 parity issues**, or a stated reason for every exception;
- **Open it in the KiCad GUI and look**: checking only the CLI errors misses many warnings (text height too small, silkscreen over pads, floating vias) and cannot show leftover imported text;
- **3D viewer**: footprints imported from PADS have no 3D models. Attach KiCad's bundled models for standard parts; for key parts, download the STEP by part number from the EasyEDA public model library (the `lceda.cn` interface works, `easyeda.com` returns 403). After attaching, **render and check pin 1 orientation and alignment**; unmounted (DNP) components are hidden by default in the 3D viewer, which does not mean the model was forgotten;
  - EasyEDA model offsets must be converted: EasyEDA positions a model by its bounding box (XY box center, lowest z point), while KiCad uses the STEP origin; if the box is asymmetric, copying the offset directly misaligns the model or sinks it into the board. Get the bounding box with `scripts/step_bbox.py <step>` and convert as it describes;
  - `scripts/model_audit.py <board>`: for each footprint type, checks that the model file can be parsed (a part with a missing model simply disappears from the GLB);
  - `scripts/edge_check.py <board>`: renders orthographically twice, with models and bare board, and reports parts protruding past the board outline. DRC does not check the component body, and the courtyard shipped with a footprint is often shorter than the real part (the EasyEDA FPC connector only draws half of the body);
- **Footprints and pins**: for a newly added part, check the pin definitions against the manufacturer datasheet, **looking at the original datasheet image** rather than trusting extracted text (a different pin layout on a generic SOT-23 package page was once taken as the BSS138's);
- **Board-edge SMA**: when the metal flange sits at the board edge, **only the signal pad** must keep a distance from the edge (for example 2 mm); the ground pads are GND like the flange and may be close to the edge to keep the soldering length; confirm the pin protrusion length for the exact model used (the same name from a different maker can differ by a factor of two);
- **Power audit and component spacing**: worst-case voltage, ferrite bead rated current and DCR, per-rail decoupling, and the spacing between adjacent 0402 pads, following the review steps of this skill;
- **Inner planes**: run `plane_check.py` per 05;
- **Silkscreen**: JLCPCB recommends text height >= 1.0 mm.

## Coordinate origin

If the whole board was moved in the KiCad GUI, scripts that check absolute coordinates, enclosure coordinates and production files will all be offset. Fix:
1. Work out the translation (compare the coordinates of the same component before and after, to the nm), then use `scripts/set_origin.py <board> --at X,Y` to set the drill/place origin to "where the original board center is now"; for a new project, `set_origin.py <board>` alone sets it to the board outline center;
2. In your own scripts, subtract `GetAuxOrigin()` before comparing absolute coordinates;
3. Produce production files with `export_prod.py` (`--origin auto` exports using that origin automatically); for 3D export use `kicad-cli pcb export glb --drill-origin` (the default is absolute coordinates, which differ by the translation).

## Export

```bash
"<KiCad>/bin/python.exe" scripts/export_prod.py <board.kicad_pcb> <schematic.kicad_sch> --out output [--name prefix]
```

Before exporting it automatically runs the **production gate** (`prod_gate.py`, which can also run on its own): the project config `<board name>.hz_layout.json` is valid -> `.kicad_dru` contains the base rules and the power rules match the config -> DRC (copper pours refilled, including schematic parity) has no errors and no unconnected items -> `route_check.py` -> the high-speed / impedance / plane checks listed in the config. If any item fails, **export is refused**; details are in `<out>/check_report.txt` (the check report).

- Fix the problem, do not bypass it: `--allow "who agreed + reason"` may only be used **after the user has explicitly agreed**; the waiver list is written to `<out>/gate_waiver.txt` (the waiver record) and kept with the production files;
- DRC warnings are only listed, not blocking; confirm each one and record it in the design document;

| Output | Description |
|---|---|
| `<prefix>-gerber.zip` | All copper layers (by actual layer count), solder mask, silkscreen (minus mask openings), stencil, board outline + PTH/NPTH drill; excludes the drill map `*-drl_map.gbr` |
| `gerber/` | Loose files (including the drill map, for viewing) |
| `<prefix>-bom.csv` | Columns Comment / Designator / Footprint / LCSC Part # / Quantity, grouped by value + footprint + part number, DNP excluded |
| `<prefix>-pos.csv`, `<prefix>-cpl-jlc.csv` | KiCad raw placement file and the JLCPCB CPL (Designator, Mid X, Mid Y, Layer, Rotation) |
| `check_report.txt`, `gate_waiver.txt` | Production gate details (check report); the latter (waiver record) is generated only when `--allow` waives something |

- Produce files for the fab only with `export_prod.py`: MCP export tools such as `export_gerbers`, `export_drill`, `export_pos` and `export_bom` do not go through the production gate and are for viewing only;
- MCP `export_sch_bom` drops empty-string parameters and writes reference designators as ranges like `C16-C19`, so the script calls kicad-cli directly;
- If the placement file is empty or missing components: the imported footprint type may be "unspecified"; use `set_footprint_type` to set it to SMD; mark mounting holes and test points as excluded;
- After LCSC part numbers are written into the schematic, the PCB footprint also needs a field of the same name, otherwise parity reports "missing symbol field" (write it with a pcbnew script, hidden and on the Fab layer);
- If a copper layer's display name is changed, the Gerber file names change with it (for example `-TopLayer.gtl`); JLCPCB identifies files by extension, so this is not affected.

## Pre-order check: surcharge items

Pricing rules change (in 2026-08 JLCPCB replaced its free HASL prototype coupons with OSP; ENIG coupons were kept), so this section lists only the trigger conditions and where to verify, not prices; values follow SPEC-005, and before submitting, rely on the price calculator preview and the production draft. Verified on 2026-10-17.

| Item | What to verify | Basis |
|---|---|---|
| Vias, trace width/spacing | Whether they fall below the surcharge threshold (the template and the production gate already block on the threshold) | SPEC-005 10.4, 10.2 |
| Via-in-pad | If really needed, choose the via-in-pad process when ordering; check the price calculator for any surcharge | SPEC-005 10.7 |
| Impedance | Tick it on the order page and fill in the stackup per 08; a tolerance of ±20% has no surcharge, only a tighter requirement (±10%) is charged | JLCPCB process parameters page, "±10% (charged)" |
| Surface finish | Free HASL prototype coupons became OSP (ENIG coupons unchanged); half-hole boards, button boards and gold-finger boards cannot use OSP, so choose the finish on the order page | JLCPCB announcements (2026-08-07, 2026-08-24) |
| SMT type | Economic reflow is 255±5 ℃ and not adjustable; some parts can only go through the standard type (the list is not public, rely on the matching page); standard type has a minimum size of 70×70 mm and requires a tooling rail (SPEC-005 10.5) | Process parameters page; the order page prevails |

## Ordering

- EasyEDA is not needed: upload the Gerber archive and choose layer count and board thickness; for impedance control choose the matching stackup (for example `JLC04161H-3313`);
- For assembly, also upload the BOM and CPL and check each rotation in the preview - the JLCPCB preview overlays the real part on the pads, which is another independent check of pads and orientation;
- If a part is low in stock or has unsuitable voltage/bias characteristics, replace it on lcsc.com before ordering.
