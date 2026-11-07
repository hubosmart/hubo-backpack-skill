---
name: hubo-backpack
description: >
  Design or review a Hubo-Tack 30-pin backpack board: follow the public 30-pin spec
  (SPEC-003) and ID-card spec (SPEC-004), check outline, mounting holes, header position
  and pad nets, ID-card address and write protection with backpack_check.py (KiCad .kicad_pcb
  or EasyEDA .epro2), check component heights with height_check.py, generate the 0x57
  ID-card image with idcard_image.py. Start a new board from the blank KiCad template.
  Triggers: "design a new backpack", "start a backpack from the template", "check my backpack",
  "can this backpack go to fab", "how to wire 5V/3V3 on a backpack", "flash the ID card",
  "height check".
  Not for: KiCad/MCP operations (use hubo-kicad); operating a running EasyEDA client
  (use the official easyeda-api-skill); pin-hazard audits (a separate tool, not in this
  repository); the mainboard itself. Backpacks built to this spec plug into Hubo-Tack
  (ESP32-P4, the baseline host), S3 hosts and CM series (in development); the three share
  the same connector and the same power and ground pins, and the functional differences
  are in SPEC-003 1.4.
user-invocable: true
---

## Summary

Design or review a plug-in backpack for the Hubo-Tack 30-pin connector. The rules live in the
public spec repo (https://github.com/hubosmart/hubo-backpack-spec, SPEC-003 / SPEC-004); this skill
carries only machine-readable data (`assets/p4_backpack_baseline.json`) and checkers.
Hard rules: large loads (RF, LED, amplifier, motor, codec) take `5V` and regulate it on the
backpack; `3V3` is for the ID card and pull-ups only (total <= 50 mA); 5V total <= 2 A; the mainboard
has no current limiting between it and the 30-pin, so the backpack should protect itself (the template includes an eFuse); a DIY ID
card is the programmed 0x57 variant and must be flashed with a descriptor
(product_id in the DIY range 0xF000-0xFFFE).
Tools: `scripts/backpack_check.py` (outline, holes, header, nets, ID card), `scripts/height_check.py`
(height limits from 3D models), `scripts/idcard_image.py` (descriptor image for the 0x57 card).
Start a new design from `templates/backpack/kicad/hubo-backpack-template.kicad_pro`.
The last gate is always a human: passing checks does not mean the board is ready to order.

## Where the rules come from

**The spec text lives in the hubo-backpack-spec repository (https://github.com/hubosmart/hubo-backpack-spec); this skill does not copy clauses.** Read these documents from that repository every time you use the skill:

| Spec | Content |
|---|---|
| `SPEC-003` Backpack schematic and layout spec | Mechanics, 30-pin interface and power, schematic, layout, enclosure, pre-fab checklist |
| `SPEC-004` ID-card address plan and product_id spec | Address plan, forbidden addresses, product_id numbering, programming |

Check the version first: if the version in the spec header differs from `spec_version` in `assets/p4_backpack_baseline.json`, **update the baseline to the new spec before checking**, and tell the user. Values marked "to be measured" (height limits, current) will be revised after prototype measurements; say so when you quote them.

## New backpack

Start from `templates/backpack/kicad/hubo-backpack-template.kicad_pro` in this repository: copy the whole `kicad/` directory, rename the project, then design inside it. Do not change what the template already provides: the 83×40 board outline, the 4 mounting holes, the 30-pin header and its nets, ID card U1, and the 5V protection.

- **The top side is blank for you**: put your circuit on the top side.
- **Draw power from `5V_USER`**: it is a whole inner PWR layer carrying protected 5V. 3V3 is for small loads only, total <= 50 mA; 5V total <= 2 A, and large loads regulate it themselves.
- **Multiplexed zone**: 8 pads (7, 8, 9, 10, 25, 27, 28, 29) connect through series resistors R21-R28 (0 Ω) to the `USR_P*` nets and test points TP21-TP28. Your circuit decides which to use and for what function.
- **ID card U1 is the programmed 0x57 variant**: a DIY board must flash a descriptor, with `product_id` in the DIY range 0xF000-0xFFFE. Generate the image with `scripts/idcard_image.py`, pull TP3 (WP) low to write it, and restore the pull-up afterwards.
- When done, run `backpack_check.py` and `height_check.py` (below), then work through the manual check items.

## What to do

### 1. Project start and schematic stage

- Decide power per SPEC-003 chapter 3: connect large loads (RF, LED, amplifier, motor, codec) to `5V` and regulate it yourself; `3V3` is only for the ID card, pull-ups/pull-downs and other small loads. There is no current limiting or switch between the mainboard and the 30-pin, so the backpack must provide its own protection.
- Draw the schematic with the **backpack pad numbers** per SPEC-003 3.1 (the mainboard `U1` pin numbers are for cross-checking only; the two are mirrored).
- ID card per SPEC-004: official models reserve 0x50-0x56 according to the address table; every DIY backpack uses the programmed 0x57 variant, flashed at the factory with a descriptor (12 bytes) and a `product_id` (DIY range 0xF000-0xFFFE); always put a 10 kΩ pull-up and a test point on `WP`.
- Choose 2 or 4 layers per SPEC-003 2.2 and record the reason in the design document.
- If the board has RF, USB, HDMI, MIPI or other impedance-controlled traces, **first ask which platform will make the PCB and which stackup it uses** (never assume), then set trace width and spacing per SPEC-003 6.5. For the steps, see `references/08_impedance_control.md` in `hubo-kicad`.

### 2. Layout stage

Place the outline, mounting holes and header exactly as the baseline gives them (values in SPEC-003 chapter 2 or the baseline file). For the actual KiCad operations, routing and inner-plane checks, use `hubo-kicad`.

Placement rules (SPEC-003 2.11–2.14, 4.8); the scripts do not check these yet, so confirm them by eye before fab:

- **External connectors on the right edge**: anything plugged in or pulled by a cable (USB-C, HDMI, RJ45, SMA, buttons, DIP switches) goes on the right board edge (positive X), opposite the header. Nothing external on the header side (left edge). If the enclosure forces one onto the top or bottom edge (for example a TF card socket), record the position and the reason in the design document.
- **The enclosure takes insertion force**: a plug or heavy connector that sticks out beyond the board edge needs its body supported by the enclosure; the solder joints must not carry it alone.
- **Reachable from the side**: the unit usually lies flat with the backpack down, so user controls must be reachable from a side and status LEDs light-piped to a side (or seen through a window that stays visible when lying flat).
- **Internal cable connectors on the top edge**: camera FPCs, GNSS U.FL and other cables that stay inside the enclosure go on the top edge, away from the right-edge connector area.
- **ID card near pads 14/15**: keep `SCL`/`SDA` short; put the WP test point where it can be reached with the enclosure fitted.

### 3. Pre-fab checks

```bash
# KiCad board: must use KiCad's bundled python (depends on pcbnew)
"<KiCad install dir>/bin/python.exe" scripts/backpack_check.py <board.kicad_pcb> [--header H1] [--expect-address 0x57]
# EasyEDA Pro project: any python 3 works; reads .epro2 (a zip) directly, no EDA client or bridge needed
python scripts/backpack_check.py <project.epro2> [--board PCB1] [--header H1] [--expect-address 0x57]
# Height limits: read the height of each footprint's 3D model (STEP)
python scripts/height_check.py <board.kicad_pcb> --models-dir "<KiCad install dir>/share/kicad/3dmodels"
```

The board reader is chosen by file extension. When an `.epro2` holds several PCBs, select one with `--board` (PCB name, board name or uuid); with a single PCB it is not needed. Both sources go through the same checks, with the same output text and levels; the only differences are the ones listed under "Known limitations" below.

| Automatic check | Verdict |
|---|---|
| Outline 83×40, R2 corners, 2/4 layers | FAIL on size mismatch |
| 4 M2 mounting holes: position, hole diameter 2.0, pad >= 3.0, connected to GND | FAIL on mismatch |
| 30-pin header on the bottom side, positions of the 30 pads | FAIL on mismatch |
| Header pad nets: 5V / 3V3 / GND groups, 5V != 3V3 | FAIL on mismatch; WARN if a pad that is floating by default is used |
| ID card: single AT24C02, address derived from A0-A2, WP not tied directly to ground, VCC on 30-pin 3V3 | FAIL if it differs from `--expect-address`; WARN when not specified, listing the model for that address; 0x57 reminds you to flash the descriptor |
| Silkscreen reference/value: text height >= 1.0 mm, line width >= 0.15 mm | FAIL if not met; for `.epro2` the text height can only be estimated from `fontSize`, so even a pass gives only WARN (see known limitations) |
| Height (`height_check.py`): top <= 3.9 mm, bottom <= 1.7 mm (header excluded) | FAIL if exceeded; no model, a non-STEP model, or a rotation about X/Y is recorded as UNCHECKED, needs manual confirmation, and does not count as a pass |

Coordinates are relative to the drill/coordinate origin (the board outline center if none is set), so the check also works after the board was translated in the UI. For `.epro2`, the reference is the canvas origin (0,0) at the board outline center; otherwise it likewise falls back to the outline center and prints that. At the end the script lists the **manual check items** (height, current budget, back-feeding, series resistors and pull-ups, product_id, enclosure); the check is complete only after you give a conclusion for each one. Then run the `hubo-kicad` DRC / consistency / `plane_check.py` / export flow.

Self-test: `python tests/run_all.py`; the last line should be `ALL PASS`.

### 4. Flash the ID card

```bash
python scripts/idcard_image.py --form-id 0 --product-id 0xF001 --hw-ver 0x10 -o idcard.bin
```

This generates the descriptor image (16 bytes) for the programmed 0x57 variant and self-checks it (see `--help` for the parameters). Pull TP3 (WP) low, write it with a programmer, then release it. product_id must fall in the DIY range 0xF000-0xFFFE.

## Why it is designed this way

- **Clauses are not copied into the skill**: the spec gets revised, and a copy will sooner or later diverge from the original. The skill carries only machine-checkable baseline data (the baseline file) and compares the version number before use.
- **Automate only what a machine can decide**: outline, hole positions, pad positions, net grouping and ID-card wiring can be judged directly from the PCB file. Current needs load data, so it is a manual list rather than a seemingly passing verdict. Height depends on 3D models; whatever cannot be found is honestly recorded as unchecked.
- **Check the address against the model, not just for legality**: every wiring is "legal"; the real error is wiring the address of a different model (the host would then configure the multiplexed zone for the wrong form). So the script requires `--expect-address` to name this model's address, and only gives a WARN when it is not specified.
- **Board reading is separate from judging**: `load_kicad` and `load_epro2` read a project into one neutral board model (mm, relative to the origin, top view with X right and Y down), and `run_checks` draws conclusions from the model only. Supporting a new board file format only needs a new loader; the judging logic stays untouched, and every verdict can be unit-tested with a synthetic model.
- **Parse `.epro2` offline, without an EDA client**: the script reads `.epro2` directly (the `.epru` line text inside the zip) and depends on no EDA client, bridge service or third-party library; it checks the same file used for backup and ordering, so results are reproducible. The cost is that the format is based on measurement rather than an official guarantee (editorVersion 3.2.x); re-verify with samples after a major version change.

## Known limitations

- **Text height in `.epro2` is unreliable**: the `strokeWidth` (mil) of reference text converts directly, so the line-width verdict is reliable; the relation between `fontSize` and the actual glyph height has not been measured, so the height is only estimated from `fontSize` (mil). An insufficient estimate is a FAIL; a sufficient one gives a WARN asking you to measure once in the EDA before fab, never an OK.
- **`.epro2` has no "drill/coordinate origin"**: the reference is the canvas origin (0,0) coinciding with the board outline center (tolerance 0.05 mm); otherwise the outline center is used.
- **The board outline parser handles only lines and arcs (including R rectangles and circles)**: an outline containing Bezier curves makes it exit with an error rather than guess.
- **Only pads with holes count as mounting holes**: a standalone non-plated "hole" primitive (no pad) in the EDA is not recognized and reports "no mounting hole at (x, y)". A mounting hole must have a pad >= 3.0 connected to GND anyway; draw it as a pad with a hole per the spec.
- **ID-card address pins selected through a 0 Ω/resistor cannot be judged**: when the net on A0-A2 is neither 3V3 nor GND, the script reports FAIL "level undetermined"; KiCad and `.epro2` behave the same. For such boards the designer confirms the soldered state manually and records it in the design document.
- `.epro2` components have no KiCad Value field, so model matching concatenates the reference-designator attribute, device name and manufacturer part number and searches for `24C02`.
- **`height_check.py` reads KiCad boards only**, and handles only STEP models on footprints not rotated about X/Y; everything else is recorded as UNCHECKED.

## What this skill does not do (boundaries)

- It does not choose components or decide the power scheme for the designer; it only lays out the spec requirements and the check conclusions.
- It does not assign product_id: official ranges are assigned by the project maintainers; for DIY, pick one from 0xF000-0xFFFE yourself and make sure it does not repeat that of another backpack on the same device.
- It does not cover mainboard design.
- Passing the checks does not mean you can order directly; the last gate must be a human.
