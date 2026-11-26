"""Run the recompiled program (GDScript -> Python via gd2py) headless, the way
tools/recomp/lockstep.py does, with a key script and per-tick callbacks.

Used by the Modern-UI tools (capture.py, check_positions.py, research) to get
real game states without Godot.  Deterministic clock: one BIOS tick every
`polls_per_tick` service calls / port reads, exactly as in lockstep.py.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools", "recomp"))
sys.path.insert(0, os.path.join(ROOT, "tools", "refemu"))
sys.path.insert(0, os.path.join(ROOT, "tools", "gdport"))
sys.path.insert(0, os.path.join(ROOT, "build", "gdpy"))

import lockstep  # noqa: E402
import scripts  # noqa: E402

DGROUP_LINEAR = (0x23D6 + lockstep.LOAD_SEG) << 4


class StopRun(Exception):
    pass


class Run:
    """One headless run of the recompiled program.

    keys: tools/refemu/run.py key script.  on_tick(run) is called after every
    BIOS tick (after the key script step).  Call run.stop() to end the run.
    """

    def __init__(self, game_dir, name, args="", keys="", season=False, polls_per_tick=64,
                 max_ticks=10 ** 9, log=print, keep_config=None):
        import importlib
        import gdrt
        from font_cp437 import FONT8
        from run import parse_keys
        from video import text_dump
        lockstep.transpile(os.path.join(ROOT, "godot", "recomp"), log)
        self.mod = importlib.import_module("recomp_mod")
        mod = self.mod
        work = os.path.join(ROOT, "build", "modern", "run-" + name)
        scripts.prepare_dir(game_dir, work, "season" if season else name)
        if keep_config:
            with open(os.path.join(work, "CONFIG.NFL"), "wb") as f:
                f.write(keep_config)
        lockstep.remove_exe(work)
        self.work = work
        gm = mod.Machine()
        self.logs = []
        gm.log_msg = lambda s_: self.logs.append(s_)
        gm.deterministic = True
        gm.polls_per_tick = polls_per_tick
        gb = mod.BIOS(gm, bytearray(FONT8), "", mod.BIOS.DEFAULT_EPOCH, 12 * 3600)
        dr = scripts.drives(work)
        gdos = mod.DOS(gm, gb, {d: [p, ""] for d, p in dr.items()}, "C")
        lockstep.install(mod, gdos, os.path.join(ROOT, "godot", "recomp"), args)
        host = mod.RecompHost()
        host.setup(gm, gb, gdos, mod.recomp_index(), gdrt.Callable(lambda n: getattr(mod, n)()))
        self.gm, self.host = gm, host
        self.mem = gm.cpu.mem

        class ScrView:
            def __init__(s):
                s.mem = gm.cpu.mem

            def __getattr__(s, a):
                return getattr(gm, a)
        self.scr = ScrView()
        self._text_dump = text_dump
        self.ks = lockstep.KeyScript(parse_keys(keys), self.text, lambda: gm.ticks, lambda: gm.kbd_queue,
                                     gm.bios_kbd_empty, log)
        self.on_tick = None
        self.max_ticks = max_ticks
        gm.tick_hooks.append(gdrt.Callable(self._tick))
        self.log = log

    def text(self):
        return self._text_dump(self.scr)

    def script_done(self):
        return self.ks.ai >= len(self.ks.actions)

    def stop(self):
        self.gm.abort = True

    def _tick(self):
        self.ks.on_tick()
        if self.on_tick:
            self.on_tick(self)
        if self.gm.ticks >= self.max_ticks:
            self.stop()

    def mode(self):
        return self.mem[0x449]

    def run(self):
        self.host.run()
        return self
