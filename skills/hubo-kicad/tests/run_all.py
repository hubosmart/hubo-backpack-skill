"""hubo-kicad self-test: pure functions only, no pcbnew needed, but shapely is required (KiCad's bundled python has it).

Usage: <KiCad>/bin/python tests/run_all.py    the last line should be ALL PASS
"""
import math
import pathlib
import sys
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from shapely.geometry import LineString, Point, Polygon, box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

import export_prod  # noqa: E402
import fence_check  # noqa: E402
import hs_review  # noqa: E402
import impedance_check  # noqa: E402
import island_via  # noqa: E402
import mini_route  # noqa: E402
import stackup  # noqa: E402
import step_bbox  # noqa: E402
import plane_check  # noqa: E402
import power_rules  # noqa: E402
import prod_gate  # noqa: E402
import miter  # noqa: E402
import route_check  # noqa: E402
import route_prep  # noqa: E402

DSN = """(pcb x
  (structure
    (layer F.Cu
      (type signal)
    )
    (layer In1.Cu
      (type signal)
    )
    (layer In2.Cu
      (type signal)
    )
    (layer B.Cu
      (type signal)
    )
  )
  (network
    (net GND
      (pins U1-1 U1-2)
    )
    (net "Net-(R1-1)"
      (pins R1-1 C1-2)
    )
    (net SIG
      (pins RF1-5)
    )
  )
  (wiring
    (wire (path F.Cu 150 0 0 1 1)(net SIG)(type route))
  )
)"""


def test_strip_nets_keeps_other_nets():
    out = route_prep.strip_nets(DSN, {"GND", "SIG"})
    assert "(net GND\n      (pins)" in out
    assert "(pins R1-1 C1-2)" in out
    assert "(net SIG\n      (pins)" in out


def test_inner_and_plane_layers():
    assert route_prep.inner_layers(DSN) == ["In1.Cu", "In2.Cu"]
    out = route_prep.plane_layers(DSN, ["In1.Cu", "In2.Cu"])
    assert out.count("(type power)") == 2 and "(layer F.Cu\n      (type signal)" in out


def test_plane_layers_display_names():
    # After stackup.py renames copper layers, the DSN exported by KiCad uses the display names
    names = {"F.Cu": "TopLayer", "In1.Cu": "GND", "In2.Cu": "PWR", "B.Cu": "BottomLayer"}
    dsn = DSN
    for std, disp in names.items():
        dsn = dsn.replace(f"(layer {std}\n", f"(layer {disp}\n")
    assert route_prep.inner_layers(dsn) == ["GND", "PWR"]
    assert route_prep.resolve_layers(dsn, ["GND", "PWR"]) == ["GND", "PWR"]
    assert route_prep.resolve_layers(dsn, ["In1.Cu", "In2.Cu"]) == ["GND", "PWR"]      # standard names converted by stackup position
    assert route_prep.resolve_layers(DSN, ["In1.Cu"]) == ["In1.Cu"]
    for bad in (["In3.Cu"], ["GND2"]):
        try:
            route_prep.resolve_layers(dsn, bad)
            raise AssertionError(bad)
        except ValueError as e:
            assert bad[0] in str(e) and "TopLayer" in str(e)
    out = route_prep.plane_layers(dsn, ["GND", "PWR"])
    assert out.count("(type power)") == 2 and "(layer TopLayer\n      (type signal)" in out


def test_plane_gaps_only_between_nets():
    # Board 40x20: GND on the left, +5V on the right, split gap x 25 to 25.3; GND has a slot left by a signal trace (reaching in from the board edge)
    gnd = box(0, 0, 25, 20).difference(box(-1, 9.8, 15, 10.2))
    fills = [("GND", gnd), ("+5V", box(25.3, 0, 40, 20))]
    gaps = route_prep.plane_gaps(box(0, 0, 40, 20), fills)
    u = unary_union(gaps)
    assert u.contains(Point(25.15, 10)) and u.contains(Point(25.15, 3))
    assert not u.intersects(Point(8, 10))                                               # the signal slot is not a split gap
    assert route_prep.plane_gaps(box(0, 0, 40, 20), [("GND", gnd)]) == []               # only one net: no gap


def test_island_via_spot_not_in_pad():
    pad = box(0.2, 0.2, 1.8, 1.8)
    # The island is almost only a pad: no legal position, the via must not land in a pad (including same-net)
    assert island_via.spot(box(0, 0, 2, 2), Point(1e6, 1e6), [pad], 0.4, 0.12) is None
    c = island_via.spot(box(0, 0, 4, 2), Point(1e6, 1e6), [pad], 0.4, 0.12)
    assert c is not None and c.buffer(0.2).distance(pad) >= 0.12 - 1e-6


def test_mini_route_rule_areas_and_pad_clearance():
    area = box(0, 0, 2, 2)
    track, via = mini_route.rule_area_blocks(
        [(area, {0, 2}, True, False), (box(10, 0, 12, 2), {2}, False, True), (box(20, 0, 22, 2), {0}, False, False)],
        (0, 2), hw=0.1, via_r=0.225)
    assert unary_union(track[0]).contains(Point(2.05, 1)) and not unary_union(track[0]).intersects(Point(11, 1))
    assert unary_union(track[2]).contains(Point(1, 1)) and not unary_union(track[0]).intersects(Point(21, 1))
    assert len(via) == 1 and via[0].contains(Point(12.2, 1))                              # no-via area grown by the via radius
    assert mini_route.pad_clearance(0.125, None, None) == 0.125
    assert mini_route.pad_clearance(0.125, 0.85, None) == 0.85                             # pad-local clearance
    assert mini_route.pad_clearance(0.125, None, 0.3) == 0.3                               # footprint-local clearance
    assert mini_route.pad_clearance(0.125, 0.05, None) == 0.125                            # does not relax the global clearance


def test_mini_route_via_size_param():
    import pcbnew
    base = ["x.kicad_pcb", "N", "U1.1"]
    assert mini_route.parse_args(base).via == [0.45, 0.25]                                 # defaults unchanged
    a = mini_route.parse_args(base + ["--via", "0.45", "0.30"])
    assert a.via == [0.45, 0.30]
    b = pcbnew.BOARD()
    v = mini_route.make_via(b, pcbnew.NETINFO_ITEM(b, "N"), 1.0, 2.0, *a.via)              # new vias use the parameters
    assert pcbnew.ToMM(v.GetWidth(pcbnew.F_Cu)) == 0.45 and pcbnew.ToMM(v.GetDrill()) == 0.30


def test_protect_wiring():
    assert "(type protect)" in route_prep.protect_wiring(DSN)


def test_new_items_ignores_direction():
    orig = [["trk", "F.Cu", 0, 0, 10, 0, 254000, "A"], ["via", 5, 5, 600, 300, "GND"]]
    routed = [["trk", "F.Cu", 10, 0, 0, 0, 254000, "A"], ["via", 5, 5, 600, 300, "GND"],
              ["trk", "B.Cu", 0, 0, 10, 0, 254000, "A"]]
    assert route_prep.new_items(orig, routed) == [routed[2]]


def test_edge_band_four_pieces_no_holes():
    parts = route_prep.edge_band(box(0, 0, 80, 40), 1.5)
    assert len(parts) == 4 and all(not p.interiors for p in parts)
    assert abs(sum(p.area for p in parts) - (80 * 40 - 77 * 37)) < 1e-6


def test_classify_hole():
    planes = {"+5V", "3V3", "GND"}
    assert plane_check.classify_hole("GND", planes) == "WARN"
    assert plane_check.classify_hole("3V3", planes) == "INFO"
    assert plane_check.classify_hole("SCK", planes) == "FAIL"
    # In a BGA fan-out area (--bga-box) non-high-speed vias crossing a line drop to WARN; high-speed prefixes stay FAIL
    assert plane_check.classify_hole("SCK", planes, in_bga=True, hs=("CSI_",)) == "WARN"
    assert plane_check.classify_hole("CSI_D0_P", planes, in_bga=True, hs=("CSI_",)) == "FAIL"
    assert plane_check.classify_hole("GND", planes, in_bga=True, hs=("CSI_",)) == "WARN"


def test_spacing_and_gap():
    outl = {"A": box(0, 0, 10, 10), "B": box(10.254, 0, 20, 10)}
    sp = plane_check.plane_spacing(outl)
    assert abs(sp[("A", "B")] - 0.254) < 1e-9
    gap = plane_check.split_gaps(box(0, 0, 20, 10), outl, min_area=0.1)
    assert abs(gap.area - 2.54) < 1e-6


def test_spacing_skips_pair_separated_by_third_plane():
    # B, 0.6 mm wide, sits between A and C: the nearest A<->C distance 1.12 mm is not a split gap (not adjacent); A<->B and B<->C are judged as usual
    outl = {"A": box(0, 0, 10, 10), "B": box(10.26, 0, 10.86, 10), "C": box(11.12, 0, 20, 10)}
    sp = plane_check.plane_spacing(outl)
    assert ("A", "C") not in sp
    assert abs(sp[("A", "B")] - 0.26) < 1e-9 and abs(sp[("B", "C")] - 0.26) < 1e-9
    # With no plane in between, the 1.12 mm wide gap is still reported
    sp2 = plane_check.plane_spacing({"A": box(0, 0, 10, 10), "C": box(11.12, 0, 20, 10)})
    assert abs(sp2[("A", "C")] - 1.12) < 1e-9


def test_facing_length_moat_vs_corner():
    # Two blocks facing each other for 10 mm with a 0.76 gap: a split gap (facing length about 10 mm); adjacent across a void only at a corner: not
    moat = {"A": box(0, 0, 10, 10), "B": box(10.76, 0, 20, 10)}
    assert 9.5 < plane_check.facing_length(moat, "A", "B") < 13      # rounded corners from growing both ends add about 2 mm
    corner = {"A": box(0, 0, 10, 10), "B": box(10.6, 10.6, 20, 20)}
    assert plane_check.facing_length(corner, "A", "B") < plane_check.FACE_MIN


def test_split_gap_only_between_two_planes():
    # Local-pour layer: A covers only the left half, the void on the right is not a split gap; 0.3 mm between A and B is
    outl = {"A": box(0, 0, 10, 10), "B": box(10.3, 0, 14, 10)}
    gap = plane_check.split_gaps(box(0, 0, 40, 10), outl, min_area=0.1)
    assert abs(gap.area - 3.0) < 1e-6
    # A notch next to only one plane (e.g. a mounting hole at the board edge) does not count as crossing a line
    notch = box(0, 0, 10, 10).difference(box(4, 8, 6, 10))
    g2 = plane_check.split_gaps(box(0, 0, 10, 10), {"A": notch}, min_area=0.1)
    assert g2.is_empty


def test_nested_plane_keeps_outer_hole():
    # A small 3V3 plane sits in a hole of the big GND plane (gap 0.26): the outline must not fill the hole, otherwise the two "overlap" and the gap cannot be computed
    gnd = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)], [[(5, 5), (15, 5), (15, 15), (5, 15)]])
    v33 = box(5.26, 5.26, 14.74, 14.74)
    fills = [("GND", gnd), ("3V3", v33)]
    outl = plane_check.nested_holes(plane_check.outlines_by_net(fills), fills)
    assert abs(outl["GND"].distance(outl["3V3"]) - 0.26) < 1e-9
    gap = plane_check.split_gaps(box(0, 0, 20, 20), outl, min_area=0.1)
    assert abs(gap.area - (100 - 9.48 * 9.48)) < 1e-6
    # A mere antipad hole (no other plane inside) is filled as usual
    assert abs(plane_check.nested_holes(plane_check.outlines_by_net([("GND", gnd)]), [("GND", gnd)])["GND"].area - 400) < 1e-9


def test_outlines_drop_holes_and_small_rings():
    with_hole = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(4, 4), (6, 4), (6, 6), (4, 6)]])
    ring = box(20, 20, 21, 21)  # 1 mm^2, treated as a via annulus
    out = plane_check.outlines_by_net([("A", with_hole), ("A", ring)])
    assert abs(out["A"].area - 100) < 1e-9


def test_neck_with_and_without_load():
    # Two 10x10 blocks joined by a 1 mm wide neck
    poly = box(0, 0, 10, 10).union(box(10, 4.5, 15, 5.5)).union(box(15, 0, 25, 10))
    w, where, n = plane_check.necks(poly, [0.5, 2.0], loads=[(20, 5)])
    assert w == 2.0 and 9 < where[0] < 16 and n == 1
    _, _, n0 = plane_check.necks(poly, [2.0], loads=[(5, 5)])
    assert n0 == 0
    assert plane_check.necks(box(0, 0, 10, 10), [2.0], loads=[]) is None


def test_copper_layers_and_cpl():
    assert export_prod.copper_layers(2) == ["F.Cu", "B.Cu"]
    assert export_prod.copper_layers(4) == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    rows = [{"Ref": "C1", "PosX": "-1.3", "PosY": "16.4", "Rot": "90.0", "Side": "bottom"}]
    assert export_prod.pos_to_jlc(rows) == [("C1", "-1.3mm", "16.4mm", "Bottom", "90.0")]



def test_impedance_pair_names():
    assert impedance_check.pair_of("TMDS_D0_P") == ("TMDS_D0", "TMDS_D0_N")
    assert impedance_check.pair_of("USB_D+") == ("USB_D", "USB_D-")
    assert impedance_check.pair_of("TMDS_D0_N") is None


def test_impedance_select_layers_exempt():
    # Only the given layers are checked; segments entirely inside an exempt box (BGA/connector fan-out, fine traces kept as in the reference board) are not checked
    segs = [("F.Cu", (0, 0), (5, 0)), ("In2.Cu", (5, 0), (9, 0)), ("F.Cu", (20, 0), (21, 0))]
    ws = [0.094, 0.099, 0.0965]
    s2, w2 = impedance_check.select_segs(segs, ws, layers=["F.Cu"], exempt=[box(19, -1, 22, 1)])
    assert s2 == [segs[0]] and w2 == [0.094]
    s3, w3 = impedance_check.select_segs(segs, ws, layers=[], exempt=[])
    assert s3 == segs and w3 == ws


def test_impedance_ok_neck_split_narrow():
    W, S = 0.1295, 0.2662
    cc = W + S
    p = [("F.Cu", (0, 0), (10, 0))]
    n_ok = [("F.Cu", (0, cc), (10, cc))]
    f, _ = impedance_check.check_pair(p, n_ok, [W, W], W, S, 0.005, 0.30, 1.5)
    assert f == []
    # Necking from 1 mm to center distance 0.30: allowed; 3 mm of necking: too long
    n_neck = [("F.Cu", (0, 0.30), (1, 0.30)), ("F.Cu", (1, 0.30), (1.1, cc)), ("F.Cu", (1.1, cc), (10, cc))]
    f, _ = impedance_check.check_pair(p, n_neck, [W] * 4, W, S, 0.005, 0.30, 1.5)
    assert f == []
    n_long = [("F.Cu", (0, 0.30), (3, 0.30)), ("F.Cu", (3, 0.30), (3.1, cc)), ("F.Cu", (3.1, cc), (10, cc))]
    f, _ = impedance_check.check_pair(p, n_long, [W] * 4, W, S, 0.005, 0.30, 1.5)
    assert any("necked section longer than" in x for x in f)
    n_narrow = [("F.Cu", (0, 0.25), (10, 0.25))]
    f, _ = impedance_check.check_pair(p, n_narrow, [W, W], W, S, 0.005, 0.30, 1.5)
    assert any("center distance" in x for x in f)
    # Split at a pad (center distance 0.5) is only reported; width 0.15 is FAIL; 0.1296 (SES rounding) is not
    n_split = [("F.Cu", (0, 0.5), (10, 0.5))]
    f, info = impedance_check.check_pair(p, n_split, [W, W], W, S, 0.005, 0.30, 1.5)
    assert f == [] and "split 1 section(s)" in info
    f, _ = impedance_check.check_pair(p, n_ok, [W, 0.15], W, S, 0.005, 0.30, 1.5)
    assert any("trace width" in x for x in f)
    f, _ = impedance_check.check_pair(p, n_ok, [W, 0.1296], W, S, 0.005, 0.30, 1.5)
    assert f == []



def test_step_bbox_vertices_only():
    txt = """#1 = CARTESIAN_POINT ( 'NONE',  ( 0.0, 0.0, -9.0 ) ) ;
#2 = CARTESIAN_POINT ( 'NONE',  ( -1.5, 2.0, 0.0 ) ) ;
#3 = CARTESIAN_POINT ( 'NONE',  ( 1.5, -1.0, 3.25 ) ) ;
#4 = VERTEX_POINT ( 'NONE', #2 ) ;
#5 = VERTEX_POINT ( 'NONE', #3 ) ;"""
    # #1 is a helper point such as the coordinate-system origin, not counted
    assert step_bbox.bbox(txt) == ((-1.5, 1.5), (-1.0, 2.0), (0.0, 3.25))
    assert step_bbox.bbox("#1 = CARTESIAN_POINT ( 'NONE', ( 0, 0, 0 ) ) ;") is None


def test_stackup_from_table():
    st = stackup.find_stack("JLC04161H-3313")
    txt = stackup.stackup_sexpr(st["stack"])
    # 4 layers: 3 dielectrics (prepreg-core-prepreg), 4 copper, 2 mask; laminate thickness 1.56 mm matches JLCPCB "finished 1.56 mm"
    assert txt.count('(type "copper")') == 4 and txt.count('(layer "dielectric') == 3
    assert '(epsilon_r 4.1)' in txt and txt.startswith("\t\t(stackup") and txt.endswith("\t\t)\n")
    lam = sum(e["thickness"] for e in st["stack"] if e.get("layer") not in stackup.MASK_TYPE)
    assert abs(lam - 1.5642) < 1e-6


def test_hs_pair_report_vias_and_skew():
    vias = {"USB+": 2, "USB-": 1, "CSI_D0_P": 0, "CSI_D0_N": 0, "CSI_D1_P": 1, "CSI_D1_N": 1}
    lengths = {"USB+": 20.0, "USB-": 20.1, "CSI_D0_P": 10.0, "CSI_D0_N": 10.2,
               "CSI_D1_P": 10.0, "CSI_D1_N": 10.3, "LED": 5.0}
    out = hs_review.pair_report(vias, lengths, max_vias=2, skew=[("USB", 5), ("CSI_", 10)])
    by = lambda key: [l for l in out if key in l]  # noqa: E731
    usb = by("USB ")
    assert usb[0].startswith("7 USB vias P 2 / N 1") and usb[0].endswith("NOT MET")   # the two traces have different via counts
    assert "off by 3.9 mil" in usb[1] and not usb[1].endswith("NOT MET")                  # 0.1 mm = 3.9 mil <= 5
    d0, d1 = by("CSI_D0 "), by("CSI_D1 ")
    assert not d0[0].endswith("NOT MET") and not d0[1].endswith("NOT MET")              # 0.2 mm = 7.9 mil <= 10
    assert d1[1].endswith("NOT MET")                                                     # 0.3 mm = 11.8 mil > 10
    assert not any("LED" in l for l in out)                                             # non-differential nets are not involved
    assert hs_review.pair_report({"A_P": 3, "A_N": 3}, {"A_P": 1.0, "A_N": 1.0}, max_vias=2)[0].endswith("NOT MET")
    # With a P-N target: judged by the difference from the target (P is 0.3 mm shorter than N = -11.8 mil, target -10 -> off by 1.8 mil <= 5)
    tg = hs_review.pair_report({}, {"CSI_D1_P": 10.0, "CSI_D1_N": 10.3}, skew=[("CSI_", 5)], target={"CSI_D1": -10.0})
    assert "P-N -11.8 mil" in tg[1] and "off by 1.8 mil" in tg[1] and not tg[1].endswith("NOT MET")
    tg = hs_review.pair_report({}, {"CSI_D1_P": 10.0, "CSI_D1_N": 10.3}, skew=[("CSI_", 5)], target={"CSI_D1": 10.0})
    assert tg[1].endswith("NOT MET")                                                     # sign flipped: off by 21.8 mil

def test_hs_stub_and_pair_gap():
    out = hs_review.stub_report([("CSI_D0_P", "(1, 1)", 2, 1.6), ("CSI_D1_P", "(2, 2)", 1, 1.6),
                                 ("CSI_D2_P", "(3, 3)", 0, 1.6), ("LED", "(4, 4)", 1, 1.6)], [("CSI_", 15)])
    assert len(out) == 2 and "stub 0 mil" in out[0] and not out[0].endswith("NOT MET")   # both outer layers connected: no stub
    assert "stub 63 mil" in out[1] and out[1].endswith("NOT MET")                         # one layer only: the whole board thickness
    lens = {"CSI_D0_P": 10.0, "CSI_D0_N": 10.0, "CSI_D1_P": 10.5, "CSI_D1_N": 10.5, "X_P": 1.0, "X_N": 1.0}
    gap = hs_review.pair_gap_report(lens, [("CSI_", 30), ("X_", 5)])
    assert len(gap) == 1 and "19.7 mil" in gap[0] and not gap[0].endswith("NOT MET")     # 0.5 mm = 19.7 mil <= 30
    assert hs_review.pair_gap_report(lens, [("CSI_", 10)])[0].endswith("NOT MET")


def test_route_check_turns():
    o = (0.0, 0.0)
    seg = lambda a, b, n="N": (n, "F.Cu", a, b)  # noqa: E731
    kinds = lambda segs: sorted(k for k, *_ in route_check.turns(segs))  # noqa: E731
    assert kinds([seg((-1, 0), o), seg(o, (1, 0))]) == []                       # straight line
    assert kinds([seg((-1, 0), o), seg(o, (1, 1))]) == []                       # 45 degree turn
    assert kinds([seg((-1, 0), o), seg(o, (0, 1))]) == ["right angle"]          # 90 degrees
    assert kinds([seg((-1, 0), o), seg(o, (-1, 0.5))]) == ["acute angle"]       # folds back into an acute angle
    assert kinds([seg((-1, 0), o), seg(o, (1, 0)), seg(o, (0, 1))]) == []       # a T-junction at 90 degrees is not a gap
    assert kinds([seg((-1, 0), o), seg(o, (1, 0)), seg(o, (1, 0.5))]) == ["acute gap"]
    assert kinds([seg((-1, 0), o), seg(o, (0, 1), "M")]) == []                  # different nets do not join
    assert kinds([("N", "F.Cu", (-1, 0), o), ("N", "B.Cu", o, (0, 1))]) == []   # different layers do not join

def test_miter_fillet():
    o = (0.0, 0.0)
    assert miter.fillet((-1, 0), o, (1, 1)) is None                       # 45 degrees: unchanged
    assert miter.fillet((-1, 0), o, (1, 0)) is None                       # straight line
    assert miter.fillet((-1, 0), o, (-1, 0.05)) is None                   # doubled back >= 170 degrees: unchanged
    for b in ((0, 1), (-1, 0.8), (-1, 0.3)):                              # 90 degrees, about 141, about 163
        poly = miter.fillet((-1, 0), o, b)
        segs = [((-1, 0), poly[0])] + list(zip(poly, poly[1:])) + [(poly[-1], b)]
        segs = [("N", "F.Cu", p, q) for p, q in segs]
        assert route_check.turns(segs, nd=6) == [], (b, route_check.turns(segs, nd=6))   # no corner > 45 degrees after the change
        assert abs(math.hypot(*poly[0]) - 0.45) < 1e-9 or abs(math.hypot(*poly[0]) - 0.5) < 1e-9
    assert math.dist(miter.fillet((-0.4, 0), o, (0, 3))[0], (-0.18, 0.0)) < 1e-9   # a short segment pulls back only 45%


def test_miter_corners():
    segs = {"a": ("N", 0, 100, (-1.0, 0.0), (0.0, 0.0)), "b": ("N", 0, 100, (0.0, 0.0), (0.0, 1.0)),
            "c": ("M", 0, 100, (5.0, 0.0), (6.0, 0.0)), "d": ("M", 0, 200, (6.0, 0.0), (6.0, 1.0))}
    todo, manual = miter.corners(segs, lambda *_: False)
    assert [(k1, e1, k2, e2) for k1, e1, k2, e2, _ in todo] == [("a", 1, "b", 0), ("c", 1, "d", 0)]   # changed even with different widths
    todo, manual = miter.corners(segs, lambda net, layer, p: net == "N")
    assert [t[0] for t in todo] == ["c"] and manual == []                                 # corners covered by a pad are not changed
    spike = {"a": ("N", 0, 100, (-1.0, 0.0), (0.0, 0.0)), "b": ("N", 0, 100, (0.0, 0.0), (-1.0, 0.01))}
    assert miter.corners(spike, lambda *_: False)[1][0][0] == "doubled back"


def test_prod_gate_netclass_floor():
    f = prod_gate.netclass_floor
    assert f([("Default", 0.1), ("HDMI", 0.15)], {}) == []                  # Default not judged; 0.15 passes
    assert len(f([("USB", 0.12), ("CONN", 0.1)], {})) == 2
    assert f([("CONN", 0.1)], {"CONN": "owner agreed, see design document"}) == []         # waived by exception
    assert f([("X", 0.127)], {}) == []


def test_fence_grade_two_tier():
    assert fence_check.parse_limit("3") == (3.0, 6.0) and fence_check.parse_limit("4/5") == (4.0, 5.0)
    assert fence_check.grade(2.9, 3, 6) is None
    assert fence_check.grade(4.65, 4, 8) == "relaxed"
    assert fence_check.grade(8.2, 4, 8) == "exceeded"


def test_pad_overlaps():
    L = {"F.Cu"}
    pads = [("D21", "1", L, (0.0, 0.0, 1.0, 0.7)), ("R46", "1", L, (0.8, 0.0, 1.4, 0.5)),     # overlap (reported even on the same net)
            ("C1", "1", L, (5.0, 0.0, 5.5, 0.5)), ("C1", "2", L, (5.55, 0.0, 6.0, 0.5)),      # not compared within one component
            ("R2", "1", L, (1.45, 0.0, 1.9, 0.5)),                                              # 0.05 from R46 < 0.1
            ("U1", "1", {"B.Cu"}, (0.0, 0.0, 1.0, 0.7))]                                        # different layers are not compared
    out = route_check.pad_overlaps(pads)
    keys = sorted((a, c) for a, _, c, _, _ in out)
    assert keys == [("D21", "R46"), ("R46", "R2")] or keys == [("D21", "R46"), ("R2", "R46")], keys
    assert next(d for a, _, c, _, d in out if {a, c} == {"D21", "R46"}) == 0.0
    assert route_check.pad_overlaps([("A", "1", L, (0, 0, 1, 1)), ("B", "1", L, (1.1, 0, 2, 1))]) == []   # distance 0.1 passes


def test_route_check_vip_exempt():
    # BGAs listed in --vip: same-net via-in-pad is waived under the via-in-pad process; other-net vias and unlisted parts are still NOT MET
    assert route_check.vip_exempt("VDD", "VDD", "U38", {"U38"})
    assert not route_check.vip_exempt("SIG", "VDD", "U38", {"U38"})
    assert not route_check.vip_exempt("VDD", "VDD", "C5", {"U38"})


def test_route_check_ep_exempt():
    ep = route_check.ep_exempt
    assert ep("GND", 0.2, "GND", (1.7, 1.7), 21, True)                 # QFN thermal pad ground via
    assert not ep("SNS_P", 0.2, "SNS_P", (0.6, 0.4), 8, False)         # signal via on a pin pad
    assert not ep("GND", 0.3, "GND", (1.0, 1.3), 2, True)              # 0805 capacitor pad: too few pins
    assert not ep("GND", 0.4, "GND", (1.7, 1.7), 21, True)             # drill > 0.3 mm
    assert not ep("SIG", 0.2, "GND", (1.7, 1.7), 21, True)             # other-net via
    assert not ep("GND", 0.2, "GND", (3.0, 1.0), 30, False)            # header pad: not the largest pad
    assert not ep("", 0.2, "", (1.7, 1.7), 21, True)                   # no net


def test_power_rules_render_apply_idempotent():
    power = [{"nets": ["+5V", "VBUS"], "current_a": 2, "layer": "outer", "width_mm": 1.0, "basis": "calculator\nouter 1oz"}]
    blk = power_rules.render(power)
    assert "(condition \"A.NetName == '+5V' || A.NetName == 'VBUS'\")" in blk
    assert "(constraint track_width (min 1mm)))" in blk and "calculator outer 1oz" in blk      # line break flattened
    base = "(version 1)\n(rule \"HZ_track_width\"\n\t(constraint track_width (min 0.127mm)))\n"
    once = power_rules.apply(base, blk)
    assert once.index("HZ_track_width") < once.index("HZ_PWR_")                         # power rules come later, overriding the global minimum
    assert power_rules.apply(once, blk) == once                                          # regenerating changes nothing
    blk2 = power_rules.render([dict(power[0], width_mm=1.5)])
    twice = power_rules.apply(once, blk2)
    assert "min 1.5mm" in twice and "min 1mm" not in twice and twice.count(power_rules.BEGIN) == 1
    assert power_rules.apply("", blk).startswith("(version 1)")


def test_power_rules_ipc2221():
    # Against the KiCad calculator (IPC-2221, 10 C rise, 1 oz = 35 um): 2 A outer about 0.78 mm, 1 A outer about 0.30 mm
    assert abs(power_rules.ipc2221_width_mm(2.0, "outer") - 0.781) < 0.002
    assert abs(power_rules.ipc2221_width_mm(1.0, "outer") - 0.300) < 0.002
    assert power_rules.ipc2221_width_mm(1.0, "inner") > 5 * power_rules.ipc2221_width_mm(1.0, "outer")  # inner layer 0.5 oz, k halved
    w, calc = power_rules.required_width({"current_a": 2.0, "layer": "outer"})
    assert w >= calc and w == 0.782
    assert power_rules.required_width({"current_a": 0.05, "layer": "outer", "width_mm": 0.2})[0] == 0.2   # the larger margin wins
    assert power_rules.required_width({"current_a": 0.05, "layer": "outer"})[0] == 0.127                  # does not relax the base minimum
    assert "min 0.127mm" in (pathlib.Path(power_rules.BASE).read_text(encoding="utf-8"))                   # consistent with the template


def test_power_rules_load_cfg_validation():
    import json
    import tempfile
    ok = {"power": [{"nets": ["3V3"], "current_a": 0.5, "layer": "outer", "basis": "calculator"}],
          "checks": {"_note": "x", "hs_review": ["--hs", "USB_D"]}}
    bad = [({"power": None}, "list"),
           ({"power": [{"nets": [], "current_a": 1, "width_mm": 1, "basis": "x"}]}, "nets"),
           ({"power": [{"nets": ["A"], "current_a": 0, "width_mm": 1, "basis": "x"}]}, "current_a"),
           ({"power": [{"nets": ["A"], "current_a": 1, "layer": "outer", "width_mm": 1, "basis": " "}]}, "basis"),
           ({"power": [{"nets": ["A"], "current_a": 1, "layer": "outer", "width_mm": 1, "basis": "[EXAMPLE VALUE - REPLACE]"}]}, "example"),
           ({"power": [{"nets": ["A"], "current_a": 1, "basis": "x"}]}, "layer"),
           ({"power": [{"nets": ["A"], "current_a": 2, "layer": "outer", "width_mm": 0.5, "basis": "x"}]}, "below"),
           ({"power": [], "checks": {"drc": []}}, "not recognized"),
           ({"power": [], "checks": {"plane_check": [["--layer", "In1.Cu"], [1]]}}, "several parameter sets")]
    with tempfile.TemporaryDirectory() as td:
        f = pathlib.Path(td) / "b.hz_layout.json"
        f.write_text(json.dumps(ok), encoding="utf-8")
        assert list(power_rules.load_cfg(f)["checks"]) == ["hs_review"]                 # keys starting with _ are ignored
        multi = dict(ok, checks={"plane_check": [["--layer", "In1.Cu"], ["--layer", "In4.Cu"]]})
        f.write_text(json.dumps(multi), encoding="utf-8")
        assert len(power_rules.load_cfg(f)["checks"]["plane_check"]) == 2               # several parameter sets for one check
        for cfg, key in bad:
            f.write_text(json.dumps(cfg), encoding="utf-8")
            try:
                power_rules.load_cfg(f)
                raise AssertionError(key)
            except ValueError as e:
                assert key in str(e), (key, e)
        try:
            power_rules.load_cfg(pathlib.Path(td) / "none.json")
            raise AssertionError("missing")
        except ValueError as e:
            assert "missing" in str(e)


def test_prod_gate_summarize_and_judge():
    rep = {"violations": [{"severity": "error", "type": "track_width", "description": "a"},
                          {"severity": "error", "type": "track_width", "description": "b"},
                          {"severity": "error", "type": "track_width", "description": "x (rule 'HZ_PWR_3V3' min width)"},
                          {"severity": "warning", "type": "lib_footprint_issues", "description": "c"}],
           "unconnected_items": [{}], "schematic_parity": []}
    block, warns = prod_gate.summarize_drc(rep)
    assert warns == 1 and block == ["DRC error track_width x2: a",
                                    "DRC error track_width (HZ_PWR_3V3) x1: x (rule 'HZ_PWR_3V3' min width)", "DRC unconnected x1"]
    assert prod_gate.summarize_drc({"violations": [], "unconnected_items": []}) == ([], 0)
    hz_warn = {"violations": [{"severity": "warning", "type": "hole_to_hole", "description": "holes too close (rule 'HZ_pth' min 0.45)"},
                              {"severity": "warning", "type": "silk_overlap", "description": "silkscreen overlap"}]}
    assert prod_gate.summarize_drc(hz_warn) == (["DRC error hole_to_hole (HZ_pth) x1: holes too close (rule 'HZ_pth' min 0.45)"], 1)
    assert prod_gate.judge("hs_review", 0, "7 X vias P 1 / N 1") and not prod_gate.judge("hs_review", 0, "... NOT MET")
    assert prod_gate.judge("impedance_check", 0, "Result: no FAIL items") and not prod_gate.judge("impedance_check", 1, "Result: has FAIL items")
    assert not prod_gate.judge("route_check", 1, "1 right angle: 3 NOT MET") and prod_gate.judge("plane_check", 0, "")
    assert not prod_gate.judge("fence_check", 0, "CSI_D0 ... NOT MET") and not prod_gate.judge("fence_check", 1, "Summary")
    assert prod_gate.judge("fence_check", 0, "Summary: 7 pair(s), exceeded 0 pair(s)")


def test_power_rules_ensure_base():
    base = "(version 1)\n# template\n(rule \"HZ_track_width\")\n"
    proj = "(version 1)\n(rule \"E22_body\")\n"
    out = power_rules.ensure_base(proj, base)
    assert out.count("(version 1)") == 1 and out.index("HZ_track_width") < out.index("E22_body")   # template comes before the project rules
    assert power_rules.ensure_base(out, base) == out                                                # an existing section is not added again
    assert power_rules.ensure_base("", base).startswith("(version 1)\n" + power_rules.BASE_BEGIN)
    new_base = "(version 1)\n# template v2\n(rule \"HZ_track_width\")\n(rule \"HZ_new\")\n"
    upd = power_rules.ensure_base(out, new_base)                                                     # after a template revision the whole section is replaced
    assert "HZ_new" in upd and "template v2" in upd and "# template\n" not in upd and upd.count(power_rules.BASE_BEGIN) == 1
    assert upd.index("HZ_new") < upd.index("E22_body")


def test_legacy_markers_compat():
    import json
    import tempfile
    # Legacy markers: Chinese strings below are test data for files written by earlier versions (compatibility only)
    base = "(version 1)\n# template v2\n(rule \"HZ_track_width\")\n"
    for old_begin in power_rules.LEGACY_BASE_BEGINS:
        old = "(version 1)\n" + old_begin + "\n(rule \"OLD_base\")\n" + power_rules.BASE_END + "\n\n(rule \"E22_body\")\n"
        assert power_rules.has_base(old)
        upd = power_rules.ensure_base(old, base)
        assert old_begin not in upd and "OLD_base" not in upd                          # the old section is replaced whole
        assert upd.count(power_rules.BASE_BEGIN) == 1 and upd.count(power_rules.BASE_END) == 1   # never two sections
        assert "E22_body" in upd and "template v2" in upd
    assert power_rules.has_base("(version 1)\n" + power_rules.BASE_BEGIN + "\n") and not power_rules.has_base("(version 1)\n")
    blk = power_rules.render([{"nets": ["3V3"], "current_a": 0.5, "layer": "outer", "basis": "calculator"}])
    for old_begin in power_rules.LEGACY_BEGINS:
        old = "(version 1)\n(rule \"E22_body\")\n\n" + old_begin + "\n(rule \"OLD_pwr\")\n" + power_rules.END + "\n"
        upd = power_rules.apply(old, blk)
        assert old_begin not in upd and "OLD_pwr" not in upd and upd.count(power_rules.BEGIN) == 1
        assert "E22_body" in upd and power_rules.apply(upd, blk) == upd
    # Example-value markers, current and legacy, are both rejected
    for mark in (power_rules.EXAMPLE_MARK, *power_rules.LEGACY_EXAMPLE_MARKS, "【示例数值，须替换】设计文档第 x 节"):
        with tempfile.TemporaryDirectory() as td:
            f = pathlib.Path(td) / "a.hz_layout.json"
            f.write_text(json.dumps({"power": [{"nets": ["A"], "current_a": 1, "layer": "outer", "width_mm": 1, "basis": mark}]}),
                         encoding="utf-8")
            try:
                power_rules.load_cfg(f)
                raise AssertionError("example marker accepted: " + mark)
            except ValueError as e:
                assert "example" in str(e)
    # stackup: a rerun deletes notes with either title
    assert stackup.is_note(stackup.TITLE + "\n1. x") and stackup.is_note(stackup.LEGACY_TITLE + "\n1. x")
    assert not stackup.is_note("other text")


def test_prod_gate_legacy_output_not_passing():
    # Output of older check scripts (Chinese) must never be released by mistake when scripts are mixed
    assert prod_gate.judge("hs_review", 0, "7 X 过孔 P 2 / N 1 不满足") is False
    assert prod_gate.judge("route_check", 0, "1 直角：3 不满足") is False
    assert prod_gate.judge("fence_check", 0, "CSI_D0 … 不满足") is False
    assert prod_gate.judge("impedance_check", 0, "结论：无 FAIL 项") is False
    assert prod_gate.judge("impedance_check", 0, "Result: no FAIL items\n1 … 不满足") is False
    assert prod_gate.judge("impedance_check", 0, "Result: has FAIL items") is False
    assert prod_gate.judge("impedance_check", 1, "Result: no FAIL items") is False
    assert prod_gate.judge("hs_review", 0, "9 test points on high-speed nets: 1 NOT MET TP1") is False
    assert prod_gate.judge("fence_check", 0, "x NOT MET") is False
    # kicad-cli with a zh-CN locale writes the Chinese word for "rule" in violation text
    rep = {"violations": [{"severity": "warning", "type": "hole_to_hole", "description": "孔近 (规则 'HZ_pth' 最小 0.45)"}],
           "unconnected_items": []}
    assert prod_gate.summarize_drc(rep) == (["DRC error hole_to_hole (HZ_pth) x1: 孔近 (规则 'HZ_pth' 最小 0.45)"], 0)


def test_fence_chains_and_outer_side():
    # P along y=0 (two segments given in reverse order), N below at y=-0.4
    segs = [(0, (5, 0), (10, 0), 0.13), (0, (5, 0), (0, 0), 0.13)]
    ch = fence_check.chains(segs)
    assert len(ch) == 1 and len(ch[0]) == 2 and ch[0][0][2] == ch[0][1][1]          # chained into one, end to end
    smp = fence_check.sample_chain(ch[0], LineString([(0, -0.4), (10, -0.4)]), step=0.25)
    assert len(smp) == 41 and abs(smp[-1][0] - 10) < 1e-9
    assert all(n[1] > 0.99 for _, _, n, _, _ in smp)                                # the outer side faces away from N, toward +y
    assert len(fence_check.chains(segs + [(0, (20, 0), (21, 0), 0.13)])) == 2        # non-touching segments start another chain


def test_fence_classify_runs_exempt():
    smp = fence_check.sample_chain([(0, (0, 0), (10, 0), 0.13)], LineString([(0, -0.4), (10, -0.4)]), step=0.5)
    isl = {0: [box(0, 0.5, 4, 2), box(6, 0.5, 10, 2)]}                               # two islands, no copper between 4 and 6
    keys = fence_check.classify(smp, isl, exempt=box(8.9, -1, 11, 1), probe=0.8)
    assert keys[0] == 0 and keys[10] is None and keys[14] == 1 and keys[19] == fence_check.EX
    assert fence_check.runs(smp, keys) == [(0, 0, 0, 8), (0, 1, 12, 17)]              # breaks at island change, no copper and exempt points
    assert fence_check.classify(smp, isl, probe=0.3)[0] is None                       # pour farther than probe from the trace edge does not count as guard


def test_fence_stations_side_and_radius():
    smp = fence_check.sample_chain([(0, (0, 0), (10, 0), 0.13)], LineString([(0, -0.4), (10, -0.4)]), step=0.25)
    vias = [(3.1, 1.0, 0.25, {0}), (5, -1.0, 0.25, {0}), (7, 2.5, 0.25, {0}), (8, 1.0, 0.25, {1})]
    st = fence_check.stations(smp, vias, radius=2.0)
    assert [j for _, j in st] == [0] and abs(st[0][0] - 3.1) < 1e-9   # inner side, beyond radius and other-layer vias do not count; position refined to 3.1


def test_fence_max_gap():
    g = fence_check.max_gap
    assert g(0, 10, [], 3) == (10, 0, "pour island without ground via")
    gap, _, kind = g(0, 10, [0.5, 9.5], 3)
    assert abs(gap - 9) < 1e-9 and kind == "grounded at both ends only"
    assert g(0, 10, [1, 3.5, 5.5, 7.5, 9.5], 3) == (2.5, 1, "")
    assert g(0, 10, [-1, 2, 4.5, 7, 9.5], 3)[0] == 3                  # a via just outside the run also grounds its start
    assert g(0, 10, [-5, 15], 3)[2] == "pour island without ground via"                 # a via too far from the run does not count
    assert g(0, 2, [], 3)[0] == 2                                    # short run with no via: passes if its length does not exceed the spacing


def test_fence_judge_net_island_membership():
    smp = fence_check.sample_chain([(0, (0, 0), (12, 0), 0.13)], LineString([(0, -0.4), (12, -0.4)]), step=0.25)
    isl = {0: [box(0, 0.5, 12, 2), box(0, 2.6, 12, 4)]}
    keys = fence_check.classify(smp, isl)
    # Guard copper has vias only at its two ends; another row of vias is on the outer island (y=3.3) and does not count as this guard's vias
    vias = [(0.5, 1.2, 0.25, {0}), (11.5, 1.2, 0.25, {0})] + [(x, 3.3, 0.25, {0}) for x in (3, 6, 9)]
    cov, bare, bad = fence_check.judge_net(smp, keys, fence_check.stations(smp, vias, 4.0), vias, isl, 3)
    assert abs(cov - 12.25) < 1e-6 and bare == 0
    assert len(bad) == 1 and abs(bad[0][0] - 11) < 1e-6 and bad[0][3] == "grounded at both ends only"
    vias += [(x, 1.2, 0.25, {0}) for x in (3, 6, 9)]
    assert fence_check.judge_net(smp, keys, fence_check.stations(smp, vias, 4.0), vias, isl, 3)[2] == []


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
