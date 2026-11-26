#!/usr/bin/env python3
"""Preview of the Godot HD text renderer (godot/text_screen.gd) without Godot.

Runs NFL.EXE in the reference emulator, and at each capture point renders the
text screen the way TextScreen._draw() does: the same cell grid, font sizing,
glyph placement and box-drawing rules, drawn with Pillow instead of Godot's
CanvasItem API.  Font rasterisation differs slightly from Godot's (FreeType in
both, but hinting/AA settings differ); layout is identical.

  python3 -I tools/preview_hd.py --game-dir /path/to/nfl [--size 1920x1080] [--modern]

Writes build/shots/hd/*.png.
"""
import argparse
import os
import shutil
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "refemu"))

import run as refrun                       # noqa: E402
import video                               # noqa: E402
from bios import CGA_PALETTE               # noqa: E402

FONT_MAIN = os.path.join(ROOT, "godot", "fonts", "JetBrainsMono-Medium.ttf")
FONT_FALLBACK = os.path.join(ROOT, "godot", "fonts", "DejaVuSansMono.ttf")

# --- mirrored from godot/text_screen.gd --------------------------------------
BOX = {
    0xB3: "1100", 0xB4: "1110", 0xB5: "1120", 0xB6: "2210", 0xB7: "0210",
    0xB8: "0120", 0xB9: "2220", 0xBA: "2200", 0xBB: "0220", 0xBC: "2020",
    0xBD: "2010", 0xBE: "1020", 0xBF: "0110", 0xC0: "1001", 0xC1: "1011",
    0xC2: "0111", 0xC3: "1101", 0xC4: "0011", 0xC5: "1111", 0xC6: "1102",
    0xC7: "2201", 0xC8: "2002", 0xC9: "0202", 0xCA: "2022", 0xCB: "0222",
    0xCC: "2202", 0xCD: "0022", 0xCE: "2222", 0xCF: "1022", 0xD0: "2011",
    0xD1: "0122", 0xD2: "0211", 0xD3: "2001", 0xD4: "1002", 0xD5: "0102",
    0xD6: "0201", 0xD7: "2211", 0xD8: "1122", 0xD9: "1010", 0xDA: "0101",
}
MODERN_PALETTE = [
    0x0C0F14, 0x2F5FD0, 0x2FAE4F, 0x23A9B5, 0xC8343A, 0xB04BC4, 0xC8822B, 0xC5CAD3,
    0x5B6270, 0x6EA0FF, 0x6BE08A, 0x63E3EF, 0xFF6B6B, 0xE58CFF, 0xFFE066, 0xF5F7FA]


def box_segments(arms, cx, cy, left, top, right, bottom, t, g):
    """Same rules as TextScreen.box_segments; returns (x, y, w, h) rects."""
    out = []
    ht = float(int(t * 0.5))
    for d in range(4):
        kind = arms[d]
        if kind == 0:
            continue
        vertical = d < 2
        opp = d ^ 1
        p0 = 2 if vertical else 0
        p1 = 3 if vertical else 1
        perp_doubles = int(arms[p0] == 2) + int(arms[p1] == 2)
        offsets = [0.0] if kind == 1 else [-g, g]
        for off in offsets:
            if kind == 1:
                if arms[opp] != 0:
                    stop = 0.0
                elif perp_doubles == 2:
                    stop = -g
                elif perp_doubles == 1:
                    stop = g
                else:
                    stop = 0.0
            else:
                side = p0 if off < 0.0 else p1
                if arms[side] == 2:
                    stop = -g
                elif arms[side] == 1:
                    stop = 0.0
                elif arms[opp] != 0:
                    stop = 0.0
                elif perp_doubles > 0:
                    stop = g
                else:
                    stop = 0.0
            stop += ht
            if vertical:
                x = cx + off - ht
                if d == 0:
                    out.append((x, top, t, cy + stop - top + (t - ht * 2.0)))
                else:
                    ys = cy - stop
                    out.append((x, ys, t, bottom - ys))
            else:
                y = cy + off - ht
                if d == 2:
                    out.append((left, y, cx + stop - left + (t - ht * 2.0), t))
                else:
                    xs = cx - stop
                    out.append((xs, y, right - xs, t))
    return out


class HDRenderer:
    def __init__(self, width, height, modern=False):
        self.w, self.h = width, height
        src = MODERN_PALETTE if modern else CGA_PALETTE
        self.colors = [tuple(v) if isinstance(v, tuple) else ((v >> 16) & 255, (v >> 8) & 255, v & 255) for v in src]
        self._fonts = {}

    def fonts(self, size):
        if size not in self._fonts:
            main = ImageFont.truetype(FONT_MAIN, size)
            fb = ImageFont.truetype(FONT_FALLBACK, size)
            notdef = self._raster(main, "\U0010FFFD")
            self._fonts[size] = (main, fb, notdef)
        return self._fonts[size]

    _glyph_cache = {}

    def _raster(self, font, s):
        """Glyph bitmap as bytes (to tell a real glyph from the .notdef box)."""
        key = (id(font), s)
        if key not in self._glyph_cache:
            im = Image.new("L", (int(font.size * 1.5), int(font.size * 1.5)))
            ImageDraw.Draw(im).text((0, 0), s, font=font, fill=255)
            self._glyph_cache[key] = im.tobytes()
        return self._glyph_cache[key]

    def render(self, m, blink_phase=0):
        md = m.rb(0x449)
        cols = m.rw(0x44A) or 80
        page = m.rb(0x462)
        base = (0xB0000 if md == 7 else 0xB8000) + page * m.rw(0x44C)
        blink_enabled = (m.cga_mode_reg & 0x20) != 0
        img = Image.new("RGB", (self.w, self.h), self.colors[0])
        dr = ImageDraw.Draw(img)

        def rect(x, y, w, h, c):
            if w > 0 and h > 0:
                dr.rectangle([int(x), int(y), int(x + w) - 1, int(y + h) - 1], fill=c)

        cell_h = self.h / 25.0
        cell_w = min(self.w / cols, cell_h * 0.75)
        ox = float(int((self.w - cell_w * cols) * 0.5))
        main, _, _ = self.fonts(100)
        adv = main.getlength("M") / 100.0
        fsize = max(int(min(cell_w * 0.98 / adv, cell_h * 0.95)), 6)
        main, fb, notdef = self.fonts(fsize)
        baseline_off = round(cell_h * 0.5 + fsize * 0.32)
        glyph_w = main.getlength("M")
        line_t = max(1.0, round(cell_w * 0.09))
        gap = max(line_t + 1.0, round(cell_w * 0.17))
        for row in range(25):
            y0 = round(row * cell_h)
            y1 = round((row + 1) * cell_h)
            for col in range(cols):
                x0 = ox + round(col * cell_w)
                x1 = ox + round((col + 1) * cell_w)
                a = base + (row * cols + col) * 2
                ch, at = m.cpu.mem[a], m.cpu.mem[a + 1]
                fgc, bgc = at & 15, (at >> 4) & 15
                if blink_enabled and md != 7:
                    bgc &= 7
                    if at & 0x80 and blink_phase:
                        fgc = bgc
                cw, chh = x1 - x0, y1 - y0
                if bgc:
                    rect(x0, y0, cw, chh, self.colors[bgc])
                if ch in (0, 0x20, 0xFF) or fgc == bgc:
                    continue
                fg, bg = self.colors[fgc], self.colors[bgc]
                if self.block(rect, ch, x0, y0, cw, chh, fg, bg, line_t, gap):
                    continue
                s = video.cp437_char(ch)
                f = main if self._raster(main, s) != notdef else fb
                px = x0 + int((cw - glyph_w) * 0.5)
                py = y0 + baseline_off
                dr.text((px, py), s, font=f, fill=fg, anchor="ls")
        return img

    @staticmethod
    def block(rect, ch, x0, y0, w, h, fg, bg, t, g):
        if 0xB0 <= ch <= 0xB2:
            k = [0.25, 0.5, 0.75][ch - 0xB0]
            rect(x0, y0, w, h, tuple(int(bg[i] + (fg[i] - bg[i]) * k) for i in range(3)))
            return True
        if ch == 0xDB:
            rect(x0, y0, w, h, fg)
            return True
        if ch == 0xDC:
            hh = round(h * 0.5)
            rect(x0, y0 + hh, w, h - hh, fg)
            return True
        if ch == 0xDF:
            rect(x0, y0, w, round(h * 0.5), fg)
            return True
        if ch == 0xDD:
            rect(x0, y0, round(w * 0.5), h, fg)
            return True
        if ch == 0xDE:
            hw = round(w * 0.5)
            rect(x0 + hw, y0, w - hw, h, fg)
            return True
        if ch == 0xFE:
            s = round(min(w, h) * 0.5)
            rect(x0 + round((w - s) * 0.5), y0 + round((h - s) * 0.5), s, s, fg)
            return True
        if ch not in BOX:
            return False
        arms = [int(c) for c in BOX[ch]]
        cx, cy = x0 + round(w * 0.5), y0 + round(h * 0.5)
        for (x, y, ww, hh) in box_segments(arms, cx, cy, x0, y0, x0 + w, y0 + h, t, g):
            rect(x, y, ww, hh, fg)
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", required=True)
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--modern", action="store_true")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "shots", "hd"))
    a = ap.parse_args()
    w, h = (int(v) for v in a.size.lower().split("x"))
    root = os.path.join(ROOT, "build", "run-hd")
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(a.game_dir, root)
    for n in os.listdir(root):
        if n.upper() == "CONFIG.NFL":
            os.remove(os.path.join(root, n))
    os.makedirs(a.out, exist_ok=True)
    drives = {"A": root, "B": root, "C": root, "D": os.path.join(root, "89")}
    s = refrun.Session(root, "NFL.EXE", "", drives, "C", shots=a.out, quiet=True)
    r = HDRenderer(w, h, a.modern)
    suffix = "_modern" if a.modern else ""

    def shot(name):
        p = os.path.join(a.out, "%s%s.png" % (name, suffix))
        r.render(s.m).save(p)
        print("[hd] %s" % p)
    s.shot = shot
    s.actions = refrun.parse_keys(
        "{when:CONFIGURATION}{shot:01_config}4"
        "{when:Color graphics}4{when:PC/XT}1"
        "{when:home team}{shot:02_choose_team}{Enter}"
        "{when:visiting team}{Down}{Enter}"
        "{when:Choose the coach}c{when:Right keypad}r{when:coach for the Colts}c"
        "{when:Timeouts}{wait:40}{shot:03_game_start}{wait:900}{shot:04_game}")
    s.run(250_000_000)


if __name__ == "__main__":
    main()
