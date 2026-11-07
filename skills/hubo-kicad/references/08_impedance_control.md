# Impedance control (RF, high-speed differential, USB)

The single place where the values are maintained: `SPEC-003` 6.5 (see the hubo-backpack-spec repository https://github.com/hubosmart/hubo-backpack-spec); this skill's `assets/impedance_table.json` is its machine-readable copy (if `spec_version` differs, update from the spec first).

## When to do it

After the schematic is frozen and before PCB work begins. If the board has any of RF traces, USB, HDMI, MIPI, LVDS or Ethernet, you must first complete steps 1-4, then generate the high-speed traces and write the net classes.

## 1. Ask for the platform and stackup (no defaults allowed)

Ask the user with the question tool; do not assume:

| Question | Options |
|---|---|
| Which platform will fabricate the PCB? | JLCPCB (recommended, same as the P4 mainboard) / Huaqiu / other |
| Layer count, board thickness, inner/outer copper weight? | Default 4 layers, 1.6 mm, outer 1 oz, inner 0.5 oz |
| Stackup model? | JLCPCB default `JLC04161H-3313`; for other platforms use that fab's standard stackup |

Write the result into this board's design document. **Changing platform means recalculating all impedance traces**; do not reuse JLCPCB values.

## 2. Set the target impedance and tolerance

| Interface | Target | Tolerance source |
|---|---|---|
| RF | 50 Ω single-ended (coplanar waveguide) | Module / chip manual |
| USB 2.0 | 90 Ω differential | USB spec, chip manual |
| HDMI TMDS, MIPI D-PHY / CSI, LVDS, Ethernet | 100 Ω differential | Interface spec, chip vendor Layout guide (example: the differential routing chapter of the chip vendor's Layout guide) |

## 3. Look up the table, and verify the check point on the calculator

1. In `assets/impedance_table.json`, look up by platform -> stackup -> (impedance, mode, layer);
2. **Whether or not an entry is found, first recompute the stackup's `check_point` on the calculator** (JLCPCB 3313: differential, outer layer, reference L2, 100 Ω, spacing 10.48 mil, should give 5.1 mil);
   - Same result -> the table values are usable;
   - Different result -> the fab has updated the stackup parameters: recalculate all impedance traces on this board, and ask the user to revise SPEC-003 6.5.3 and this table;
3. For combinations not in the table, calculate in step 4.

China-site calculator: <https://tools.jlc.com/jlcTools/index.html#/impedanceCalculatenew>. The first recommended stackup on the international site <https://jlcpcb.com/pcb-impedance-calculator> is `JLC04161H-3313A` (two prepreg sheets); **you must manually switch to `JLC04161H-3313`**, otherwise the trace width is off by about a factor of two.

## 4. When it is not in the table: how to calculate and adjust

First fix one quantity from the routing conditions, then let the calculator solve the other:

- Differential: spacing is usually set by the pin pitch and channel width (for example a 0.5 mm pitch connector, a 0.4 mm pitch QFN) -> enter the spacing, solve the width; if the width has a minimum (process, loss), do it the other way round;
- RF coplanar waveguide: fix the width (close to the pad width), solve the gap to the copper pour.

Adjustment tendencies (other conditions unchanged):

| Change | Impedance |
|---|---|
| Wider trace | Decreases (the most commonly used knob) |
| Larger differential spacing | Differential impedance increases; to return to the same impedance the width must also increase |
| Thicker dielectric (changing stackup) | Increases; `3313A` is thicker than `3313`, and the width for the same impedance is about twice as wide |
| Higher dielectric constant | Decreases |
| Covered by solder mask | Decreases by about 2-4 Ω (estimate); calculate outer layers as "with solder mask" |
| Thicker copper | Decreases slightly |
| Inner layer (stripline) | Clearly lower than outer layer at the same width; must be calculated separately |
| Coplanar pour close by | Decreases |

Write the computed values, calculator screenshots and date into the design document; if the platform or stackup is new, have the user review it and then add it to SPEC-003 6.5.3 and this table.

## 5. Apply it in KiCad

- Net classes: `track_width`, `diff_pair_width`, `diff_pair_gap` must match the design document (MCP saving the project resets `.kicad_pro` to defaults, so rewrite the net classes after saving, see 07 item 7);
- Change `W` and `S` in the differential-trace generating script accordingly, and look at DRC after regenerating: wider spacing often causes clearance violations in fan-out areas and length-matching meanders;
- QFN / BGA breakout areas may narrow the intra-pair spacing: a single segment ≤ 1.5 mm, center distance ≥ 0.30 mm (SPEC-003 6.5.6);
- Stay on the same layer throughout with an intact GND reference, do not cross splits, and add a return via at every layer change;
- Stackup and notes: `scripts/stackup.py <board> --stackup <model> --names TopLayer,GND,PWR,BottomLayer --note "<impedance entry>" --note-at=X,Y`.
  It writes the KiCad stackup (layer thicknesses and Dk, taken from `stack` in `assets/impedance_table.json`), the copper layer display names (use `GND2` when L3 serves as a full ground),
  and writes "[PCB impedance and stackup notes]" on the `Cmts.User` layer outside the board outline (not in the Gerber). The stackup only affects calculations inside KiCad and the Gerber job file;
  the fab produces according to the stackup chosen on the order; after renaming layers the Gerber file names change with them (for example `-TopLayer.gtl`), and the fab identifies files by extension.

## 6. Check

```bash
"<KiCad>/bin/python.exe" scripts/impedance_check.py <board.kicad_pcb> --width 0.1295 --gap 0.2662 [--nets TMDS_ CSI_]
        [--layers F.Cu B.Cu] [--exempt X0 Y0 X1 Y1 ...]
```

- `--layers`: check only segments on these layers (standard layer names). The impedance trace width of the same pair differs between outer-layer microstrip and inner-layer stripline, so check once per layer group (for a multilayer board, write the checks config as several parameter groups);
- `--exempt`: segments lying entirely inside the box are not checked (thin traces in BGA and connector fan-out areas kept as in the reference board); the box is relative to the drill origin, and must be listed in the design document.

| Result | Meaning |
|---|---|
| Width mismatch | The net class or generating script was not changed; or Freerouting rounding (a difference within 0.1 µm does not count) |
| Center distance < narrowing lower limit, narrowed section too long | FAIL, go back to step 5 |
| "split" section | At pads, connectors and flow-through ESD parts the traces follow the pad spacing; only the length is reported; if the length is clearly abnormal, check whether a paired trace was left unrouted |

It passes only when the last line reads "Result: no FAIL items".

For geometry beyond width and spacing, use `scripts/hs_review.py` to measure item by item (corners, ground vias beside layer-change vias, copper pour clearance, distance to crystals / switching nodes / buck components, GND pour between pairs, reference plane). Usage example (reference designators, nets and coordinates are fictitious):

```bash
"<KiCad>/bin/python.exe" scripts/hs_review.py <board> --hs TMDS_ CSI_D CSI_CLK \
  --zone-clear TMDS_=0.432 CSI_=0.508 --keep-from XTAL_P,XTAL_N=0.65 "Net-(U1-LX),Net-(U2-LX)=0.65" \
  --far U1,U2=7 --gnd-between CSI_ --exempt 0 0 50 30
```

Experience: a small bulge for intra-pair length matching can reach into the narrow GND strip between adjacent pairs, or push the trace close to the buck circuit; a U-shaped meander by itself does not create intra-pair skew, the skew comes from the remaining corners, so place bulges on segments facing no other pair.

## 7. Ordering

Tick impedance control, with the stackup the same as in step 1; in the remarks write something like "L1/L4 differential 100 Ω, width 5.1 mil, spacing 10.48 mil"; attach the calculator screenshot to the design document.

## Why it is done this way

- Formula estimates are unreliable: a width and spacing taken from a formula can give an actual impedance outside the interface's lower limit; the JLCPCB calculator is the reference;
- The stackup model name stays the same while its parameters change (the Dk of 3313 was once changed from 4.05 to 4.1), so every project must verify the check point;
- DRC checks only the minimum width and spacing; a width that is too wide or a spacing that is too large is not reported, so the geometry must be checked separately.
