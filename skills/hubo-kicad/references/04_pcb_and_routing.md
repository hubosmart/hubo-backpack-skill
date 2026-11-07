# 04 PCB and routing

**PCB tools** act on "the currently loaded board": run `open_project` first, and `save_project` after changes.

## Common tools

| Operation | Tools |
|---|---|
| Add | `place_component`, `add_via`, `route_pad_to_pad` |
| Delete | `delete_component`, `delete_trace(traceUuid)`, `delete_graphic(uuid)` |
| Modify | `move_component`, `edit_component`, `set_footprint_type` (SMD/through-hole, exclude from BOM/position file), `move_footprint_text`, `update_graphic`, `add_footprint_3d_model` |
| Query | `get_component_pads`, `query_traces`, `list_graphics`, `get_nets_list`, `get_board_2d_view` |

## State synchronization (the most common cause of data loss)

- After a script or `kicad-cli --save-board` has modified the board file, you **must run `reload_board`**, otherwise MCP overwrites it with the stale in-memory board when it saves;
- `save_board` may also falsely report "file modified externally" after MCP itself wrote to disk; treat the file on disk as authoritative;
- **Do not modify the file while the user has this board open in the KiCad UI**: saving in the UI overwrites the script's changes. Confirm it is closed before changing, or ask the user to reload in the UI afterwards;
- Refill copper pours with `kicad-cli pcb drc --refill-zones --save-board`, not MCP `refill_zones` (it goes through SWIG and risks a segfault).

## Auto-routing: Freerouting in seven steps

**MCP's routing tools do not avoid obstacles**; routing one by one is a lot of work and prone to violations.

1. **Place power fan-outs and special traces first** (power vias, traces to test points, traces that can only take one path), otherwise signal traces will squeeze them out of position.
2. **Make a routing copy**: `scripts/route_prep.py copy <board> <copy> --keepout <module keepout name> --gap In2.Cu:B.Cu --edge-band 1.5`
   This locks all existing copper; it adds routing keepouts directly under the module, on the outer layer above inner-plane split gaps (to prevent crossing a split; only gaps between planes of different nets count, slots left by inner-layer signal traces do not), and in a ring band along the board edge (reserved for stitching vias).
3. `reload_board(copy)` → `export_dsn` (**do not** pass `boardPath`; it exports the stale in-memory board).
4. **DSN post-processing**: `scripts/route_prep.py strip <in.dsn> <out.dsn> --nets GND,+5V,3V3,<frozen RF nets> [--plane-layers In1.Cu,In2.Cu]`
   - After copper layers have been renamed (`stackup.py`), the DSN uses the display names (such as GND, PWR); `--plane-layers` accepts both standard and display names and errors out on a name that matches no layer;
   - Inner layers are exported as `signal`, so Freerouting would route across whole ground/split power layers → change to `(type power)`;
   - Locked copper is still `(type route)` and would be torn up and rerouted (RF traces included) → change to `protect`;
   - Clear the pin lists of the copper-pour nets, otherwise they get connected once more as signal nets;
   - **Rule areas that only forbid traces are not exported**, so a keepout must forbid vias as well (`route_prep copy` already does this).
5. Run Freerouting (see 01).
6. **`import_ses` wipes all existing copper on the board** (Freerouting does not write protect traces into the SES). Import only into a **trial copy**, then:
   `route_prep.py dump <production board> orig.json`, `route_prep.py dump <trial copy> routed.json`, `route_prep.py merge <production board> orig.json routed.json`.
7. After merging, run DRC, parity, freeze comparison and the plane-split crossing check (`plane_check.py` from 05), and render the board to see whether the routing is reasonable (hugging the board edge, detouring far). Route the few unroutable traces by hand, generating the coordinates of the added traces from data as well.

**Cleanup and fill-in routing after merging (scripts are in `scripts/`; coordinate parameters are relative to the drill origin):**

| Step | Script | Notes |
|---|---|---|
| Deduplicate | `dedupe.py <board>` | Between two rows of same-net pads of a flow-through ESD part, Freerouting adds a segment overlapping the pre-routed one, with coordinates off by 100 nm and width 0.1 µm greater, which inflates the length statistics (07 item 26) |
| Fill-in routing | `mini_route.py <board> <net> <ref.pin> --hs … --corridor 0.45 --exempt … --avoid … --block-area …` | Grid A*; avoids other nets' copper (uses the local clearance when a pad/footprint has a larger one), trace-forbidden/via-forbidden rule areas, rule areas named by `--block-area` (such as a module body area carrying only custom DRC rules), the 3W corridor of high-speed traces, and bottom-layer no-route boxes (such as under a QFN thermal pad). A failed search usually means the pin is walled in: first remove the wall segments, or draw a short stub by hand and search again |
| Island ground vias | `island_via.py <board> --hs … --corridor 0.45 --exempt …` | Adds vias when an outer-layer GND island contains GND pads but no ground via; a via never lands inside any pad (same-net pads included); when there is no room it only reports, and you must change the routing (remove the power segments enclosing the island and route around from outside) |
| Stitching vias | MCP `add_gnd_stitching_vias` | Run DRC after placing: it does not check the hole spacing to existing GND vias, so overlaps (`hole_to_hole`) occasionally occur; just delete them |

## MCP gaps and substitutes

| Need | MCP status | Substitute |
|---|---|---|
| Design rules, net classes | `set_design_rules` does not change the Default net class; `create_netclass` does not write net assignments; `save_project` overwrites the board-edge clearance back to 0.5 mm | Write rules into `.kicad_dru` (MCP does not touch it); edit net classes directly in `.kicad_pro` |
| Pad size, fields (such as LCSC) and 3D models of footprints on the board | `edit_footprint_pad` changes only the library file; no tool to set fields; `add_component_3d_model` goes through IPC only | pcbnew script; update the library file through MCP in sync |
| Delete rule areas, change copper pour outlines | No tool | pcbnew script |
| Board-edge stitching vias | `add_gnd_stitching_vias` has no "along the board edge" strategy | Script that walks the board outline and finds valid positions at the given spacing |
| Bulk trace deletion | `delete_trace` handles one segment at a time | Loop and delete according to DRC results |
| Layer names, stackup, coordinate origin | None | `scripts/stackup.py` (layer names + stackup + on-board impedance notes); `scripts/set_origin.py` |

**MCP first, and record the gaps**: every time you switch to a script, state why MCP could not do it, so it can be fed back upstream.

## pcbnew scripting notes

- One process loads only one board; after `Remove`, a second `LoadBoard`, or loading the board after loading library footprints, objects may become untyped `SwigPyObject` → finish all reading and changing first and delete everything at the end; read libraries and boards in separate processes;
- `Models()` returns a copy, so to change 3D models clear them and `Add3DModel` again; `GetSize()` returns an internal reference, so take the values out before calling `SetSize`;
- A field created with `SetField` is visible by default and on the silkscreen layer → make it hidden and move it to the Fab layer;
- After copper layers have been renamed (such as `TopLayer`), `GetLayerName` returns the display name, so always compare layers in scripts with `BOARD.GetStandardLayerName`;
- The rotation direction of KiCad 3D models about Z is opposite to mathematical counter-clockwise; after attaching a model, render it and check the pin-1 direction.
