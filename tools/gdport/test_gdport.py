#!/usr/bin/env python3
"""Test the GDScript port without Godot.

1. Transpile the legacy interpreter port (tools/gdport/legacy_emu/*.gd, formerly godot/emu/) to build/gdpy/gdemu.py (tools/gdport/gd2py.py).
2. Run the CPU unit tests (tools/refemu/test_cpu.py) against the transpiled
   GDScript CPU.
3. Differential run: boot NFL.EXE in the transpiled GDScript machine
   (Machine + BIOS + DOS + Video, file I/O through the FileAccess/DirAccess
   shim) and in the Python oracle side by side, feed both the same keys at the
   same emulated time, and compare CPU registers/flags every chunk and the
   whole 1 MB of memory at checkpoints.

  python3 -I tools/gdport/test_gdport.py --game-dir /path/to/nfl [--minstr 60]
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "build", "gdpy")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "refemu"))
sys.path.insert(0, OUT)

GD_FILES = ["cpu.gd", "machine.gd", "bios.gd", "dos.gd", "video.gd", "font_cp437.gd"]


def transpile():
    os.makedirs(OUT, exist_ok=True)
    subprocess.run([sys.executable, os.path.join(HERE, "gd2py.py"), os.path.join(OUT, "gdemu.py")]
                   + [os.path.join(HERE, "legacy_emu", f) for f in GD_FILES], check=True)
    import gdemu
    return gdemu


class FakeMachine:
    """Just enough of Machine for CPU unit tests."""
    trace_ints = False

    def __init__(self, hle):
        self.hle = hle
        self.pic = None

    def on_unknown(self, *a):
        pass

    def port_in(self, port, w):
        return 0xFF

    def port_out(self, port, v, w):
        pass


def cpu_tests(gdemu):
    import test_cpu

    def factory(hle):
        cpu = gdemu.CPU()
        cpu.machine = FakeMachine(hle)
        return cpu
    test_cpu.CPU_FACTORY[0] = factory
    return test_cpu.main()


def state(cpu):
    return (tuple(cpu.r), tuple(cpu.sr), cpu.ip, cpu.cf, cpu.pf, cpu.af, cpu.zf, cpu.sf,
            cpu.iflag, cpu.df, cpu.of, cpu.icount)


def compare_render(gdemu, s, gm):
    """GDScript Video.render() vs oracle render_rgb() (8x16 text / CGA), blink phase 1."""
    import video as oracle_video
    from font_cp437 import FONT8, FONT16
    w, h, rgb = oracle_video.render_rgb(s.m, FONT8, FONT16, 1)
    gv = gdemu.Video()
    gv.render(gm, 1)
    if (gv.width, gv.height) != (w, h) or bytes(gv.rgb) != rgb:
        diff = sum(1 for i in range(min(len(rgb), len(gv.rgb))) if rgb[i] != gv.rgb[i])
        print("RENDER MISMATCH: oracle %dx%d, gd %dx%d, %d differing bytes" % (w, h, gv.width, gv.height, diff))
        return False
    return True


def realtime_smoke(gdemu, game_dir):
    """Drive the GDScript machine like main.gd does in realtime mode (ticks from
    'wall clock', run(4000) until idle) and check it reaches the first screen."""
    from font_cp437 import FONT8
    root = os.path.join(ROOT, "build", "gdport-run", "rt")
    if os.path.exists(root):
        shutil.rmtree(root)
    shutil.copytree(game_dir, root, ignore=shutil.ignore_patterns("*.whl"))
    gm = gdemu.Machine(20000)
    gm.log_msg = lambda m: None
    gm.realtime = True
    gb = gdemu.BIOS(gm, bytearray(FONT8), "", gdemu.BIOS.DEFAULT_EPOCH, 12 * 3600)
    pair = [root, ""]
    gdos = gdemu.DOS(gm, gb, {"A": pair, "B": pair, "C": pair, "D": [os.path.join(root, "89"), ""]}, "C")
    gdos.load_program("C:NFL.EXE", "")
    video = gdemu.Video()
    frames = 0
    idle_frames = 0
    for frame in range(20000):
        if frame % 3 == 0:          # ~18.2 Hz at 60 fps
            gm.tick()
        budget = 0
        while budget < 60000 and not gm.exited:
            before = gm.cpu.icount
            gm.pump_keyboard()
            gm.run(4000)
            if gm.cpu.icount == before:
                idle_frames += 1
                break
            budget += gm.cpu.icount - before
        video.render(gm, frame // 16 & 1)
        frames += 1
        if "CONFIGURATION" in gdemu.Video.text_dump(gm):
            print("[realtime] CONFIGURATION screen after %d frames (%d ticks, %d instr, %d idle frames); "
                  "Video.render %dx%d" % (frames, gm.ticks, gm.cpu.icount, idle_frames, video.width, video.height))
            return True
    print("[realtime] did not reach the first screen")
    return False


def differential(gdemu, game_dir, minstr, script, tail="", season_file=None):
    import run as oracle_run
    from font_cp437 import FONT8
    work = os.path.join(ROOT, "build", "gdport-run")
    for side in ("py", "gd"):
        d = os.path.join(work, side)
        if os.path.exists(d):
            shutil.rmtree(d)
        shutil.copytree(game_dir, d, ignore=shutil.ignore_patterns("*.whl"))
        if season_file:
            shutil.copy(season_file, os.path.join(d, "89", "SEASON.NFL"))
    py_root = os.path.join(work, "py")
    gd_root = os.path.join(work, "gd")
    logs = []
    # --- oracle
    s = oracle_run.Session(py_root, "NFL.EXE", tail, {"A": py_root, "B": py_root, "C": py_root,
                                                    "D": os.path.join(py_root, "89")},
                           log=lambda m: None, quiet=True)
    s.m.tick_hooks.remove(s.on_tick)
    # --- GDScript port (transpiled)
    gm = gdemu.Machine(20000)
    gm.log_msg = lambda msg: logs.append(msg)
    gb = gdemu.BIOS(gm, bytearray(FONT8), "", gdemu.BIOS.DEFAULT_EPOCH, 12 * 3600)
    pair = [gd_root, ""]
    gdos = gdemu.DOS(gm, gb, {"A": pair, "B": pair, "C": pair, "D": [os.path.join(gd_root, "89"), ""]}, "C")
    info = gdos.load_program("C:NFL.EXE", tail)
    print("[gd] loaded:", info)
    actions = oracle_run.parse_keys(script)
    ai = 0
    chunk = 1000
    total = int(minstr * 1e6)
    t0 = time.time()
    last_ticks = -1
    while s.m.cpu.icount < total and not s.m.exited:
        # inject keys identically (only at tick boundaries)
        if s.m.ticks != last_ticks:
            last_ticks = s.m.ticks
            if ai < len(actions):
                kind, v = actions[ai]
                if kind == "when":
                    if s.m.ticks % 4 == 0 and v in s.text():
                        gtext = gdemu.Video.text_dump(gm)
                        if gtext != s.text():
                            print("SCREEN MISMATCH at tick %d" % s.m.ticks)
                            print(s.text())
                            print("----- gd -----")
                            print(gtext)
                            return False
                        if not compare_render(gdemu, s, gm):
                            return False
                        print("[when] %r at tick %d: screen text and rendered pixels identical" % (v, s.m.ticks))
                        ai += 1
                elif kind == "key":
                    if not s.m.kbd_queue and s.m.bios_kbd_empty():
                        s.m.key_scancodes(v)
                        gm.key_scancodes(list(v))
                        ai += 1
                else:
                    ai += 1
        s.m.run(chunk)
        gm.run(chunk)
        a, b = state(s.m.cpu), state(gm.cpu)
        if a != b:
            print("STATE MISMATCH after %d instructions" % s.m.cpu.icount)
            print(" oracle:", a)
            print(" gdport:", b)
            print(" oracle last op at %04X:%04X, gd last op at %04X:%04X" % (
                s.m.cpu.op_cs, s.m.cpu.op_ip, gm.cpu.op_cs, gm.cpu.op_ip))
            return False
        if s.m.cpu.icount % 5_000_000 < chunk:
            hp = hashlib.sha1(bytes(s.m.mem[:0x100000])).hexdigest()
            hg = hashlib.sha1(bytes(gm.cpu.mem[:0x100000])).hexdigest()
            dt = time.time() - t0
            print("[check] %5.1fM instr, tick %d: memory %s (%s) %.0fs" % (
                s.m.cpu.icount / 1e6, s.m.ticks, "identical" if hp == hg else "DIFFERENT", hp[:12], dt))
            if hp != hg:
                diff = [i for i in range(0x100000) if s.m.mem[i] != gm.cpu.mem[i]][:16]
                print(" first differing addresses:", ["%05X" % x for x in diff])
                return False
    print("[diff] oracle and GDScript port identical for %d instructions (%d ticks), %d/%d key actions"
          % (s.m.cpu.icount, s.m.ticks, ai, len(actions)))
    print("[diff] GDScript port file log:", [(e[0], e[1]) for e in gdos.file_log])
    print("[diff] exited: oracle %s, gdport %s" % (s.m.exited, gm.exited))
    for side in ("py", "gd"):
        st = os.path.join(work, side, "89", "STATS.LOG")
        if os.path.exists(st):
            data = open(st, "rb").read()
            print("[diff] %s STATS.LOG %d bytes sha1 %s" % (side, len(data), hashlib.sha1(data).hexdigest()[:16]))
    if logs:
        print("[diff] GDScript port log:", logs[:10])
    return ai == len(actions)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default=os.environ.get("NFL_GAME_DIR", ""))
    ap.add_argument("--minstr", type=float, default=40)
    ap.add_argument("--keys", default="{when:CONFIGURATION}4{when:Color graphics}4{when:PC/XT}1"
                    "{when:home team}{Enter}{when:visiting team}{Down}{Enter}"
                    "{when:Choose the coach}c{when:Right keypad}r{when:coach for the Colts}c"
                    "{when:Loading the team roster}")
    ap.add_argument("--season", help="SEASON.NFL to copy to the team disk; runs NFL /s with Alt-E")
    a = ap.parse_args()
    tail = ""
    if a.season:
        tail = "/s"
        a.keys = ("{when:CONFIGURATION}4{when:Color graphics}4{when:PC/XT}1"
                  "{when:Timeouts}{wait:20}{Alt-E}")
    gdemu = transpile()
    print("== CPU unit tests on the transpiled GDScript CPU")
    rc = cpu_tests(gdemu)
    if a.game_dir:
        print("== realtime-mode smoke test of the GDScript machine")
        rc = rc or (0 if realtime_smoke(gdemu, a.game_dir) else 1)
        print("== differential run: GDScript port vs Python oracle on NFL.EXE")
        ok = differential(gdemu, a.game_dir, a.minstr, a.keys, tail, a.season)
        rc = rc or (0 if ok else 1)
    return rc


if __name__ == "__main__":
    sys.exit(main())
