"""Translate the Modern-UI GDScript (godot/ui/*.gd) to Python with
tools/gdport/gd2py.py --ui and import it, so the shipped layout code runs
headless (tests and SVG previews).  Godot-only scripts (the ones that draw
with CanvasItem or rasterise SVG) are listed in GODOT_ONLY and not translated.
"""
import hashlib
import importlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
GDPORT = os.path.join(ROOT, "tools", "gdport")
OUT = os.path.join(ROOT, "build", "gdpy")
UI = os.path.join(ROOT, "godot", "ui")
sys.path.insert(0, GDPORT)
sys.path.insert(0, OUT)

GODOT_ONLY = {"modern_view.gd"}
EXTRA = [os.path.join(ROOT, "godot", "host", "video.gd")]


def sources():
    ui = sorted(os.path.join(UI, f) for f in os.listdir(UI) if f.endswith(".gd") and f not in GODOT_ONLY)
    return EXTRA + ui


def load(log=print):
    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, "ui_mod.py")
    srcs = sources()
    stamp = hashlib.sha1()
    for p in srcs + [os.path.join(GDPORT, "gd2py.py")]:
        stamp.update(open(p, "rb").read())
    sp = dst + ".sha1"
    if not (os.path.exists(dst) and os.path.exists(sp) and open(sp).read() == stamp.hexdigest()):
        subprocess.run([sys.executable, os.path.join(GDPORT, "gd2py.py"), "--ui", dst] + srcs, check=True)
        open(sp, "w").write(stamp.hexdigest())
        log("[gd2py --ui] %d files -> %s" % (len(srcs), dst))
    import gdrt
    gdrt.VFS["res://"] = os.path.join(ROOT, "godot")
    if "ui_mod" in sys.modules:
        return importlib.reload(sys.modules["ui_mod"])
    return importlib.import_module("ui_mod")
