# 05 Inner-plane split and stitching vias (design rules)

| Item | Rule | Basis / notes |
|---|---|---|
| Plane split gap width | **10-20 mil** (0.254-0.508 mm); take the larger of the "voltage difference requirement" and the "fab process minimum" | IPC-2221B Table 6-1, inner-layer conductors (B1): 0-30 V only requires 0.05 mm, so on low-voltage boards the process decides; JLCPCB's minimum spacing on multilayer boards is 3.5 mil, and about 10 mil isolates well; wider gains nothing for isolation and only makes the planes smaller |
| Signal vias | **Must not sit on a split gap** (if the anti-pad connects to the gap, it counts as sitting on it) | When a signal changes layers its return current has to move to the adjacent plane; a via on the gap cuts the return path |
| Signal traces | Do not cross a split: the projection of an outer-layer trace must not fall on an inner-layer split gap | Before routing, add a keepout on the outer layer above the inner-layer split gap (04, step 2), and check after routing |
| **GND stitching vias** | **Review each one individually**: (1) prefer placing it in the GND area next to the split; (2) if it sits on or touches the split, confirm it does not cut a narrow neck into the current path of the power plane (acceptable only on an unloaded branch); (3) there must be GND vias close to both sides of the split and near signal layer changes, to carry the return current | The anti-pad of a stitching via merges with the split gap and locally widens it; a row of vias along a split is the most likely way to create narrow necks in a plane |
| Mechanical holes | If a hole with a fixed position (screw hole etc.) sits on a split, reroute the plane boundary around it; do not move the hole | Mechanical positions are usually frozen |
| Hole to inner-layer copper | Via barrel to other-net inner-layer copper >= 0.2 mm; through-hole barrel >= 0.3 mm | JLCPCB process capability |
| Current capacity | Split width has **no direct relation** to current capacity; current capacity depends on the narrowest point of the plane along the current path x copper thickness (JLCPCB inner layers default to 0.5 oz) x allowed temperature rise | Widening the split only makes the planes smaller and the necks narrower |

## Automatic check

```bash
"<KiCad>/bin/python.exe" scripts/plane_check.py <board.kicad_pcb> --layer In2.Cu [--gap-min-mil 10 --gap-max-mil 20]
        [--bga-box X0 Y0 X1 Y1 ... --hs CSI_ HDMI_ ...]
```

The output has four sections: plane piece count and area per net -> split gap width between planes of different nets -> holes sitting on a split (signal holes FAIL; GND holes WARN, confirm each one; holes of the net of this layer's own plane INFO) -> narrow necks (power planes are shrunk by 1/2/3 mm; if the plane breaks apart and the detached part has a via/pad of that net, it is WARN; an unloaded branch is INFO). The exit code is 1 when there is any FAIL.

Definitions (revised for 8-layer boards):
- Plane split gap = free space that is within gap-max of two different net planes at the same time. Free space that touches only one plane (space around a local power island on a signal layer, notches at fixed holes on the board edge) does not count, so the script can also check a partially poured layer (for example a layer such as L6 with a few power islands);
- Split width is measured only between the two adjacent planes: if a third plane is nearest, it does not count. Exceeding the upper limit only counts for gaps that are >= 3 mm long as a continuous run; point-like adjacency (via clearances where several patches meet under a BGA) is recorded as INFO; below the lower limit is always FAIL;
- When the hole of one plane contains a plane of another net (a 3V3 island inside a large GND area), the hole is treated as a split gap and is not filled in as an anti-pad;
- `--bga-box` (relative to the drill origin, may be repeated): in a BGA fan-out area the via pitch (0.5-0.65 mm) is smaller than the roughly 1.2 mm needed for "two holes belonging to two different planes, each enclosed by its own plane", so the split inevitably crosses holes. For non-`--hs` nets inside the box, a hole on the split and an over-wide gap are recorded as WARN, to be confirmed one by one; high-speed nets are still FAIL. If you use it, state the boxes and the reasons in the design document.

**Notes**:
- Fill the copper pours first (`kicad-cli pcb drc --refill-zones --save-board`); the script reads the filled result;
- KiCad saves copper pours as "fractured" polygons (holes are merged into the outer outline through cut lines). Any statistics you write yourself must `Unfracture` first, otherwise you get a false "0 holes" and wrongly conclude the plane is intact;
- A 2-layer board has no inner plane, so this section does not apply; use a large ground pour on the bottom layer instead, and signal traces must not cut the bottom ground into islands.
