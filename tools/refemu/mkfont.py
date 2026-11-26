#!/usr/bin/env python3
"""Generate the CP437 bitmap fonts used by the emulator (8x8 and 8x16).

Glyphs 0x20-0x7E, accented letters and symbols are rasterized from DejaVu Sans
Mono (Bitstream Vera license + public-domain DejaVu changes, see
docs/FONT_LICENSE.txt).  Box drawing, block and shade characters (0xB0-0xDF and
a few others) are generated procedurally so that they tile seamlessly.

Outputs:
  tools/refemu/font_cp437.py   FONT8 (2048 bytes) and FONT16 (4096 bytes)
  godot/emu/font_cp437.gd      the same data as PackedByteArray constants

Usage: python3 tools/refemu/mkfont.py [path/to/DejaVuSansMono.ttf]
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TTF = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

CP437_LOW = ("\u0000☺☻♥♦♣♠•◘○◙♂♀♪♫☼►◄↕‼¶§▬↨↑↓→←∟↔▲▼")


def cp437_char(i):
    if i < 32:
        return CP437_LOW[i]
    if i == 127:
        return "⌂"
    return bytes([i]).decode("cp437")


# Box drawing description: for each code, (up, down, left, right) line weights
# 0 = none, 1 = single, 2 = double.
BOX = {
    0xB3: (1, 1, 0, 0), 0xB4: (1, 1, 1, 0), 0xB5: (1, 1, 2, 0), 0xB6: (2, 2, 1, 0),
    0xB7: (0, 2, 1, 0), 0xB8: (0, 1, 2, 0), 0xB9: (2, 2, 2, 0), 0xBA: (2, 2, 0, 0),
    0xBB: (0, 2, 2, 0), 0xBC: (2, 0, 2, 0), 0xBD: (2, 0, 1, 0), 0xBE: (1, 0, 2, 0),
    0xBF: (0, 1, 1, 0), 0xC0: (1, 0, 0, 1), 0xC1: (1, 0, 1, 1), 0xC2: (0, 1, 1, 1),
    0xC3: (1, 1, 0, 1), 0xC4: (0, 0, 1, 1), 0xC5: (1, 1, 1, 1), 0xC6: (1, 1, 0, 2),
    0xC7: (2, 2, 0, 1), 0xC8: (2, 0, 0, 2), 0xC9: (0, 2, 0, 2), 0xCA: (2, 0, 2, 2),
    0xCB: (0, 2, 2, 2), 0xCC: (2, 2, 0, 2), 0xCD: (0, 0, 2, 2), 0xCE: (2, 2, 2, 2),
    0xCF: (1, 0, 2, 2), 0xD0: (2, 0, 1, 1), 0xD1: (0, 1, 2, 2), 0xD2: (0, 2, 1, 1),
    0xD3: (2, 0, 0, 1), 0xD4: (1, 0, 0, 2), 0xD5: (0, 1, 0, 2), 0xD6: (0, 2, 0, 1),
    0xD7: (2, 2, 1, 1), 0xD8: (1, 1, 2, 2), 0xD9: (1, 0, 1, 0), 0xDA: (0, 1, 0, 1),
}


def box_glyph(code, w, h):
    up, down, left, right = BOX[code]
    g = [[0] * w for _ in range(h)]
    cx, cy = 3, h // 2 - 1          # centre column / row (matches IBM layout)
    vdouble = up == 2 or down == 2
    hdouble = left == 2 or right == 2
    vx = [cx - 1, cx + 1] if vdouble else [cx]
    hy = [cy - 1, cy + 1] if hdouble else [cy]

    def vline(x, y0, y1):
        for y in range(max(0, y0), min(h, y1 + 1)):
            g[y][x] = 1

    def hline(y, x0, x1):
        for x in range(max(0, x0), min(w, x1 + 1)):
            g[y][x] = 1

    # vertical strokes
    for i, x in enumerate(vx):
        if up:
            end = cy
            if hdouble and vdouble:
                # inner strokes stop at the inner horizontal line
                end = hy[0] if (i == 0 and left) or (i == 1 and right) else hy[-1]
            elif hdouble:
                end = hy[-1] if (left or right) and not down else hy[0]
                end = hy[-1] if not down else end
            vline(x, 0, end)
        if down:
            start = cy
            if hdouble and vdouble:
                start = hy[-1] if (i == 0 and left) or (i == 1 and right) else hy[0]
            elif hdouble:
                start = hy[0] if not up else hy[0]
            vline(x, start, h - 1)
    # horizontal strokes
    for i, y in enumerate(hy):
        if left:
            end = cx
            if hdouble and vdouble:
                end = vx[0] if (i == 0 and up) or (i == 1 and down) else vx[-1]
            elif vdouble:
                end = vx[-1]
            hline(y, 0, end)
        if right:
            start = cx
            if hdouble and vdouble:
                start = vx[-1] if (i == 0 and up) or (i == 1 and down) else vx[0]
            elif vdouble:
                start = vx[0]
            hline(y, start, w - 1)
    if not up and not down and vdouble is False and hdouble and (left or right):
        pass
    return g


def special_glyph(code, w, h):
    g = [[0] * w for _ in range(h)]
    if code in BOX:
        return box_glyph(code, w, h)
    if code in (0xB0, 0xB1, 0xB2):
        for y in range(h):
            for x in range(w):
                if code == 0xB0:
                    g[y][x] = 1 if (x % 4 == (y % 2) * 2 and y % 2 == 0) or (x % 4 == 2 and y % 4 == 1) else 0
                    g[y][x] = 1 if (x + 2 * (y % 2)) % 4 == 0 else 0
                elif code == 0xB1:
                    g[y][x] = 1 if (x + y) % 2 == 0 else 0
                else:
                    g[y][x] = 0 if (x + 2 * (y % 2)) % 4 == 0 else 1
        return g
    if code == 0xDB:
        return [[1] * w for _ in range(h)]
    if code == 0xDC:
        return [[1 if y >= h // 2 else 0] * w for y in range(h)]
    if code == 0xDF:
        return [[1 if y < h // 2 else 0] * w for y in range(h)]
    if code == 0xDD:
        return [[1 if x < w // 2 else 0 for x in range(w)] for _ in range(h)]
    if code == 0xDE:
        return [[1 if x >= w // 2 else 0 for x in range(w)] for _ in range(h)]
    if code == 0xFE:
        for y in range(h // 4, h - h // 4):
            for x in range(1, w - 2):
                g[y][x] = 1
        return g
    if code == 0x08 or code == 0x0A:      # inverse bullet / inverse circle
        return None
    return None


def raster(font, ch, w, h, size, yoff):
    img = Image.new("L", (w * 4, h * 4), 0)
    d = ImageDraw.Draw(img)
    d.text((0, yoff * 4), ch, font=font, fill=255)
    small = img.resize((w, h), Image.BOX)
    px = small.load()
    return [[1 if px[x, y] >= 100 else 0 for x in range(w)] for y in range(h)]


def build(w, h, ttf):
    size = {8: 9, 16: 15}[h]
    yoff = {8: -1.2, 16: -0.5}[h]
    font = ImageFont.truetype(ttf, size * 4)
    out = bytearray()
    for code in range(256):
        g = special_glyph(code, w, h)
        if g is None:
            ch = cp437_char(code)
            if code == 0 or code == 0x20 or code == 0xFF:
                g = [[0] * w for _ in range(h)]
            else:
                g = raster(font, ch, w, h, size, yoff)
                if code in (0x08, 0x0A):
                    g = [[1 - v for v in row] for row in raster(font, "•" if code == 8 else "○", w, h, size, yoff)]
        for row in g:
            b = 0
            for x in range(w):
                b = (b << 1) | (row[x] & 1)
            out.append(b)
    return bytes(out)


def main():
    ttf = sys.argv[1] if len(sys.argv) > 1 else TTF
    f8 = build(8, 8, ttf)
    f16 = build(8, 16, ttf)
    with open(os.path.join(HERE, "font_cp437.py"), "w") as fp:
        fp.write('"""Generated by mkfont.py from DejaVu Sans Mono; see docs/FONT_LICENSE.txt."""\n')
        fp.write("FONT8 = bytes.fromhex(\n")
        for i in range(0, len(f8), 32):
            fp.write('    "%s"\n' % f8[i:i + 32].hex())
        fp.write(")\nFONT16 = bytes.fromhex(\n")
        for i in range(0, len(f16), 32):
            fp.write('    "%s"\n' % f16[i:i + 32].hex())
        fp.write(")\n")
    gd = os.path.join(ROOT, "godot", "emu", "font_cp437.gd")
    os.makedirs(os.path.dirname(gd), exist_ok=True)
    with open(gd, "w") as fp:
        fp.write("# Generated by tools/refemu/mkfont.py from DejaVu Sans Mono.\n")
        fp.write("# License: see docs/FONT_LICENSE.txt (Bitstream Vera license).\n")
        fp.write("class_name FontCP437\nextends RefCounted\n\n")
        fp.write('const FONT8_HEX: String = "%s"\n' % f8.hex())
        fp.write('const FONT16_HEX: String = "%s"\n\n' % f16.hex())
        fp.write("static func font8() -> PackedByteArray:\n\treturn FONT8_HEX.hex_decode()\n\n")
        fp.write("static func font16() -> PackedByteArray:\n\treturn FONT16_HEX.hex_decode()\n")
    print("wrote", len(f8), len(f16))


if __name__ == "__main__":
    main()
