import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "hubo-backpack" / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))

import idcard_image as ic  # noqa: E402
import scan_public as sp  # noqa: E402
import check_template_sync as cts  # noqa: E402
import height_check as hc  # noqa: E402


class IdcardImageTest(unittest.TestCase):
    def test_crc_known_vector(self):
        self.assertEqual(ic.crc16_ccitt_false(b"123456789"), 0x29B1)

    def test_build_layout(self):
        d = ic.build_descriptor(form_id=0, product_id=0xF001, hw_ver=0x10)
        self.assertEqual(len(d), 12)
        self.assertEqual(d[:4], b"HuBo")
        self.assertEqual(d[4], 1)
        self.assertEqual(d[6:8], b"\x01\xF0")
        self.assertEqual(struct.unpack_from("<H", d, 10)[0], ic.crc16_ccitt_false(d[:10]))
        self.assertEqual(ic.check_descriptor(d), [])

    def test_official_id_needs_flag(self):
        d = ic.build_descriptor(form_id=3, product_id=0x0001, hw_ver=0x10)
        self.assertTrue(any("DIY" in e for e in ic.check_descriptor(d)))
        self.assertEqual(ic.check_descriptor(d, allow_official=True), [])

    def test_forbidden_ids(self):
        for pid in (0x0000, 0xFFFF):
            d = ic.build_descriptor(form_id=0, product_id=pid, hw_ver=0x10)
            self.assertTrue(ic.check_descriptor(d, allow_official=True))

    def test_reserved_flag_bits(self):
        d = ic.build_descriptor(form_id=0, product_id=0xF001, hw_ver=0x10, flags=0x04)
        self.assertTrue(any("flags" in e for e in ic.check_descriptor(d)))

    def test_crc_tamper(self):
        d = bytearray(ic.build_descriptor(form_id=0, product_id=0xF001, hw_ver=0x10))
        d[5] ^= 1
        self.assertTrue(any("CRC" in e for e in ic.check_descriptor(bytes(d))))

    def test_cli_writes_16_bytes(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "id.bin"
            rc = ic.main(["--form-id", "0", "--product-id", "0xF001", "--hw-ver", "0x10", "-o", str(out)])
            self.assertEqual(rc, 0)
            data = out.read_bytes()
            self.assertEqual(len(data), 16)
            self.assertEqual(data[12:], b"\xFF" * 4)


class ScanPublicTest(unittest.TestCase):
    def _scan(self, text, extra=(), name="a.md"):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / name
            p.write_text(text, encoding="utf-8")
            return sp.scan([Path(t)], list(extra))

    def test_hits(self):
        # built by concatenation so this test file itself is not a scan hit; all sample values are fictitious
        for text in ("D:" + "/Work/project/x", "C:" + r"\Us" + r"ers\bob\x",
                     "ip 192." + "168.0.10", "ip 10." + "0.0.5", "mac 02:00:00" + ":00:00:01",
                     "mail a.b" + "@corp.example.net"):
            self.assertTrue(self._scan(text), text)

    def test_allowed(self):
        for text in ("1234567+dev" + "@users.noreply.github.com", "KiCad 10.0.6",
                     "C:" + r"\Program Files\KiCad\10.0\bin", "SPEC-003 3.3.1", "uuid 1f2e3d4c-aaaa",
                     "dev" + "@example.com"):
            self.assertEqual(self._scan(text), [], text)

    def test_extra_patterns(self):
        self.assertTrue(self._scan("approved by Alice", ["Alice"]))

    def test_step_is_scanned(self):
        # the STEP header FILE_NAME often carries an author and local paths, so it must be scanned as text
        self.assertTrue(self._scan("FILE_NAME('x','C:" + r"\Us" + r"ers\bob\a.step')", name="m.step"))

    def test_binary_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "a.png").write_bytes(("ip 192." + "168.1.1").encode())
            self.assertEqual(sp.scan([Path(t)], []), [])


class TemplateSyncTest(unittest.TestCase):
    def test_sync_then_verify(self):
        with tempfile.TemporaryDirectory() as t:
            src, dst, lock = Path(t) / "tpl", Path(t) / "copy", Path(t) / "TEMPLATE.lock"
            (src / "kicad").mkdir(parents=True)
            (src / "kicad" / "a.kicad_sch").write_text("sch", encoding="utf-8")
            (src / "LICENSE").write_text("lic", encoding="utf-8")
            (src / "README.md").write_text("not copied", encoding="utf-8")
            cts.sync(src, dst, lock, commit="abc1234")
            self.assertTrue((dst / "kicad" / "a.kicad_sch").exists())
            self.assertFalse((dst / "README.md").exists())
            self.assertEqual(cts.verify(dst, lock), [])
            (dst / "kicad" / "a.kicad_sch").write_text("changed", encoding="utf-8")
            self.assertEqual(cts.verify(dst, lock), ["changed: kicad/a.kicad_sch"])

    def test_checkout_noise_ignored(self):
        # git autocrlf on Windows converts line endings to CRLF; KiCad generates .kicad_prl after opening a project. Neither counts as the copy being modified
        with tempfile.TemporaryDirectory() as t:
            src, dst, lock = Path(t) / "tpl", Path(t) / "copy", Path(t) / "TEMPLATE.lock"
            (src / "kicad").mkdir(parents=True)
            (src / "kicad" / "a.kicad_sch").write_bytes(b"line1\nline2\n")
            (src / "LICENSE").write_bytes(b"lic\n")
            cts.sync(src, dst, lock, commit="abc1234")
            (dst / "kicad" / "a.kicad_sch").write_bytes(b"line1\r\nline2\r\n")
            (dst / "kicad" / "a.kicad_prl").write_text("{}", encoding="utf-8")
            self.assertEqual(cts.verify(dst, lock), [])


# KiCad 10 style: model sub-items span several lines
FP = '''(footprint "Lib:C_1206"
	(layer "{layer}")
	(property "Reference" "{ref}" (at 0 0 0) (layer "F.SilkS"))
	(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask"))
	(model "${{KIPRJMOD}}/m.step"
		(offset
			(xyz 0 0 {oz})
		)
		(scale
			(xyz 1 1 1)
		)
		(rotate
			(xyz 0 0 0)
		)
	)
)'''


class HeightCheckTest(unittest.TestCase):
    def test_parse_and_judge(self):
        pcb = "(kicad_pcb\n" + FP.format(layer="F.Cu", ref="C1", oz=0) + "\n" + \
              FP.format(layer="B.Cu", ref="C2", oz=0) + "\n" + FP.format(layer="B.Cu", ref="H1", oz=0) + "\n)"
        fps = hc.footprints(pcb)
        self.assertEqual([f["ref"] for f in fps], ["C1", "C2", "H1"])
        self.assertEqual(fps[0]["side"], "top")
        self.assertEqual(fps[1]["side"], "bottom")
        self.assertEqual(fps[0]["model"], "${KIPRJMOD}/m.step")
        limits = {"top_height_mm": 3.9, "bottom_height_mm_excl_header": 1.7}
        self.assertEqual(hc.judge(fps[0], 2.0, limits, "H1"), "OK")
        self.assertEqual(hc.judge(fps[1], 2.0, limits, "H1"), "FAIL")
        self.assertEqual(hc.judge(fps[2], 9.0, limits, "H1"), "SKIP")
        self.assertEqual(hc.judge(fps[0], None, limits, "H1"), "UNCHECKED")


if __name__ == "__main__":
    unittest.main()
