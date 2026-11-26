"""Screen rendering: text modes (CP437 + attributes) and CGA modes 4/5/6.

``render_rgb`` returns (width, height, bytes) RGB; ``write_png`` stores it with
zlib/struct only; ``text_dump`` returns the text screen as a str (CP437 decoded).
"""
import struct
import zlib

from bios import CGA_PALETTE, BDA_MODE, BDA_COLS, BDA_PAGE, BDA_PAGESIZE, BDA_CURPOS

# CGA 320x200 palettes: (palette, intensity) -> colours 1..3
CGA4 = {
    (0, 0): (2, 4, 6), (0, 1): (10, 12, 14),
    (1, 0): (3, 5, 7), (1, 1): (11, 13, 15),
}


CP437_LOW = " ☺☻♥♦♣♠•◘○◙♂♀♪♫☼►◄↕‼¶§▬↨↑↓→←∟↔▲▼"


def cp437_char(c):
    """CP437 glyph as text: control codes as their PC glyphs, 0xFF as a space."""
    if c < 32:
        return CP437_LOW[c]
    if c == 127:
        return "⌂"
    if c == 255:
        return " "
    return bytes([c]).decode("cp437")


CP437_TABLE = [cp437_char(c) for c in range(256)]


def video_state(m):
    mode = m.rb(BDA_MODE)
    return mode, m.rw(BDA_COLS) or 80, m.rb(BDA_PAGE), m.rw(BDA_PAGESIZE)


def text_dump(m, page=None):
    mode, cols, cur_page, psize = video_state(m)
    if mode in (4, 5, 6):
        return "[graphics mode %d]" % mode
    if page is None:
        page = cur_page
    base = (0xB0000 if mode == 7 else 0xB8000) + page * psize
    lines = []
    for row in range(25):
        a = base + row * cols * 2
        lines.append("".join(CP437_TABLE[m.mem[a + 2 * c]] for c in range(cols)).rstrip(" "))
    return "\n".join(lines)


def render_rgb(m, font8, font16=None, blink_phase=0):
    """Return (w, h, rgb bytes). Text: 8x16 cells if font16 given, else 8x8."""
    mode, cols, page, psize = video_state(m)
    mem = m.mem
    pal = [bytes(c) for c in CGA_PALETTE]
    if mode in (4, 5):
        creg = m.cga_color_reg
        bg = pal[creg & 0x0F]
        sel = 1 if (creg & 0x20) else 0
        inten = 1 if (creg & 0x10) else 0
        if mode == 5:
            cols3 = (3, 4, 7) if not inten else (11, 12, 15)
        else:
            cols3 = CGA4[(sel, inten)]
        lut = [bg, pal[cols3[0]], pal[cols3[1]], pal[cols3[2]]]
        out = bytearray()
        for y in range(200):
            a = 0xB8000 + (y & 1) * 0x2000 + (y >> 1) * 80
            for xb in range(80):
                b = mem[a + xb]
                out += lut[b >> 6] + lut[(b >> 4) & 3] + lut[(b >> 2) & 3] + lut[b & 3]
        return 320, 200, bytes(out)
    if mode == 6:
        fg = pal[m.cga_color_reg & 0x0F]
        if (m.cga_color_reg & 0x0F) == 0:
            fg = pal[15]
        bg = pal[0]
        out = bytearray()
        for y in range(200):
            a = 0xB8000 + (y & 1) * 0x2000 + (y >> 1) * 80
            for xb in range(80):
                b = mem[a + xb]
                for bit in range(7, -1, -1):
                    out += fg if (b >> bit) & 1 else bg
        return 640, 200, bytes(out)
    # text
    font = font16 if font16 is not None else font8
    ch_h = 16 if font16 is not None else 8
    base = (0xB0000 if mode == 7 else 0xB8000) + page * psize
    blink_enabled = bool(m.cga_mode_reg & 0x20)
    width = cols * 8
    height = 25 * ch_h
    out = bytearray(width * height * 3)
    cur = m.rw(BDA_CURPOS + page * 2)
    crow, ccol = cur >> 8, cur & 0xFF
    for row in range(25):
        for col in range(cols):
            a = base + (row * cols + col) * 2
            ch = mem[a]
            at = mem[a + 1]
            if mode == 7:
                fgc = 15 if at & 0x08 else 7
                bgc = 0
                if (at & 0x77) == 0x70:
                    fgc, bgc = 0, 7
                elif (at & 0x07) == 0:
                    fgc = 0
            else:
                fgc = at & 0x0F
                bgc = (at >> 4) & 0x0F
                if blink_enabled:
                    bgc &= 7
                    if at & 0x80 and blink_phase:
                        fgc = bgc
            fg = pal[fgc]
            bg = pal[bgc]
            for y in range(ch_h):
                bits = font[ch * ch_h + y]
                if row == crow and col == ccol and y >= ch_h - 2 and blink_phase == 0:
                    bits = 0xFF
                o = ((row * ch_h + y) * width + col * 8) * 3
                for x in range(8):
                    c = fg if bits & (0x80 >> x) else bg
                    out[o:o + 3] = c
                    o += 3
    return width, height, bytes(out)


def write_png(path, w, h, rgb, yscale=1, xscale=1):
    raw = bytearray()
    stride = w * 3
    for y in range(h):
        line = rgb[y * stride:(y + 1) * stride]
        if xscale > 1:
            line = b"".join(line[x * 3:x * 3 + 3] * xscale for x in range(w))
        row = b"\x00" + line
        for _ in range(yscale):
            raw += row
    h2 = h * yscale

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w * xscale, h2, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")
    with open(path, "wb") as fp:
        fp.write(png)


def screenshot(m, path, font8, font16=None):
    w, h, rgb = render_rgb(m, font8, font16)
    yscale = 2 if h == 200 else 1
    xscale = 2 if w == 320 else 1
    write_png(path, w, h, rgb, yscale, xscale)
    return path
