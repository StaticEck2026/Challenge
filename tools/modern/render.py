#!/usr/bin/env python3
"""Render Modern-UI frames from captured game states, without Godot.

The shipped layout code (godot/ui/*.gd) is translated to Python with
tools/gdport/gd2py.py --ui (uimod.py) and run on real states captured by
capture.py: GameState.update() reads the snapshot, Screens.build() lays the
frame out into a DrawList, DrawList.to_svg() writes an SVG document, and
rasterize.js renders it with headless Chromium at 1920x1080.  Text widths are
measured with FreeType on the same font files Godot uses (godot/fonts).

  python3 -I tools/modern/render.py [state ...]       # default: every state
  -> build/shots/modern/<state>.svg and .png
"""
import argparse
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import capture  # noqa: E402
import uimod  # noqa: E402

ROOT = uimod.ROOT
OUT = os.path.join(ROOT, "build", "shots", "modern")
FONTS = os.path.join(ROOT, "godot", "fonts")


class Measurer:
    """Text width in px with the bundled fonts (FreeType via Pillow)."""

    def __init__(self):
        from PIL import ImageFont
        self.fonts = {
            (False, False): ImageFont.truetype(os.path.join(FONTS, "Inter-Medium.otf"), 200),
            (True, False): ImageFont.truetype(os.path.join(FONTS, "Inter-Bold.otf"), 200),
            (False, True): ImageFont.truetype(os.path.join(FONTS, "JetBrainsMono-Medium.ttf"), 200),
            (True, True): ImageFont.truetype(os.path.join(FONTS, "JetBrainsMono-Medium.ttf"), 200),
        }
        self.cache = {}

    def __call__(self, text, size, bold=False, mono=False):
        k = (text, bool(bold), bool(mono))
        w = self.cache.get(k)
        if w is None:
            w = self.fonts[(bool(bold), bool(mono))].getlength(text) / 200.0
            self.cache[k] = w
        return w * size


def ui_defaults():
    return {"view_label": "Modern", "speed_label": "1x", "paused": False, "sound": True, "subtitle": ""}


def make(ui):
    import gdrt
    art = ui.UiArt()
    screens = ui.Screens(art)
    dl = ui.DrawList()
    dl.measurer = gdrt.Callable(Measurer())
    return art, screens, dl


def scenario_of(name):
    return {0: "boot", 1: "boot", 2: "boot", 3: "human", 4: "full", 5: "alt"}.get(int(name[:2]) // 10, name)


def previous_text_state(name):
    """The last text-mode state captured before `name` in the same run: the
    app's GameState has seen it (scoreboard, play text) before a play is drawn."""
    best = None
    for p in sorted(glob.glob(os.path.join(capture.OUT, "*.json"))):
        n = os.path.basename(p)[:-5]
        if n >= name:
            break
        if scenario_of(n) == scenario_of(name) and capture.load(n)[1]["mode"] == 3:
            best = n
    return best


def render_state(ui, name, art, screens, dl, extra=None, out_name=None):
    mem, meta = capture.load(name)
    gs = ui.GameState()
    if meta["mode"] != 3:
        prev = previous_text_state(name)
        if prev:
            gs.update(capture.load(prev)[0])
    gs.update(mem)
    u = ui_defaults()
    if extra:
        u.update(extra)
    sid = screens.build(dl, gs, u)
    svg = dl.to_svg(art, list(ui.UiTheme.TEXT_PALETTE))
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, (out_name or name) + ".svg")
    with open(path, "w") as f:
        f.write(svg)
    return path, sid, gs


def rasterize(paths):
    if paths:
        subprocess.run(["node", os.path.join(HERE, "rasterize.js"), OUT] + paths, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("states", nargs="*")
    ap.add_argument("--no-png", action="store_true")
    a = ap.parse_args()
    ui = uimod.load()
    art, screens, dl = make(ui)
    names = a.states or sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(capture.OUT, "*.bin")))
    paths = []
    for n in names:
        path, sid, gs = render_state(ui, n, art, screens, dl)
        extra = ""
        if sid == "play":
            extra = " declutter max %.2f yd" % screens.match_view.last_declutter_yards
        print("[svg] %-22s screen=%-9s ops=%4d hotspots=%2d%s" % (n, sid, len(dl.ops), len(dl.hotspots), extra))
        paths.append(path)
        if sid == "play" and n.endswith("_mid"):
            # the same frame with the toolbar's camera "Zoom" on, centred on the ball
            cam = [gs.ball["fx"], gs.ball["fy"]]
            path, sid, gs = render_state(ui, n, art, screens, dl, {"zoom": True, "camera": cam}, n + "_zoom")
            print("[svg] %-22s screen=%-9s (zoom) declutter max %.2f yd"
                  % (n + "_zoom", sid, screens.match_view.last_declutter_yards))
            paths.append(path)
    if not a.no_png:
        rasterize(paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
