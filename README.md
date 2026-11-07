English | [简体中文](./README.zh-CN.md)

# hubo-backpack-skill

An AI agent skill that designs **Hubo-Tack 30-pin backpack boards** in KiCad by following a written SOP,
and checks every step with scripts. Clone this one repository and you can start drawing a backpack.

| Repository | What it is | License |
|---|---|---|
| **hubo-backpack-skill** (this one) | The entry point: two agent skills, check scripts, and a copy of the blank template | MIT |
| [hubo-backpack-template](https://github.com/hubosmart/hubo-backpack-template) | The canonical blank KiCad template (copied into `templates/backpack/` here) | CERN-OHL-P v2 |
| [hubo-backpack-spec](https://github.com/hubosmart/hubo-backpack-spec) | The interface spec: pinout (30-pin interface figure on its front page), mechanics, power, ID card | CC BY 4.0 |

## Quick start

1. Install [KiCad 10](https://www.kicad.org/), Python 3.10+, and the
   [KiCAD-MCP-Server](https://github.com/mixelpixx/KiCAD-MCP-Server) for your agent.
   For autorouting, also install [Freerouting](https://github.com/freerouting/freerouting) and a matching Java runtime.
2. Copy the two folders under `skills/` into your agent's skill directory
   (for Claude Code: `~/.claude/skills/`).
3. Check the environment: `"<KiCad>/bin/python.exe" skills/hubo-kicad/scripts/doctor.py`
4. Tell your agent, for example:
   *"Start a new backpack from the hubo-backpack template and add a temperature/humidity sensor and a button."*

The skills are `hubo-backpack` (what a backpack must satisfy) and `hubo-kicad` (how to do the work in KiCad).
The skill text (`SKILL.md` and `references/`) is written in English; the skills work equally well in Chinese conversations.

## Workflow

1. A human writes the requirement.
2. The agent reads the skill and edits the schematic, starting from `templates/backpack/`.
3. Netlist vs. wiring table check, ERC 0.
4. A human does the placement and the critical traces.
5. Freerouting routes the rest; only new tracks are merged back; routing checks run.
6. The production gate runs all checks and exports Gerber / BOM / placement files.
7. DFM review, peer review and final sign-off by humans.
8. Order.

## Checks

| Script | What it checks |
|---|---|
| `skills/hubo-backpack/scripts/backpack_check.py` | Outline, mounting holes, header position and pad nets, ID-card address and write protection (KiCad `.kicad_pcb` or EasyEDA Pro `.epro2`) |
| `skills/hubo-backpack/scripts/height_check.py` | Component height from 3D models (top ≤ 3.9 mm, bottom ≤ 1.7 mm except the header) |
| `skills/hubo-backpack/scripts/idcard_image.py` | Builds and self-checks the 0x57 ID-card descriptor image |
| `skills/hubo-kicad/scripts/route_check.py`, `miter.py`, `plane_check.py`, `hs_review.py`, `impedance_check.py` | Routing rules, right angles, plane splits, high-speed signal review, impedance |
| `skills/hubo-kicad/scripts/prod_gate.py`, `export_prod.py` | Production gate and manufacturing export |

Script output cites spec clauses by number: `SPEC-003` = [30-pin backpack spec](https://github.com/hubosmart/hubo-backpack-spec/blob/main/docs/en/30pin-backpack-spec.md), `SPEC-004` = [ID card spec](https://github.com/hubosmart/hubo-backpack-spec/blob/main/docs/en/id-card-spec.md), `SPEC-005` = [PCB layout rules](https://github.com/hubosmart/hubo-backpack-spec/blob/main/docs/en/pcb-layout-spec.md).

DIY backpacks **must** use the programmed ID card at I²C 0x57 with a `product_id` from `0xF000`–`0xFFFE`.
See the [ID card spec](https://github.com/hubosmart/hubo-backpack-spec).

## EasyEDA

KiCad is the primary tool. If you use EasyEDA (嘉立创 EDA), install the official
[easyeda-api-skill](https://github.com/easyeda/easyeda-api-skill); `backpack_check.py` can also read an
EasyEDA Pro `.epro2` file offline. Community MCP servers such as
[hyl64/jlcmcp](https://github.com/hyl64/jlcmcp) and
[oaslananka/easyeda-mcp-pro](https://github.com/oaslananka/easyeda-mcp-pro) may also work; we have not tested them.

## The last check must be a human

Passing every script does not mean a board is ready to order. These tools give no warranty of quality,
safety or regulatory compliance.

## License

MIT, see [LICENSE](LICENSE). `templates/backpack/` is CERN-OHL-P v2. Third-party notices: [NOTICE.md](NOTICE.md).
