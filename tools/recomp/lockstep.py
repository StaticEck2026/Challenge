#!/usr/bin/env python3
"""Lockstep differential test: recompiled GDScript vs the Python oracle.

The recompiled program is the *GDScript* that recompile.py writes, plus the
GDScript host layer (godot/host/*.gd), transpiled to Python with
tools/gdport/gd2py.py and run on the Godot API shim tools/gdport/gdrt.py.
The oracle is tools/refemu/ (the old 8086 interpreter).

Both run with the same deterministic clock: one BIOS tick every K "polls"
(service calls through a ROM stub + port reads), and a tick also whenever a
service blocks for input.  Keys from the scenario script are delivered at
ticks.  So both programs see the same time at the same logical points.

Checks:
* at every service call: service number and registers (AX BX CX DX SI DI BP
  SP DS ES SS); calls made by the overlay manager compare AX BX CX DX DS;
* every N service calls and at the end: CRC32 of the full 1 MB memory;
* at every {when:TEXT} of the key script: the text screen;
* at the end: every file the program wrote (STATS.LOG ...), byte for byte.

  python3 -I tools/recomp/lockstep.py --game-dir /path/to/nfl season
  python3 -I tools/recomp/lockstep.py --game-dir /path/to/nfl boot --max-services 400000

The oracle trace of a scenario is cached in build/recomp/trace_<scenario>.pkl.
"""
import argparse
import hashlib
import os
import pickle
import subprocess
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
GDPORT = os.path.join(ROOT, "tools", "gdport")
OUT = os.path.join(ROOT, "build", "gdpy")
sys.path.insert(0, os.path.join(ROOT, "tools", "refemu"))
sys.path.insert(0, GDPORT)
sys.path.insert(0, OUT)
sys.path.insert(0, HERE)

import scripts  # noqa: E402

HOST_FILES = ["cpu_state.gd", "machine.gd", "bios.gd", "dos.gd", "video.gd", "font_cp437.gd",
              "recomp_unit.gd", "recomp_host.gd"]
LOAD_SEG = 0x122
MGR_CS = 0x191F + LOAD_SEG
MEM_EVERY = 2000
STACK_SEG = 0x3832      # the program's stack segment (set by the C start-up)
MGR_STACK = 0x2F21 + LOAD_SEG   # the overlay manager's private start-up stack (256 bytes)


class Mismatch(Exception):
    pass


class StopRun(Exception):
    pass


# ----------------------------------------------------------------- scripts
class KeyScript:
    """The tick-driven key script of run.Session, for any machine."""

    def __init__(self, actions, text, ticks, queue, kbd_empty, log, shot=None):
        self.actions = actions
        self.ai = 0
        self.wait_ticks = 0
        self.text = text
        self.ticks = ticks
        self.queue = queue
        self.kbd_empty = kbd_empty
        self.log = log
        self.when_log = []
        self.shot = shot

    def on_tick(self):
        while self.ai < len(self.actions):
            kind, v = self.actions[self.ai]
            if kind == "wait":
                if self.wait_ticks == 0:
                    self.wait_ticks = v
                self.wait_ticks -= 1
                if self.wait_ticks > 0:
                    return
                self.ai += 1
                continue
            if kind in ("when", "gone"):
                if self.ticks() % 4:
                    return
                present = v in self.text()
                if present != (kind == "when"):
                    return
                self.when_log.append((self.ai, self.ticks(), self.text()))
                self.ai += 1
                continue
            if kind == "key":
                q = self.queue()
                if len(q) or not self.kbd_empty():
                    return
                q.extend(v)
                self.ai += 1
                return
            if kind == "shot" and self.shot:
                self.shot(v)
            self.ai += 1


# Input registers of the services NFL.EXE calls (int, AH) -> names.  int86()
# loads all eight words of a REGS struct the C code fills only partly, so the
# other registers can hold stack garbage (residue of the natively replaced
# overlay manager differs from the original's); those are not compared.
PROG_INPUTS = {
    (0x10, 0x00): ("al",), (0x10, 0x01): ("cx",), (0x10, 0x02): ("bh", "dx"), (0x10, 0x03): ("bh",),
    (0x10, 0x05): ("al",), (0x10, 0x06): ("al", "bh", "cx", "dx"), (0x10, 0x07): ("al", "bh", "cx", "dx"),
    (0x10, 0x09): ("al", "bx", "cx"), (0x10, 0x0B): ("bx",), (0x10, 0x0F): (),
    (0x16, 0x00): (), (0x16, 0x01): (), (0x16, 0x02): (), (0x1A, 0x00): (),
    (0x21, 0x02): ("dl",), (0x21, 0x09): ("dx", "ds"), (0x21, 0x2C): (), (0x21, 0x30): (),
    (0x21, 0x3C): ("cx", "dx", "ds"), (0x21, 0x3D): ("al", "dx", "ds"), (0x21, 0x3E): ("bx",),
    (0x21, 0x3F): ("bx", "cx", "dx", "ds"), (0x21, 0x40): ("bx", "cx", "dx", "ds"),
    (0x21, 0x41): ("dx", "ds"), (0x21, 0x42): ("al", "bx", "cx", "dx"), (0x21, 0x44): ("al", "bx", "dx"),
    (0x21, 0x45): ("bx",), (0x21, 0x4A): ("bx", "es"), (0x21, 0x4C): ("al",), (0x21, 0xEF): (),
}
ALL_NAMES = ("ax", "bx", "cx", "dx", "si", "di", "bp", "sp", "ds", "es", "ss")


def reg_vals(r, sr):
    return {"ax": r[0], "bx": r[3], "cx": r[1], "dx": r[2], "si": r[6], "di": r[7], "bp": r[5], "sp": r[4],
            "ds": sr[3], "es": sr[0], "ss": sr[2], "al": r[0] & 0xFF, "bh": r[3] >> 8, "dl": r[2] & 0xFF}


def regs_tuple(r, sr, n):
    ah = r[0] >> 8
    v = reg_vals(r, sr)
    names = PROG_INPUTS.get((n, ah), ALL_NAMES)
    # SP and SS are always meaningful (the IRET frame is there)
    return (ah,) + tuple(v[x] for x in names) + (v["sp"], v["ss"])


# inputs of the DOS functions the overlay manager calls (the other registers
# hold leftovers of the manager's own code, which is replaced natively)
MGR_INPUTS = {0x30: (), 0xEF: ("bx",), 0x3D: ("al", "dx", "ds"), 0x3E: ("bx",),
              0x42: ("al", "bx", "cx", "dx"), 0x3F: ("bx", "cx", "dx", "ds")}


def mgr_tuple(r, sr):
    ah = r[0] >> 8
    vals = {"al": r[0] & 0xFF, "bx": r[3], "cx": r[1], "dx": r[2], "ds": sr[3]}
    names = MGR_INPUTS.get(ah, ("al", "bx", "cx", "dx", "ds"))
    return (ah,) + tuple(vals[n] for n in names)


def is_mgr_call(mem, r, sr):
    a = (sr[2] << 4) + ((r[4] + 2) & 0xFFFF)
    return (mem[a] | (mem[a + 1] << 8)) == MGR_CS


def mem_crc(mem, ss=None):
    """CRC32 of the 1 MB memory; with ss, the 64 KB stack segment is left out
    (stack residue, see PROG_INPUTS)."""
    if ss is None:
        return zlib.crc32(bytes(mem[:0x100000]))
    lo = ss << 4
    plo = MGR_STACK << 4
    c = zlib.crc32(bytes(mem[:plo]))
    c = zlib.crc32(bytes(mem[plo + 0x100:lo]), c)
    return zlib.crc32(bytes(mem[lo + 0x10000:0x100000]), c)


# ------------------------------------------------------------------ oracle
def run_oracle(scenario, game_dir, k, max_services, log, snapshot=False):
    from run import Session, parse_keys
    from video import text_dump
    work = os.path.join(ROOT, "build", "recomp", "ls-oracle-" + scenario)
    scripts.prepare_dir(game_dir, work, scenario)
    sc = scripts.SCENARIOS[scenario]
    s = Session(work, "NFL.EXE", sc["args"], scripts.drives(work), "C", log=lambda m_: None, quiet=True)
    m = s.m
    cpu = m.cpu
    bios = s.bios
    m.tick_hooks.remove(s.on_tick)
    bios.idle_poll = lambda: None          # no idle fast-forward in test mode
    trace = []
    crcs = {}
    st = {"polls": 0, "retry": False, "nocount": False, "ticks": 0}
    stop = scenario in scripts.STOP_WHEN_DONE
    max_ticks = sc.get("max_ticks", 10 ** 9)
    ks = KeyScript(parse_keys(sc.get("lockstep_keys", sc["keys"])), lambda: text_dump(m), lambda: st["ticks"],
                   lambda: m.kbd_queue, m.bios_kbd_empty, log)

    def key_event(scv):
        m.kbd_data = scv
        st["nocount"] = True
        bios.int09()
        st["nocount"] = False

    def pump():
        q = m.kbd_queue
        while q:
            scv = q[0]
            modifier = scv in (0x1D, 0x2A, 0x36, 0x38)
            if not (scv & 0x80) and not modifier and not m.bios_kbd_empty():
                return
            q.pop(0)
            key_event(scv)

    frames = []

    def do_tick():
        bios.int08()
        st["ticks"] += 1
        ks.on_tick()
        pump()
        if m.mem[0x449] in (4, 5, 6):
            frames.append((st["ticks"], zlib.crc32(bytes(m.mem[0xB8000:0xBC000]))))
        if stop and (ks.ai >= len(ks.actions) or st["ticks"] >= max_ticks):
            raise StopRun()
        if st["ticks"] % 1000 == 0:
            log("[oracle] tick %d, %d service calls, script step %d/%d" % (
                st["ticks"], len(trace), ks.ai, len(ks.actions)))

    def sync():
        st["polls"] += 1
        if st["polls"] >= k:
            st["polls"] = 0
            do_tick()

    orig_hle = m.hle
    orig_port_in = m.port_in

    def hle(n):
        if not st["retry"]:
            r, sr = cpu.r, cpu.sr
            if is_mgr_call(m.mem, r, sr):
                trace.append((n, "M", mgr_tuple(r, sr)))
            else:
                trace.append((n, "P", regs_tuple(r, sr, n)))
            if len(trace) % MEM_EVERY == 0:
                crcs[len(trace)] = mem_crc(m.mem, STACK_SEG)
            if snapshot and len(trace) == max_services:
                st["snap"] = bytes(m.mem[:0x100000])
            sync()
        orig_hle(n)
        st["retry"] = bool(cpu.halted and m.idle)

    def port_in(port, w):
        if not w and not st["nocount"]:
            sync()
        return orig_port_in(port, w)
    m.hle = hle
    cpu.hle = hle
    m.port_in = port_in
    cpu.port_in = port_in
    t0 = time.time()
    while not m.exited and len(trace) < max_services:
        try:
            cpu.run(50000)
        except StopRun:
            break
        if cpu.halted:
            if st["retry"]:
                st["polls"] = 0
                do_tick()
                cpu.halted = False
                m.idle = False
            elif not m.exited:
                log("[oracle] halted at %04X:%04X" % (cpu.op_cs, cpu.op_ip))
                break
    dt = time.time() - t0
    crcs["end"] = mem_crc(m.mem, STACK_SEG)
    snap = st.get("snap")
    files = collect_files(work)
    log("[oracle] %s: %d service calls, %d ticks, %d instructions, %.1fs (%.0f instr/s), exited=%s code=%s, actions %d/%d" % (
        scenario, len(trace), st["ticks"], cpu.executed, dt, cpu.executed / max(dt, 1e-9), m.exited,
        m.exit_code, ks.ai, len(ks.actions)))
    return dict(trace=trace, crcs=crcs, files=files, when=ks.when_log, time=dt, executed=cpu.executed, snap=snap,
                frames=frames,
                ticks=st["ticks"], exited=m.exited, exit_code=m.exit_code, actions=(ks.ai, len(ks.actions)))


def collect_files(work):
    out = {}
    for dp, dn, fn in os.walk(work):
        for f in fn:
            if f.upper() in ("STATS.LOG", "AVGSTATS.LOG", "CONFIG.NFL") or f.upper().startswith("GAME."):
                p = os.path.join(dp, f)
                out[os.path.relpath(p, work)] = open(p, "rb").read()
    return out


# -------------------------------------------------------------- recompiled
def transpile(recomp_dir, log):
    os.makedirs(OUT, exist_ok=True)
    host = [os.path.join(ROOT, "godot", "host", f) for f in HOST_FILES]
    gen = sorted(os.path.join(recomp_dir, f) for f in os.listdir(recomp_dir) if f.endswith(".gd"))
    dst = os.path.join(OUT, "recomp_mod.py")
    srcs = host + gen
    stamp = hashlib.sha1()
    for p in srcs + [os.path.join(GDPORT, "gd2py.py")]:
        stamp.update(open(p, "rb").read())
    stamp_path = dst + ".sha1"
    if os.path.exists(dst) and os.path.exists(stamp_path) and open(stamp_path).read() == stamp.hexdigest():
        return dst
    t0 = time.time()
    subprocess.run([sys.executable, os.path.join(GDPORT, "gd2py.py"), dst] + srcs, check=True)
    open(stamp_path, "w").write(stamp.hexdigest())
    log("[gd2py] %d GDScript files -> %s in %.1fs" % (len(srcs), dst, time.time() - t0))
    return dst


def run_recomp(scenario, game_dir, k, ref, log, max_services, snapshot=False):
    import importlib
    import gdrt
    from font_cp437 import FONT8
    from video import text_dump
    t0 = time.time()
    mod = importlib.import_module("recomp_mod")
    log("[recomp] module imported in %.1fs" % (time.time() - t0))
    work = os.path.join(ROOT, "build", "recomp", "ls-recomp-" + scenario)
    scripts.prepare_dir(game_dir, work, scenario)
    sc = scripts.SCENARIOS[scenario]
    gm = mod.Machine()
    logs = []
    gm.log_msg = lambda s_: logs.append(s_)
    gm.deterministic = True
    gm.polls_per_tick = k
    gb = mod.BIOS(gm, bytearray(FONT8), "", mod.BIOS.DEFAULT_EPOCH, 12 * 3600)
    dr = scripts.drives(work)
    gdos = mod.DOS(gm, gb, {d: [p, ""] for d, p in dr.items()}, "C")
    info = gdos.load_program("C:NFL.EXE", sc["args"])
    log("[recomp] loaded %s" % info)
    host = mod.RecompHost()
    idx = mod.recomp_index()
    host.setup(gm, gb, gdos, idx, gdrt.Callable(lambda name: getattr(mod, name)()))
    host.log_unwinds = 30
    from run import parse_keys

    class ScrView:
        """The GDScript machine seen through the oracle's video helpers."""

        def __init__(self):
            self.mem = gm.cpu.mem

        def __getattr__(self, name):
            return getattr(gm, name)
    Scr = ScrView()
    ks = KeyScript(parse_keys(sc.get("lockstep_keys", sc["keys"])), lambda: text_dump(Scr), lambda: gm.ticks,
                   lambda: gm.kbd_queue, gm.bios_kbd_empty, log)
    gm.tick_hooks.append(gdrt.Callable(ks.on_tick))
    frames = []
    shots = os.path.join(ROOT, "build", "recomp", "shots-" + scenario)

    def frame_hook():
        if gm.cpu.mem[0x449] in (4, 5, 6):
            frames.append((gm.ticks, zlib.crc32(bytes(gm.cpu.mem[0xB8000:0xBC000]))))
            if len(frames) in (1, 300, 1000, 2000, 3000, 4000):
                import video
                from font_cp437 import FONT16
                os.makedirs(shots, exist_ok=True)
                video.screenshot(Scr, os.path.join(shots, "cga_%03d.png" % len(frames)), FONT8, FONT16)
        if scenario in scripts.STOP_WHEN_DONE and (ks.ai >= len(ks.actions)
                                                   or gm.ticks >= sc.get("max_ticks", 10 ** 9)):
            gm.abort = True
    gm.tick_hooks.append(gdrt.Callable(frame_hook))
    cpu = gm.cpu
    trace = ref["trace"] if ref else None
    crcs = ref["crcs"] if ref else {}
    state = {"n": 0, "first_bad": None}

    def on_service(n):
        i = state["n"]
        state["n"] = i + 1
        r, sr = cpu.r, cpu.sr
        mine = (n, "M", mgr_tuple(r, sr)) if is_mgr_call(cpu.mem, r, sr) else (n, "P", regs_tuple(r, sr, n))
        if trace is not None:
            if i >= len(trace):
                raise Mismatch("recompiled program made more service calls than the oracle (%d)" % i)
            if trace[i] != mine:
                raise Mismatch("service call #%d differs:\n  oracle %s\n  recomp %s\n  prev oracle %s" % (
                    i, fmt(trace[i]), fmt(mine), fmt(trace[i - 1]) if i else "-"))
            if (i + 1) % MEM_EVERY == 0 and (i + 1) in crcs:
                c = mem_crc(cpu.mem, STACK_SEG)
                if c != crcs[i + 1]:
                    raise Mismatch("memory CRC differs after service call #%d" % (i + 1))
        if i + 1 >= max_services:
            gm.abort = True
            if snapshot:
                state["snap"] = bytes(cpu.mem[:0x100000])
    gm.service_hook = gdrt.Callable(on_service)
    t0 = time.time()
    err = None
    try:
        host.run()
    except Mismatch as e:
        err = str(e)
    dt = time.time() - t0
    res = dict(time=dt, services=state["n"], ticks=gm.ticks, exited=gm.exited, exit_code=gm.exit_code,
               error=err or host.error_msg, files=collect_files(work), when=ks.when_log, logs=logs,
               crc_end=mem_crc(cpu.mem, STACK_SEG), actions=(ks.ai, len(ks.actions)), host=host,
               misses=host.dispatch_misses, dispatched=host.calls_dispatched, ovl_loads=host.ovl_loads,
               snap=state.get("snap"), frames=frames)
    log("[recomp] %s: %d service calls, %d ticks, %.1fs, exited=%s code=%s, actions %d/%d, %d dispatched calls, "
        "%d dispatch misses, %d overlay loads" % (
            scenario, state["n"], gm.ticks, dt, gm.exited, gm.exit_code, ks.ai, len(ks.actions),
            host.calls_dispatched, host.dispatch_misses, host.ovl_loads))
    if logs:
        log("[recomp] host log: %s" % logs[:12])
    return res


def fmt(t):
    n, kind, regs = t
    if kind == "M":
        names = ["AH"] + [x.upper() for x in MGR_INPUTS.get(regs[0], ("al", "bx", "cx", "dx", "ds"))]
    else:
        names = ["AH"] + [x.upper() for x in PROG_INPUTS.get((n, regs[0]), ALL_NAMES)] + ["SP", "SS"]
    return "INT %02Xh %s %s" % (n, "mgr" if kind == "M" else "prog",
                                " ".join("%s=%04X" % (a, b) for a, b in zip(names, regs)))


def diff_at(a, n):
    def log(msg):
        print(msg, flush=True)
    ref = run_oracle(a.scenario, a.game_dir, a.polls_per_tick, n, log, snapshot=True)
    transpile(a.recomp_dir, log)
    res = run_recomp(a.scenario, a.game_dir, a.polls_per_tick, None, log, n, snapshot=True)
    m1, m2 = ref["snap"], res["snap"]
    ranges = []
    i = 0
    while i < 0x100000:
        if m1[i] != m2[i]:
            j = i
            while j < 0x100000 and (m1[j] != m2[j] or (j + 8 < 0x100000 and m1[j:j + 8] != m2[j:j + 8])):
                j += 1
            ranges.append((i, j))
            i = j
        else:
            i += 1
    log("[diff] %d differing ranges at service call %d" % (len(ranges), n))
    for lo, hi in ranges[:60]:
        log("  %05X-%05X (%d bytes) seg %04X:%04X  oracle %s  recomp %s" % (
            lo, hi, hi - lo, (lo >> 4), lo & 15, bytes(m1[lo:min(hi, lo + 24)]).hex(),
            bytes(m2[lo:min(hi, lo + 24)]).hex()))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-dir", default=os.environ.get("NFL_GAME_DIR", ""))
    ap.add_argument("--recomp-dir", default=os.path.join(ROOT, "godot", "recomp"))
    ap.add_argument("--polls-per-tick", type=int, default=64)
    ap.add_argument("--max-services", type=int, default=10 ** 9)
    ap.add_argument("--fresh-oracle", action="store_true", help="ignore a cached oracle trace")
    ap.add_argument("--no-oracle", action="store_true", help="run the recompiled program alone")
    ap.add_argument("--diff-at", type=int, default=0,
                    help="debug: run both sides to service call N and list differing memory ranges")
    ap.add_argument("scenario", choices=list(scripts.SCENARIOS))
    a = ap.parse_args()
    if a.diff_at:
        return diff_at(a, a.diff_at)

    def log(msg):
        print(msg, flush=True)
    ref = None
    if not a.no_oracle:
        cache = os.path.join(ROOT, "build", "recomp", "trace_%s_k%d_n%d.pkl" % (
            a.scenario, a.polls_per_tick, min(a.max_services, 10 ** 9)))
        if os.path.exists(cache) and not a.fresh_oracle:
            ref = pickle.load(open(cache, "rb"))
            log("[oracle] cached trace %s: %d service calls (%.1fs when recorded)" % (
                cache, len(ref["trace"]), ref["time"]))
        else:
            ref = run_oracle(a.scenario, a.game_dir, a.polls_per_tick, a.max_services, log)
            pickle.dump(ref, open(cache, "wb"))
    transpile(a.recomp_dir, log)
    res = run_recomp(a.scenario, a.game_dir, a.polls_per_tick, ref, log, a.max_services)
    ok = res["error"] is None or res["error"] == ""
    if res["error"]:
        log("[lockstep] FAIL: %s" % res["error"])
    if ref and ok:
        if res["services"] != len(ref["trace"]):
            log("[lockstep] FAIL: %d service calls vs oracle %d" % (res["services"], len(ref["trace"])))
            ok = False
        if res["crc_end"] != ref["crcs"]["end"]:
            log("[lockstep] FAIL: final memory differs")
            ok = False
        for (ai, t1, x1), (aj, t2, x2) in zip(ref["when"], res["when"]):
            if (ai, t1, x1) != (aj, t2, x2):
                log("[lockstep] FAIL: screen at script step %d differs" % ai)
                ok = False
                break
        rf = ref.get("frames")
        if rf is None:
            log("[lockstep] CGA frames: not in this (older) oracle trace; rerun with --fresh-oracle")
        elif rf or res["frames"]:
            same = rf == res["frames"]
            log("[lockstep] CGA frames (video memory CRC at every tick in graphics mode): oracle %d, recomp %d, %s" % (
                len(rf), len(res["frames"]), "IDENTICAL" if same else "DIFFERENT"))
            ok = ok and same
        for name in sorted(set(ref["files"]) | set(res["files"])):
            a_ = ref["files"].get(name)
            b_ = res["files"].get(name)
            same = a_ == b_
            log("[lockstep] file %-16s oracle %s recomp %s %s" % (
                name, "%d bytes sha1 %s" % (len(a_), hashlib.sha1(a_).hexdigest()[:12]) if a_ is not None else "-",
                "%d bytes sha1 %s" % (len(b_), hashlib.sha1(b_).hexdigest()[:12]) if b_ is not None else "-",
                "IDENTICAL" if same else "DIFFERENT"))
            ok = ok and same
        log("[lockstep] %d service calls compared, %d memory checkpoints, %d screens; oracle %.1fs, "
            "recompiled %.1fs (%.1fx)" % (res["services"], len([k_ for k_ in ref["crcs"] if k_ != "end"
                                                               and k_ <= res["services"]]),
                                         len(res["when"]), ref["time"], res["time"], ref["time"] / max(res["time"], 1e-9)))
    log("[lockstep] %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
