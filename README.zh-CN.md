[English](./README.md) | 简体中文

# hubo-backpack-skill

让 AI 按书面 SOP 在 KiCad 里画 **Hubo-Tack 30-Pin 背包板**、每一步都用脚本检查的 AI 技能。
克隆这一个仓库就可以开始画背包。

| 仓库 | 是什么 | 许可证 |
|---|---|---|
| **hubo-backpack-skill**（本仓库） | 入口：两个 AI 技能、检查脚本、空白模板副本 | MIT |
| [hubo-backpack-template](https://github.com/hubosmart/hubo-backpack-template) | 空白 KiCad 模板正本（本仓库 `templates/backpack/` 是它的副本） | CERN-OHL-P v2 |
| [hubo-backpack-spec](https://github.com/hubosmart/hubo-backpack-spec) | 接口规范：针位（首页有 30-Pin 接口一览图）、机械、供电、身份卡 | CC BY 4.0 |

## 快速开始

1. 安装 [KiCad 10](https://www.kicad.org/)、Python 3.10+，并给你的 AI 装上
   [KiCAD-MCP-Server](https://github.com/mixelpixx/KiCAD-MCP-Server)。要自动布线，再装
   [Freerouting](https://github.com/freerouting/freerouting) 和对应版本的 Java。
2. 把 `skills/` 下的两个目录复制到 AI 的技能目录（Claude Code：`~/.claude/skills/`）。
3. 自检环境：`"<KiCad>/bin/python.exe" skills/hubo-kicad/scripts/doctor.py`
4. 对 AI 说，例如：*“用 hubo-backpack 模板新建一块背包，加一个温湿度传感器和一个按钮。”*

两个技能：`hubo-backpack`（背包必须满足什么）和 `hubo-kicad`（在 KiCad 里怎么做）。
技能正文（`SKILL.md` 与 `references/`）为英文；AI 在中文对话里同样可用。

## 流程

1. 人提需求。
2. AI 读技能，从 `templates/backpack/` 起步改原理图。
3. 网表与接线表核对，ERC 0。
4. 人做布局和关键走线。
5. Freerouting 布其余的线，只合并新增走线，跑走线检查。
6. 出厂闸门跑全部检查并导出 Gerber、BOM、坐标文件。
7. DFM 复核、同事校对、终审（都是人）。
8. 下单。

## 检查脚本

| 脚本 | 检查什么 |
|---|---|
| `skills/hubo-backpack/scripts/backpack_check.py` | 板框、安装孔、排针位置与焊盘网络、身份卡地址与写保护（支持 KiCad `.kicad_pcb` 与嘉立创 EDA 专业版 `.epro2`） |
| `skills/hubo-backpack/scripts/height_check.py` | 按 3D 模型核对元件高度（顶面 ≤ 3.9 mm，底面除排针外 ≤ 1.7 mm） |
| `skills/hubo-backpack/scripts/idcard_image.py` | 生成并自检 0x57 身份卡描述符镜像 |
| `skills/hubo-kicad/scripts/route_check.py`、`miter.py`、`plane_check.py`、`hs_review.py`、`impedance_check.py` | 走线规则、直角、内电层分割、高速信号评审、阻抗 |
| `skills/hubo-kicad/scripts/prod_gate.py`、`export_prod.py` | 出厂闸门与生产文件导出 |

脚本输出按编号引用规范条款：`SPEC-003` = [30-Pin 背包规范](https://github.com/hubosmart/hubo-backpack-spec/blob/main/docs/zh-CN/30pin-backpack-spec.md)，`SPEC-004` = [身份卡规范](https://github.com/hubosmart/hubo-backpack-spec/blob/main/docs/zh-CN/id-card-spec.md)，`SPEC-005` = [PCB Layout 通用规范](https://github.com/hubosmart/hubo-backpack-spec/blob/main/docs/zh-CN/pcb-layout-spec.md)。

DIY 背包**必须**使用 I²C 0x57 烧录版身份卡，`product_id` 取 `0xF000`～`0xFFFE`。
见[身份卡规范](https://github.com/hubosmart/hubo-backpack-spec)。

## 嘉立创 EDA

以 KiCad 为主。用嘉立创 EDA 的话，安装官方 [easyeda-api-skill](https://github.com/easyeda/easyeda-api-skill)；
`backpack_check.py` 也能离线读嘉立创 EDA 专业版 `.epro2`。社区的 MCP（如
[hyl64/jlcmcp](https://github.com/hyl64/jlcmcp)、[oaslananka/easyeda-mcp-pro](https://github.com/oaslananka/easyeda-mcp-pro)）
可能也能用，我们没有验证过。

## 最后一关必须是人

脚本全部通过不代表板子可以直接下单。这些工具不对质量、安全或合规性做任何保证。

## 许可证

MIT，见 [LICENSE](LICENSE)。`templates/backpack/` 为 CERN-OHL-P v2。第三方说明见 [NOTICE.md](NOTICE.md)。
