# 02 EasyEDA Pro to KiCad: conversion and verification

**Principle: the original EasyEDA project is read-only.** Copy it to a new project directory first (set the read-only attribute and record the sha256) before working; do all unpacking, renaming and exporting on the copy.

## Conversion paths (measured with EasyEDA Pro 3.2)

| Path | Result | Notes |
|---|---|---|
| `.epro2` → KiCad's EasyEDA Pro importer | ❌ empty board | `.epro2` from 3.x is not recognized |
| EasyEDA exports Altium → KiCad Altium importer | ❌ | EasyEDA exports only **ASCII** Altium, while KiCad supports only binary (pcbnew segfaults outright). But ASCII can be parsed as text, so it **works as an independent reference** (pad nets, component Unique ID) |
| **EasyEDA exports PADS → `kicad-cli pcb import --format pads`** (= MCP `import_pcb(format="pads")`) | ✅ usable, with 5 classes of defects to fix | Recommended |
| EasyEDA Pro direct export to KiCad | Not verified | If available, verify it the same way as below |

**The schematic is not converted**: PADS carries only the PCB. Rebuild the schematic with MCP as needed (see 03), then associate it with the PCB.
`.epro2` is itself a zip containing JSON-line documents; when you need the schematic netlist, unpack it on the copy and parse it with Python (the pad nets in the PCB document are the netlist).

## PADS import defects and fixes (mandatory)

| # | Defect | Impact | Fix |
|---|---|---|---|
| 1 | Some component type names (containing `x`, or long model names) fail to parse | Footprints have no pads (connectors most often) | Before import, rename the component type names in the PADS file to short names `PT00`~ |
| 2 | The EasyEDA PCB uses the silkscreen "Name" as the reference designator (mostly the component value, and duplicated) | All reference designators wrong | Map the schematic `Unique ID` to the Altium ASCII `SOURCEUNIQUEID` and write them back |
| 3 | Duplicate reference designators lose pad connections | Pads have no net | Rewrite from the Altium ASCII pad nets |
| 4 | Inner planes are negative (PADS `PLANE CAM`) | The inner layer is empty and the RF reference ground is lost | Rebuild that layer's copper pour from the board outline, taking clearance and thermal-relief parameters from the original board's rules |
| 5 | Bottom-layer pads are read as "squares not on any copper layer"; duplicate traces | Wrong geometry | Rebuild pads from the Altium data; delete fully coincident traces |

Also restore and clean up:
- **Board outline notches** may be lost; restore them from the EasyEDA Gerber (a notch in the EasyEDA outline layer is a closed rectangle straddling the board edge, not part of the outline);
- **Copper pour clearance**: use the value measured on the EasyEDA Gerber, not the default after import;
- **Import leftovers** (must be cleaned before ordering): PADS record fragment text on the `User.Comments` layer (some off the board), silkscreen of deleted components, keepouts of deleted components, and dangling vias connected at one point only.

## Project organization

- Separate directories: `source/` (read-only copy of the original + sha256), `import/` (conversion outputs and reports), `kicad/` (project and local libraries), `output/` (production files), `docs/`;
- Put imported footprints into the project-local library (`kicad/lib/xxx.pretty`), and use `${KIPRJMOD}` relative paths in the library tables.

## Verification after conversion (mandatory)

**Every item needs an independent source; "the tool returned success" does not count as checking.**

| Check | Source | Method |
|---|---|---|
| Pad nets | Altium ASCII | Compare net pad by pad |
| Copper geometry, board outline, RF zone | **EasyEDA production Gerber** (a board already in volume production is the ground truth) | gerbonara + shapely: RF trace to pour clearance, RF-zone copper XOR, board outline area |
| Position and size of every pad | EasyEDA production Gerber | Convert coordinates using known points such as screw holes, compare pad by pad (deviation <= 0.02 mm) |
| Frozen parts (RF, modules, connectors) | Baseline snapshot after import | After every change, automatically compare coordinates, angle and layer (coordinates relative to the drill/coordinate origin, see 06) |
| Impedance | JLCPCB stackup parameters + impedance calculator | Do not use the stackup parameters in the Altium file (they are defaults) |
| ERC / DRC | KiCad | `run_erc`, `kicad-cli pcb drc --schematic-parity --severity-all` |

- When a schematic tool reports `schematic_load_failed`, run `repair_flat_symbols(path)` first;
- Run `lint_offgrid` in report-only mode first, and use `fix: true` only after confirming.
