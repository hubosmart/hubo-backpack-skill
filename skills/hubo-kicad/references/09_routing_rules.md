# 09 Routing rules (basic rules + high-speed signals)

The single place where the rules and values are maintained: `SPEC-005` "PCB Layout General Specification" (public version: [hubo-backpack-spec/docs/zh-CN/pcb-layout-spec.md](https://github.com/hubosmart/hubo-backpack-spec/blob/main/docs/zh-CN/pcb-layout-spec.md); clause numbers match the script output). This file only covers how to apply and check the rules in KiCad, and does not repeat the values.

## 0. Basic rules (all nets, SPEC-005 chapter 10)

All requirements are finally enforced by `prod_gate.py` at export (see 06); what follows is what to prepare before routing:

1. **Write the project config**: copy `assets/hz_layout.example.json` to `<board name>.hz_layout.json` next to the board file, with contents taken from the design document:
   - `power`: for each power net, the current the traces **actually carry** and the layer they are on (`outer`/`inner`); `basis` states where the current comes from (design document clause, component datasheet). `power_rules.py` computes the width per IPC-2221 with a 10 ℃ temperature rise (the same formula as KiCad's Calculator Tools > Track Width). For a net whose trunk is a copper pour and whose traces are only branches, enter the branch current, and check the trunk plane for narrow necks separately with `plane_check.py`; do not put large currents on thin inner-layer traces (inner layers are 0.5 oz, about 5 times wider for the same current). The example values must not be used as-is (a config containing the marker "[EXAMPLE VALUE - REPLACE]" is rejected);
   - `checks`: the parameters of the `hs_review` / `impedance_check` / `plane_check` / `fence_check` runs this board needs (see section 3, 08, 05); leave it out if there are none;
2. **Generate the DRC rules**: `python scripts/power_rules.py <board>`. It inserts `assets/jlc_base.kicad_dru` (trace width and spacing, pad to trace, vias, through-holes, board edge, silkscreen) at the front of the project `.kicad_dru`, and appends a minimum-width rule for each power net according to `power`. Rerun it after changing the config;
3. **Local spots below the recommended values** (such as breakout of a 0.4 mm pitch QFN): after the template in the project `.kicad_dru`, add exception rules limited to a net or area, and list the locations and reasons in the design document; do not change the template. The via hole must not go below the template's 0.3 mm (SPEC-005 10.4): a hole < 0.3 mm with an outer diameter ≤ 0.4 mm incurs a JLCPCB surcharge, so do not shrink vias when working around obstacles;
4. **Check after routing** (geometry that DRC cannot cover):

```bash
python scripts/route_check.py <board> [--vip U1 U2]
```

`--vip`: reference designators of fine-pitch BGAs ordered with the JLCPCB via-in-pad process; same-net vias on their pads are allowed (listed on a separate line). State the via-in-pad process and these designators in the design document.
To make the production gate use this parameter too, write `"route_check": ["--vip", "U1"]` in `checks` of `<board name>.hz_layout.json` (if absent, the gate runs with default parameters).

| Item | Meaning | Action |
|---|---|---|
| 1 Acute / right angle | The corner between two joined segments is > 45°; junctions inside same-net pad/via copper are not counted | First run `miter.py <board>` to convert automatically to 45° bends (verified with DRC at each spot, reverted on conflict); for the "doubled back" cases it lists, first run `dedupe.py` or delete the short segments by hand |
| 1 Acute-angle wedge | The angle where three traces meet is < 90° | Change to a T junction or connect separately by hand |
| 3 Pad overlap | Pad edge to edge < 0.1 mm between different components (including the same net; DRC does not check same-net pads) | Move the components apart or change the footprint (SPEC-005 10.12) |
| 2 Via-in-pad | A via inside an SMD pad | Always move signal vias out of the pad; same-net ground vias on the chip's bottom thermal pad with hole diameter ≤ 0.3 mm are passed automatically as the 10.7 exception (listed on a separate line); otherwise, if truly needed, choose the via-in-pad process when ordering and record it in the design document |

An exit code of 1 means there is a line marked "NOT MET". `miter.py` does not save the copper pours after changing; refill the pours per the project procedure, then rerun DRC and `route_check.py`.

Thermal-pad ground vias are best built into the footprint (the KiCad standard library `_ThermalVias` footprints, or PTH pads in a custom footprint), with the stencil opening avoiding the via locations;
vias drilled individually on the board are also recognized by route_check as an exception, but they are lost when the footprint is swapped, so this is not recommended. Power trace width is reported by DRC (rule name `HZ_PWR_<net>`).

## 1. High-speed signals: set signal classes and chip appendices before placement

1. List the classes involved on this board per SPEC-005 1.2 (L, U1, U2, S, M, D, C, R, H) and write them into the design document. A board with only class L (such as an LED board) only needs section 0;
2. List the main controller and the chips with high-speed interfaces on the board, and confirm they appear in the SPEC-005 appendix (currently ESP32-P4, ESP32-S3, ESP32-C3 and some HDMI bridge chips). **If not, stop**: ask the user to first read that chip's hardware design guide and add the appendix, then begin routing;
3. Place parts according to the chip appendix and chapter 7: the distance from the crystal to the chip clock pin, series components near the chip, ESD near the connector, Flash/PSRAM right next to the main controller.

## 2. High-speed signals: while routing

- Impedance trace width and spacing per `08_impedance_control.md`;
- Route crystal clock traces and RF traces by hand, not with Freerouting: they must not have vias, and Freerouting would change layers;
- Use KiCad differential routing (key `6`) for differential pairs, and place the two vias of a pair together; place a return via at the layer change as well;
- For length matching use KiCad length tuning (keys `7` / `8`) and differential skew tuning (key `9`); add meanders only to the shorter trace, close to where the mismatch arises.

## 3. High-speed signals: checks

Fill the copper pours first, then run with parameters chosen by this board's classes (KiCad's bundled python):

```bash
python scripts/hs_review.py <board> --hs <high-speed net prefixes...> [--max-vias 2] [--skew <prefix>=<mil>] \
       [--no-via <crystal clock net> <RF net>] [--xtal <crystal designator>,<chip designator>=<mm>] [--via-gnd 1.0]
```

| Class | Parameters (values from SPEC-005) |
|---|---|
| U2 USB high speed | `--hs USB_D --max-vias 2 --skew USB_D=5` |
| M MIPI | `--hs CSI_D CSI_CLK --skew CSI_=10`; for inter-pair guard ground use `--gnd-between CSI_`; for Gbps-class add `--stub CSI_=15 --pair-skew CSI_=30` (SPEC-005 2.8, 5.2) |
| C Crystal (ESP32-P4) | `--no-via XTAL_P XTAL_N --xtal Y1,U1=4.5` (2.0 for S3) |
| R RF | `--no-via <RF net>` |

A line in the output containing "NOT MET" means it does not pass; paste the result into the design document. The meaning of each item is described in the script header.

Notes:

- The `--hs` prefix must be precise to the high-speed traces: `CSI_` would also pull in I2C nets such as `CSI_SCL_1V8`, so write `CSI_D CSI_CLK`;
- Differential pairs are paired by `_P` / `_N` or `+` / `-`; other naming (such as `USB_DP` / `USB_DN`) does not enter items 7 and 8 - name them with one of these two suffixes in the schematic;
- The `--xtal` distance is valid only for that chip's appendix: the 4.5 mm of the P4 does not apply to other chips;
- Item 8 is the difference in total trace length; length inside pads is not counted; item 13 (inter-pair) counts the layer-change via barrel as one board thickness per via, so a pair whose length is compensated by layer-change vias is not misjudged;
- Item 12 stubs: for a trace that connects to only one outer layer through a through-hole via, the full board thickness counts (about 63 mil on a 4-layer board); if both outer layers connect it is 0. For Gbps-class, it is best for the whole pair not to change layers, and to use vias only when a layer change is necessary; if the chip vendor gives the length difference inside the chip package, deduct it separately;
- Component placement order and SPI topology are checked by a person in the KiCad GUI; the script does not judge them;
- Connector pin assignment (GND - differential pair - GND, SPEC-005 2.10), whether the trunk of a multi-load power net uses a copper pour (SPEC-005 10.11), and cutouts under pads are likewise checked by a person; do not use thicker traces as a substitute for a copper pour.
- For chips with an evaluation board: the evaluation board is only a reference; take values from the chip vendor's spec and the body of SPEC-005, and do not copy the evaluation board files.

### 3.1 Guard-ground vias (SPEC-005 2.9)

The guard copper on both sides of and between a differential pair must be tied to the reference ground with vias along its whole length; grounding only at the two ends is forbidden (a long strip of copper grounded only at the ends is a resonant cavity and increases crosstalk). Fill the copper pours first:

```bash
python scripts/fence_check.py <board> --net <prefix>=<target mm>[/<limit mm>] [...] [--exempt X0 Y0 X1 Y1 ...] [--radius 2.0] [-v]
```

| Class | Target (λ/20, λ at 2.5 times the bit rate) | Limit (λ/10, when space is constrained) | Example parameters |
|---|---|---|---|
| M MIPI (≤ 1 Gbps/lane) | ≤ 3 mm | ≤ 6 mm | `--net CSI_D=3 CSI_CLK=3` |
| D HDMI TMDS (≤ 1080p) | ≤ 4 mm | ≤ 8 mm | `--net TMDS_=4` |
| U2 USB 2.0 HS | ≤ 6 mm | ≤ 12 mm | `--net USB_D=6` |
| Higher rates | Recompute with the SPEC-005 2.9 formula and write it into the design document | | |

| Parameter | Meaning |
|---|---|
| `--net prefix=target[/limit]` | Net prefix and ground via spacing along the line; if the limit is omitted it defaults to 2 times the target (λ/10); paired by `_P` / `_N` or `+` / `-` |
| `--exempt` | Exemption boxes (relative to the drill origin): chip fan-out areas, connector and ESD pad areas; the same set of boxes as `hs_review`, listed in the design document |
| `--radius` | Maximum lateral distance from a ground via to the trace centerline, default 2.0 mm |
| `--probe` | How far beyond the trace edge same-layer GND fill counts as "has guard ground", default 0.8 mm (larger than the pour clearance 0.432 / 0.508) |

Judgment: samples are taken along each trace, looking only at the outer side (the side facing away from the other trace of the pair). Within a continuous run where there is same-layer GND fill on the outer side, a ground via (GND via, GND through-hole pad) must be on the outer side, within radius laterally, and on that same copper island; for the largest along-line gap of segment start -> via -> via -> segment end: exceeding the target but not the limit is recorded as "relaxed" (only listed, and the design document gives the reason segment by segment), and exceeding the limit is "NOT MET" with an "exceeded" label, flagged "pour island without ground via" or "grounded at both ends only". The length with no guard ground on the outer side is only counted and not judged for spacing (corresponding to 2.9 "use >= 3W instead"; 3W is handled by `hs_review` and the project corridor check). One line per pair; an exit code of 1 means there is a "NOT MET".

Fix: add GND vias on the guard copper of out-of-limit segments; for "relaxed" segments add vias where possible, and where vias truly do not fit (a large piece of ground copper with vias at both ends) record it in the design document - do not remove the finding by adding exemption boxes; if the guard copper is too narrow for vias, delete that copper and leave 3W clearance instead. Differential traces on both L1 and L4 are checked; guard ground refers to same-layer pours.

## Why it is done this way

- **Basic rules are checked in two places**: what DRC can express (width, spacing, holes, board edge, per-net power trace width) goes into `.kicad_dru`, travels with the project and is reported live in the GUI; geometry DRC cannot express (corners, via-in-pad) is covered by `route_check.py`;
- **Requirements are written as data and enforced at the exit**: the current, width and check parameters from the design document go into `hz_layout.json`, and `prod_gate.py` verifies them item by item at export - a process that relies on the agent's discipline gets skipped, while blocking at the exit is reliable;
- **Power trace width is computed by a script from the formula**: no rule-of-thumb values (at the same 20 mil, a 0.5 A board and a 2 A board have completely different requirements), and no reliance on a GUI calculator - some versions of EasyEDA have no circuit calculator, while a script calculation is reproducible and reviewable;
- **Organized by signal class rather than by chip**: the same USB high-speed port has the same routing rules on different chips; chip differences are concentrated in the crystal, power and pins, and go in the appendix;
- **Add a new chip's appendix before routing**: the chip vendor's requirements may be stricter than the general rules (P4 crystal 4.5 mm, S3 only 2.0 mm), and rework after routing by the general rules is costly;
- **Limits are script parameters, not code**: the values are revised as SPEC-005 is reviewed, and the script only measures and compares;
- **Ground vias are judged by copper island membership**: merely checking "is there a ground via nearby" would count vias across another trace and connected to a different piece of copper, and could not catch a long guard strip grounded only at its ends.
