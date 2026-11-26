#!/usr/bin/env python3
"""Run main.gd's smoke mode (godot/host/smoke.gd) without Godot.

Transpiles the host layer, the smoke classes and the recompiled program with
tools/gdport/gd2py.py and drives Smoke the way main.gd does: setup(), start()
(the real program thread, as a Python thread), then step() once per 60 Hz
"frame" until it reports the result.  The scenarios are run in the order
given; `season` uses the SEASON.NFL that `seasoncom` wrote in the same output
folder.

  python3 -I tools/recomp/smoke_test.py [--data godot/recomp/game] boot seasoncom season
"""
import argparse
import importlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import lockstep  # noqa: E402  (sets sys.path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(ROOT, "godot", "recomp", "game"),
                    help="the game's data folder (read only; default: the --bundle-data copy)")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "recomp", "smoke"))
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("scenarios", nargs="*", default=["boot", "seasoncom", "season"])
    a = ap.parse_args()
    import gdrt
    rc = 0
    os.makedirs(a.out, exist_ok=True)
    for name in a.scenarios:
        prog = "season" if name == "seasoncom" else ""
        rdir = os.path.join(ROOT, "godot", "recomp", prog) if prog else os.path.join(ROOT, "godot", "recomp")
        lockstep.transpile(rdir, print)
        mod = importlib.import_module(lockstep.module_name(rdir))
        smoke = mod.Smoke()
        smoke.timeout_sec = a.timeout
        err = smoke.setup(name, a.data, rdir, a.out, mod.recomp_index(),
                          gdrt.Callable(lambda n, mod=mod: getattr(mod, n)()))
        if err:
            print("[smoke] %s: setup failed: %s" % (name, err))
            rc = 1
            continue
        smoke.start()
        while not smoke.step():
            time.sleep(1 / 60)
        rc = rc or smoke.exit_code
    print("[smoke-test] %s" % ("PASS" if rc == 0 else "FAIL"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
