#!/usr/bin/env python3
"""Smoke test of the Godot thread model, without Godot.

Runs the transpiled GDScript (host layer + recompiled program, see
lockstep.py) the way main.gd does: the program on its own thread with the
realtime clock (Machine.deterministic = false), and a "main thread" loop at
60 Hz that wakes it, feeds keys through Machine.key_scancodes() (the mutex
protected queue), reads video memory, and finally stops the program with
request_abort() + join.  The Godot Mutex/Semaphore/Thread are mapped to
Python's threading module by tools/gdport/gdrt.py.

  python3 -I tools/recomp/thread_smoke.py --game-dir /path/to/nfl
"""
import argparse
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import lockstep  # noqa: E402  (sets sys.path for gdport/refemu/build)
import scripts  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default=os.environ.get("NFL_GAME_DIR", ""))
    ap.add_argument("--speed", type=float, default=4.0)
    ap.add_argument("--timeout", type=float, default=300.0)
    a = ap.parse_args()
    import importlib
    import gdrt
    from font_cp437 import FONT8
    from run import parse_keys
    from video import text_dump
    lockstep.transpile(os.path.join(ROOT, "godot", "recomp"), print)
    mod = importlib.import_module("recomp_mod")
    work = os.path.join(ROOT, "build", "recomp", "thread-smoke")
    scripts.prepare_dir(a.game_dir, work, "boot")
    print("[main] removed: %s" % lockstep.remove_exe(work))
    gm = mod.Machine()
    logs = []
    gm.log_msg = lambda s: logs.append(s)
    gm.speed = a.speed
    gb = mod.BIOS(gm, bytearray(FONT8), "", mod.BIOS.DEFAULT_EPOCH, 12 * 3600)
    gdos = mod.DOS(gm, gb, {d: [p, ""] for d, p in scripts.drives(work).items()}, "C")
    lockstep.install(mod, gdos, os.path.join(ROOT, "godot", "recomp"), "/i")
    host = mod.RecompHost()

    def program_thread():          # main.gd _program_thread()
        host.setup(gm, gb, gdos, mod.recomp_index(), gdrt.Callable(lambda n: getattr(mod, n)()))
        host.run()
    th = threading.Thread(target=program_thread, daemon=True)
    t0 = time.time()
    th.start()

    class View:
        def __init__(self):
            self.mem = gm.cpu.mem

        def __getattr__(self, n):
            return getattr(gm, n)
    view = View()
    # main thread: 60 Hz frames; keys from the scenario script, triggered by screen text
    actions = parse_keys(scripts.CONFIG_KEYS + scripts.TEAM_KEYS + "{when:Loading the team roster}{when:Timeouts}")
    ai = 0
    frames = 0
    seen = []
    while time.time() - t0 < a.timeout and ai < len(actions):
        gm.wake()
        frames += 1
        kind, v = actions[ai]
        if kind == "when":
            if v in text_dump(view):
                seen.append((v, round(time.time() - t0, 1), gm.ticks))
                ai += 1
        elif kind == "key":
            if not gm.kbd_queue and not gm.in_queue and gm.bios_kbd_empty():
                gm.key_scancodes(list(v))
                ai += 1
        else:
            ai += 1
        time.sleep(1 / 60)
    ok = ai == len(actions)
    for v, t, tk in seen:
        print("[main] %-26r on screen after %5.1fs (BIOS ticks %d)" % (v, t, tk))
    t1 = time.time()
    gm.request_abort()             # main.gd _stop()
    th.join(30)
    print("[main] request_abort(): program thread %s after %.2fs; %d frames, %d service calls, error=%r" % (
        "joined" if not th.is_alive() else "STILL RUNNING", time.time() - t1, frames, gm.cpu.icount,
        host.error_msg))
    print("[thread-smoke] %s" % ("PASS" if ok and not th.is_alive() else "FAIL"))
    return 0 if ok and not th.is_alive() else 1


if __name__ == "__main__":
    sys.exit(main())
