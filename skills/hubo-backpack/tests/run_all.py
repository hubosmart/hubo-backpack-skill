"""hubo-backpack self-test: covers only the pure functions and .epro2 parsing, no pcbnew needed.

Usage: python tests/run_all.py    the last line should be ALL PASS
"""
import json
import pathlib
import sys
import tempfile
import traceback
import zipfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import backpack_check as bc  # noqa: E402

BASE = json.load(open(ROOT / "assets" / "p4_backpack_baseline.json", encoding="utf-8"))
PINS = BASE["id_card"]["pins"]


def good_nets():
    nets = {n: "" for n in range(1, 31)}
    for p in BASE["pins"]["5V"]:
        nets[p] = "+5V"
    for p in BASE["pins"]["3V3"]:
        nets[p] = "3V3"
    for p in BASE["pins"]["GND"]:
        nets[p] = "GND"
    for p in BASE["pins"]["NC_DEFAULT"]:
        nets[p] = f"unconnected-(H1-Pad{p})"
    return nets


def test_header_pad_positions():
    want = bc.expected_header_pads(BASE["header"])
    assert want[1] == (-31.63, -13.431) and want[2] == (-36.13, -13.431)
    assert want[29] == (-31.63, 14.569) and want[30] == (-36.13, 14.569)
    assert len(want) == 30


def test_eeprom_address():
    hi = {"3V3"}
    assert bc.eeprom_address({"1": "3V3", "2": "3V3", "3": "3V3"}, hi, "GND", PINS) == (0x57, "")
    assert bc.eeprom_address({"1": "3V3", "2": "GND", "3": "GND"}, hi, "GND", PINS) == (0x51, "")
    addr, why = bc.eeprom_address({"1": "", "2": "GND", "3": "GND"}, hi, "GND", PINS)
    assert addr is None and "A0" in why


def test_address_table_covers_all():
    # SPEC-004 V2.2: every address 0x50-0x57 maps to a model, 0x57 is the programmed card
    t = BASE["id_card"]["address_table"]
    assert sorted(int(k) for k in t) == list(range(0x50, 0x58))
    assert BASE["id_card"]["address_burn"] == 0x57 and "Programmed" in t["87"]
    assert "Reserved for official models" in t["80"] and "Reserved for official models" in t["86"]


def test_pin_nets_good():
    assert bc.check_pin_nets(good_nets(), BASE["pins"])[0][0] == "OK"


def test_pin_nets_faults():
    n = good_nets()
    n[3] = n[4] = "+5V"
    assert any(l == "FAIL" and "same net" in m for l, m in bc.check_pin_nets(n, BASE["pins"]))
    n = good_nets()
    n[30] = "SIG"
    assert any(l == "FAIL" and "GND" in m for l, m in bc.check_pin_nets(n, BASE["pins"]))
    n = good_nets()
    n[16] = "ADC_IN"
    assert any(l == "WARN" for l, m in bc.check_pin_nets(n, BASE["pins"]))


# ---------------- run_checks: synthetic models covering OK and each kind of FAIL ----------------

def good_model(address_pins=("3V3", "3V3", "GND"), wp="U7_WP"):
    """A synthetic board fully matching the baseline: 0x53 (A0, A1 on 3V3, A2 on ground)."""
    want = bc.expected_header_pads(BASE["header"])
    nets = good_nets()
    hdr = {"ref": "H1", "side": "bottom", "value": "HDR",
           "pads": [{"num": str(n), "x": x, "y": y, "net": nets[n]} for n, (x, y) in want.items()]}
    nl = list(address_pins) + ["GND", "U7_SDA", "U7_SCL", wp, "3V3"]
    u7 = {"ref": "U7", "side": "top", "value": "AT24C02C-SSHM-T",
          "pads": [{"num": str(i + 1), "x": 0, "y": 0, "net": n} for i, n in enumerate(nl)]}
    mh = BASE["mounting_holes"]
    return {
        "origin_desc": "test origin", "outline": {"width": 83.0, "height": 40.0, "radii": [2.0]}, "copper_layers": 4,
        "holes": [{"ref": f"S{i}", "x": x, "y": y, "drill": mh["drill"], "pad": mh["pad_min"], "net": mh["net"]}
                  for i, (x, y) in enumerate(mh["positions"])],
        "components": [hdr, u7], "header": {"candidates": ["H1"]},
        "silk": {"items": [{"ref": "H1", "height": 1.0, "thickness": 0.15}], "height_verified": True},
    }


def run(model, expect="0x53"):
    lines = []
    code = bc.run_checks(model, BASE, expect, out=lines.append)
    return code, lines


def fail_lines(lines):
    return [l for l in lines if l.startswith("  [FAIL]")]


def test_run_checks_ok():
    code, lines = run(good_model())
    assert code == 0 and not fail_lines(lines), lines
    assert lines[-1] == "Result: no FAIL in automatic checks"
    assert any("U7 address 0x53" in l and l.startswith("  [OK  ]") for l in lines)


def test_run_checks_wrong_address():
    code, lines = run(good_model(), expect="0x51")
    assert code == 1 and any("this board should be 0x51" in l for l in fail_lines(lines))
    code, lines = run(good_model(), expect=None)  # unspecified is only a WARN
    assert code == 0 and any(l.startswith("  [WARN]") and "--expect-address" in l for l in lines)


def test_run_checks_wp_grounded():
    code, lines = run(good_model(wp="GND"))
    assert code == 1 and any("WP is on GND" in l for l in fail_lines(lines))


def test_run_checks_5v_3v3_shorted():
    m = good_model()
    for p in m["components"][0]["pads"]:
        if p["num"] in ("1", "2"):
            p["net"] = "3V3"
    code, lines = run(m)
    assert code == 1 and any("same net" in l for l in fail_lines(lines))


def test_run_checks_header_on_top():
    m = good_model()
    m["components"][0]["side"] = "top"
    code, lines = run(m)
    assert code == 1 and any("H1 is on the top side" in l for l in fail_lines(lines))


def test_run_checks_hole_offset():
    m = good_model()
    m["holes"][0]["x"] += 0.2
    code, lines = run(m)
    assert code == 1 and any("no mounting hole at" in l for l in fail_lines(lines))
    m = good_model()
    m["holes"][1]["net"] = ""
    assert any("net none" in l for l in fail_lines(run(m)[1]))


def test_run_checks_header_pad_offset_and_missing():
    m = good_model()
    m["components"][0]["pads"][4]["x"] += 0.2
    assert any("pad positions off: [5]" in l for l in fail_lines(run(m)[1]))
    m = good_model()
    m["header"]["candidates"] = []
    code, lines = run(m)
    assert code == 1 and "cannot find a unique 30-pad header" in lines[-1]


def test_run_checks_outline_and_layers():
    m = good_model()
    m["outline"]["width"] = 84.0
    m["copper_layers"] = 6
    assert len(fail_lines(run(m)[1])) == 2
    m = good_model()
    m["outline"] = None
    assert any("no board outline found" in l for l in fail_lines(run(m)[1]))


def test_run_checks_silk():
    m = good_model()
    m["silk"]["items"][0]["thickness"] = 0.1
    assert any("NOT MET ['H1']" in l for l in fail_lines(run(m)[1]))
    # when the text height comes from an unmeasured field, a pass is only a WARN, never OK
    m = good_model()
    m["silk"]["height_verified"] = False
    code, lines = run(m)
    assert code == 0 and any(l.startswith("  [WARN]") and "not measured" in l for l in lines)


# ---------------- EasyEDA: coordinate conversion, outline, end to end ----------------

# Data of the bottom-layer 30-Pin header H1/H4 in 3 real samples: component (-1333.8458, -22.3951) mil, angle 270, bottom layer;
# footprint pad 1 at (-551.181, -88.577), pad 2 at (-551.181, 88.583), pad 3 at (-472.441, -88.577)
HDR_C = (-1333.8458, -22.3951, 270)


def to_mm_yd(xy):
    """Canvas mil, Y up -> mm, Y down (same conversion as inside load_epro2)."""
    return xy[0] * bc.MIL, -xy[1] * bc.MIL


def test_eda_pad_top_rotation():
    # top layer, 90 degrees counter-clockwise: footprint-local (100, 0) -> (0, 100)
    x, y = bc.eda_pad_xy(10, 20, 90, False, 100, 0)
    assert abs(x - 10) < 1e-9 and abs(y - 120) < 1e-9
    x, y = bc.eda_pad_xy(0, 0, 0, False, 3, 4)
    assert (round(x, 9), round(y, 9)) == (3, 4)


def test_eda_pad_bottom_mirror():
    # bottom layer mirrors first (negate y) then rotates: at angle 0 only the sign of y changes, x is unchanged
    x, y = bc.eda_pad_xy(0, 0, 0, True, 3, 4)
    assert (round(x, 9), round(y, 9)) == (3, -4)
    # mirror then rotate 90 degrees: (3, 4) -> (3, -4) -> (4, 3); rotating first and mirroring after would give (-4, -3), which differs
    x, y = bc.eda_pad_xy(0, 0, 90, True, 3, 4)
    assert (round(x, 9), round(y, 9)) == (4, 3)


def test_eda_pad_real_header_matches_baseline():
    want = bc.expected_header_pads(BASE["header"])
    for num, (px, py) in ((1, (-551.181, -88.577)), (2, (-551.181, 88.583)), (3, (-472.441, -88.577))):
        x, y = to_mm_yd(bc.eda_pad_xy(HDR_C[0], HDR_C[1], HDR_C[2], True, px, py))
        assert abs(x - want[num][0]) < 0.01 and abs(y - want[num][1]) < 0.01, (num, x, y, want[num])


def test_eda_outline_geom():
    # real sample 1: straight lines + four R2 corners (-90 degree arcs)
    path = [-1555.1181, -787.4016, "ARC", -90, -1633.8583, -708.6614, "L", -1633.8583, 708.6614,
            "ARC", -90, -1555.1181, 787.4016, "L", 1555.1181, 787.4016, "ARC", -90, 1633.8583, 708.6614,
            "L", 1633.8583, -708.6614, "ARC", -90, 1555.1181, -787.4016, "L", -1555.1181, -787.4016]
    pts, radii = bc.eda_outline_geom(path)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    assert abs((max(xs) - min(xs)) * bc.MIL - 83.0) < 0.01 and abs((max(ys) - min(ys)) * bc.MIL - 40.0) < 0.01
    assert {round(r * bc.MIL, 2) for r in radii} == {2.0}
    # real sample 2: R rectangle, the last number is the corner radius
    pts, radii = bc.eda_outline_geom(["R", -1633.858, 787.402, 3267.717, 1574.803, 0, 78.74])
    assert abs((max(p[0] for p in pts) - min(p[0] for p in pts)) * bc.MIL - 83.0) < 0.01
    assert [round(r * bc.MIL, 2) for r in radii] == [2.0]
    # the outward bulge of a semicircle counts toward the bounding box: (0,0)->(10,0) clockwise 180 degrees, the arc is above the chord (Y up)
    pts, radii = bc.eda_outline_geom([0, 0, "ARC", -180, 10, 0])
    assert abs(max(p[1] for p in pts) - 5) < 1e-6 and abs(min(p[1] for p in pts)) < 1e-6
    pts, _ = bc.eda_outline_geom([0, 0, "ARC", 180, 10, 0])  # counter-clockwise puts it below
    assert abs(min(p[1] for p in pts) + 5) < 1e-6
    try:
        bc.eda_outline_geom([0, 0, "C", 1, 1, 2, 2, 3, 3])
        assert False, "Bezier must raise an error"
    except SystemExit:
        pass


def _line(typ, id_, body):
    return json.dumps({"type": typ, "ticket": 1, "id": id_}) + "||" + json.dumps(body, ensure_ascii=False) + "|"


def _doc(doc_type, uuid, meta, records=()):
    head = json.dumps({"type": "DOCHEAD"}) + "||" + json.dumps({"docType": doc_type, "uuid": uuid}) + "|"
    return [head, _line("META", "META", meta)] + list(records)


def make_epro2(path, pcb_titles=("PCB1",), header_layer=2, silk_stroke=6):
    """Synthesize a minimal .epro2: one board matching the baseline (4 holes + bottom 30-Pin + AT24C02 at 0x53)."""
    nets = good_nets()
    lines = _doc("FOOTPRINT", "fp30", {"title": "HDR30"}, [
        _line("PAD", f"p{n}", {"num": str(n), "centerX": -551.181 + ((n - 1) // 2) * 78.7402,
                               "centerY": -88.5827 if n % 2 else 88.5827, "hole": None,
                               "defaultPad": {"padType": "RECT", "width": 35, "height": 118}}) for n in range(1, 31)])
    lines += _doc("FOOTPRINT", "fp8", {"title": "SOIC8"}, [
        _line("PAD", f"q{n}", {"num": str(n), "centerX": 0, "centerY": 0, "hole": None, "defaultPad": {}}) for n in range(1, 9)])
    lines += _doc("DEVICE", "dv30", {"title": "HDR-30P"}) + _doc("DEVICE", "dv8", {"title": "AT24C02C-SSHM-T"})
    lines += _doc("BOARD", "bd1", {"title": "Board1"})
    ee_nets = ["3V3", "3V3", "GND", "GND", "SDA", "SCL", "WP", "3V3"]
    for k, title in enumerate(pcb_titles):
        rec = [_line("LAYER", json.dumps(["LAYER", i]), {"layerId": i, "layerType": t, "use": u})
               for i, t, u in ((1, "TOP", True), (2, "BOTTOM", True), (3, "TOP_SILK", True), (15, "SIGNAL", True),
                               (16, "SIGNAL", True), (17, "SIGNAL", False))]
        rec.append(_line("POLY", "outline", {"layerId": 11, "width": 10, "path": ["R", -1633.858, 787.402, 3267.717, 1574.803, 0, 78.74]}))
        for i, (x, y) in enumerate(BASE["mounting_holes"]["positions"]):
            rec.append(_line("PAD", f"m{i}", {"netName": "GND", "layerId": 12, "num": "", "centerX": x / bc.MIL, "centerY": -y / bc.MIL,
                                              "hole": {"holeType": "ROUND", "width": 2.0 / bc.MIL, "height": 2.0 / bc.MIL},
                                              "defaultPad": {"padType": "ELLIPSE", "width": 3.0 / bc.MIL, "height": 3.0 / bc.MIL}}))
        rec.append(_line("COMPONENT", "cH", {"layerId": header_layer, "x": HDR_C[0], "y": HDR_C[1], "angle": HDR_C[2]}))
        rec.append(_line("COMPONENT", "cU", {"layerId": 1, "x": 0, "y": 0, "angle": 0}))
        for cid, des, fp, dv in (("cH", "H1", "fp30", "dv30"), ("cU", "U7", "fp8", "dv8")):
            for key, val in (("Designator", des), ("Footprint", fp), ("Device", dv)):
                rec.append(_line("ATTR", cid + key, {"parentId": cid, "key": key, "value": val, "layerId": 3,
                                                     "valueVisible": key == "Designator", "fontSize": 45, "strokeWidth": silk_stroke}))
        for n in range(1, 31):
            rec.append(_line("PAD_NET", json.dumps(["PAD_NET", "cH", str(n), f"p{n}"]), {"padNet": nets[n]}))
        for n, net in enumerate(ee_nets, 1):
            rec.append(_line("PAD_NET", json.dumps(["PAD_NET", "cU", str(n), f"q{n}"]), {"padNet": net}))
        lines += _doc("PCB", f"pcb{k}", {"title": title, "board": "bd1"}, rec)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("project2.json", json.dumps({"title": "t"}))
        z.writestr("proj.epru", "\n".join(lines))


def test_load_epro2_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "t.epro2"
        make_epro2(f)
        m = bc.load_epro2(str(f))
        assert m["copper_layers"] == 4 and m["outline"]["radii"] == [2.0]
        assert m["header"]["candidates"] == ["H1"]
        code, lines = run(m, "0x53")
        assert code == 0 and not fail_lines(lines), lines
        assert any("H1 is on the bottom side" in l for l in lines) and any("U7 address 0x53" in l for l in lines)
        # the same board with the header on top must FAIL (proves the bottom-layer flag is really read, not always bottom)
        make_epro2(f, header_layer=1)
        code, lines = run(bc.load_epro2(str(f)), "0x53")
        assert any("H1 is on the top side" in l for l in fail_lines(lines)) and any("pad positions off" in l for l in fail_lines(lines))
        # stroke width too small
        make_epro2(f, silk_stroke=3)
        assert any("NOT MET" in l for l in fail_lines(run(bc.load_epro2(str(f)))[1]))


def test_load_epro2_board_selection():
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "t.epro2"
        make_epro2(f, pcb_titles=("PCB1", "PCB2"))
        try:
            bc.load_epro2(str(f))
            assert False, "several PCBs with none specified must exit"
        except SystemExit as e:
            assert "--board" in str(e)
        assert bc.load_epro2(str(f), None, "PCB2")["header"]["candidates"] == ["H1"]
        try:
            bc.load_epro2(str(f), None, "does-not-exist")
            assert False
        except SystemExit:
            pass


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                fails += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print("ALL PASS" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
