#!/usr/bin/env python3
"""Generate and self-check the 0x57 ID-card descriptor image for Hubo-Tack 30-pin backpacks.

DIY backpacks MUST use the programmed card at I2C 0x57 with product_id in 0xF000-0xFFFE
(for personal use, global uniqueness is not guaranteed).

Usage:
  python idcard_image.py --form-id 0 --product-id 0xF001 --hw-ver 0x10 -o idcard.bin
Writing: write the 16-byte image to AT24C02 offset 0x00; pull the WP test point low first to lift write protect.
"""
import argparse
import struct
import sys

SIGNATURE = b"HuBo"
SPEC_VER = 0x01
DESC_LEN = 12
REGION_LEN = 16  # 0x00-0x0F descriptor region: 12-byte descriptor + 4 reserved bytes
DIY_FIRST, DIY_LAST = 0xF000, 0xFFFE
FORBIDDEN_IDS = (0x0000, 0xFFFF)


def crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: polynomial 0x1021, initial value 0xFFFF (same as the host firmware)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def build_descriptor(form_id, product_id, hw_ver, flags=0, spec_ver=SPEC_VER) -> bytes:
    body = SIGNATURE + bytes([spec_ver, form_id]) + struct.pack("<H", product_id) + bytes([hw_ver, flags])
    return body + struct.pack("<H", crc16_ccitt_false(body))


def check_descriptor(desc: bytes, allow_official=False) -> list:
    """Return a list of problems; an empty list means pass."""
    if len(desc) != DESC_LEN:
        return [f"length {len(desc)} != {DESC_LEN}"]
    errs = []
    if desc[:4] != SIGNATURE:
        errs.append("signature must be 'HuBo'")
    if desc[4] < 1:
        errs.append("spec_ver must be >= 1")
    pid = struct.unpack_from("<H", desc, 6)[0]
    if pid in FORBIDDEN_IDS:
        errs.append(f"product_id 0x{pid:04X} is forbidden")
    elif not allow_official and not DIY_FIRST <= pid <= DIY_LAST:
        errs.append(f"product_id 0x{pid:04X} outside DIY range 0xF000-0xFFFE (use --official for an assigned id)")
    if desc[9] & 0xFC:
        errs.append("flags bit2-7 must be 0")
    if struct.unpack_from("<H", desc, 10)[0] != crc16_ccitt_false(desc[:10]):
        errs.append("CRC mismatch")
    return errs


def region_image(desc: bytes) -> bytes:
    return desc + b"\xFF" * (REGION_LEN - DESC_LEN)


def _int(s):
    return int(s, 0)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form-id", type=_int, required=True, help="0=no multiplexed zone 1=SPI 2=I2S 3=LoRa 4=parallel input 5=SPI slave+UART+ready handshake")
    ap.add_argument("--product-id", type=_int, required=True)
    ap.add_argument("--hw-ver", type=_int, required=True, help="high nibble = major, low nibble = minor version; V1.0 = 0x10")
    ap.add_argument("--flags", type=_int, default=0, help="bit0 ESP_INT polarity (0 = active low), bit1 PACK_PEN")
    ap.add_argument("--official", action="store_true", help="product_id is officially assigned (outside the DIY range)")
    ap.add_argument("-o", "--out", required=True, help="output file for the 16-byte image")
    a = ap.parse_args(argv)
    for name, v in (("form-id", a.form_id), ("hw-ver", a.hw_ver), ("flags", a.flags)):
        if not 0 <= v <= 0xFF:
            ap.error(f"{name} must be 0..255")
    if not 0 <= a.product_id <= 0xFFFF:
        ap.error("product-id must be 0..0xFFFF")
    desc = build_descriptor(a.form_id, a.product_id, a.hw_ver, a.flags)
    errs = check_descriptor(desc, allow_official=a.official)
    if errs:
        print("[FAIL] " + "; ".join(errs))
        return 1
    with open(a.out, "wb") as f:
        f.write(region_image(desc))
    print(f"[OK] descriptor: {desc.hex(' ')}")
    print(f"[OK] written: {a.out}  (I2C 0x57, offset 0x00; pull WP low first)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
