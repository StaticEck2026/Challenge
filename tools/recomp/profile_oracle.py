#!/usr/bin/env python3
"""Coverage profile of NFL.EXE from the Python oracle (tools/refemu/).

Runs the oracle through scripted scenarios and records, per resident-overlay
configuration ("variant"):

* every executed instruction address (coverage), and
* every *dynamic* control transfer: indirect CALL/JMP targets, and every
  RET/RETF/IRET whose target is not the instruction after the matching
  call (tracked with a shadow call stack).  The IRET of a ROM service stub
  back into the program (int86() continuation) is one of these.

The translator (recompile.py) reads the transfer log as a coverage profile;
the coverage sets are used by the coverage report (coverage.py).

  python3 -I tools/recomp/profile_oracle.py --game-dir /path/to/nfl boot season replay
  python3 -I tools/recomp/profile_oracle.py --export tools/recomp/profile.json build/recomp/profile_*.json

Output: build/recomp/profile_<scenario>.json (addresses only, no game bytes).
"""
import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools", "refemu"))
sys.path.insert(0, HERE)

from run import Session, parse_keys  # noqa: E402

import scripts  # noqa: E402

LOAD_SEG = 0x122
TABLE_SEG = 0x18B4 + LOAD_SEG
MGR_SEG = 0x191F + LOAD_SEG
NOVL = 12


def resident_key(mem):
    """Tuple of overlay numbers whose 'loaded' bit is set in the PLINK86 table."""
    t = TABLE_SEG << 4
    out = []
    for n in range(1, NOVL + 1):
        rec = t + 4 + 16 * (n - 1)
        if mem[rec + 1] & 0x80:
            out.append(n)
    return tuple(out)


class Profiler:
    def __init__(self, session):
        self.s = session
        self.cpu = session.m.cpu
        self.cov = {}            # variant key -> set(lin)
        self.xfers = {}          # (kind, site_cs, site_ip, tgt_cs, tgt_ip, variant) -> count
        self.shadow = []
        self.variant = ()
        self.cur_cov = self.cov.setdefault(self.variant, set())
        self._install()

    def refresh_variant(self):
        k = resident_key(self.cpu.mem)
        if k != self.variant:
            self.variant = k
            self.cur_cov = self.cov.setdefault(k, set())

    def log(self, kind, tcs, tip):
        cpu = self.cpu
        key = (kind, cpu.op_cs, cpu.op_ip, tcs, tip, self.variant)
        self.xfers[key] = self.xfers.get(key, 0) + 1

    def _install(self):
        cpu = self.cpu
        ops = cpu.ops
        prof = self
        orig = {k: ops[k] for k in (0xC2, 0xC3, 0xCA, 0xCB, 0xCF, 0xE8, 0x9A, 0xFF)}
        orig_interrupt = cpu.interrupt

        def ret_like(op, kind):
            def f(o):
                orig[op](o)
                tgt = (cpu.sr[1], cpu.ip)
                sh = prof.shadow
                if sh and sh[-1] == tgt:
                    sh.pop()
                    return
                if cpu.op_cs == MGR_SEG:
                    prof.refresh_variant()
                # resynchronise (longjmp-like unwinding)
                for i in range(len(sh) - 1, -1, -1):
                    if sh[i] == tgt:
                        prof.log(kind + "-unwind%d" % (len(sh) - i - 1), tgt[0], tgt[1])
                        del sh[i:]
                        break
                else:
                    prof.log(kind, tgt[0], tgt[1])
            return f

        for op, kind in ((0xC2, "ret"), (0xC3, "ret"), (0xCA, "retf"), (0xCB, "retf"), (0xCF, "iret")):
            ops[op] = ret_like(op, kind)

        def call_near(o):
            sh = prof.shadow
            ret_ip = (cpu.ip + 2) & 0xFFFF
            orig[0xE8](o)
            if cpu.ip != ret_ip:        # 'call $+3 / pop reg' is not a call
                sh.append((cpu.sr[1], ret_ip))
            if len(sh) > 4096:
                del sh[:2048]

        def call_far(o):
            ret = (cpu.sr[1], (cpu.ip + 4) & 0xFFFF)
            orig[0x9A](o)
            prof.shadow.append(ret)

        def grp5(o):
            mem = cpu.mem
            cs = cpu.sr[1]
            m = mem[((cs << 4) + cpu.ip) & 0xFFFFF]
            sub = (m >> 3) & 7
            sp0 = cpu.r[4]
            orig[0xFF](o)
            if sub in (2, 3, 4, 5):
                kind = {2: "callind", 3: "callfarind", 4: "jmpind", 5: "jmpfarind"}[sub]
                prof.log(kind, cpu.sr[1], cpu.ip)
                if sub in (2, 3):
                    # the return address the call pushed
                    a = (cpu.sr[2] << 4) + cpu.r[4]
                    rip = mem[a] | (mem[a + 1] << 8)
                    rcs = cs if sub == 2 else (mem[a + 2] | (mem[a + 3] << 8))
                    prof.shadow.append((rcs, rip))
            del sp0

        def interrupt(n):
            ret = (cpu.sr[1], cpu.ip)
            orig_interrupt(n)
            prof.shadow.append(ret)

        ops[0xE8] = call_near
        ops[0x9A] = call_far
        ops[0xFF] = grp5
        cpu.interrupt = interrupt
        # instrumented main loop (coverage)
        cpu.run = self.run

    def run(self, n):
        cpu = self.cpu
        mem = cpu.mem
        ops = cpu.ops
        sr = cpu.sr
        i = 0
        while i < n:
            if cpu.irq_flag and cpu.iflag and not cpu.inhibit:
                irq = cpu.pic.ack()
                if irq >= 0:
                    cpu.interrupt(irq)
            if cpu.halted:
                break
            cpu.inhibit = False
            cpu.ovr = -1
            cpu.rep = 0
            cs = sr[1]
            ip = cpu.ip
            cpu.op_cs = cs
            cpu.op_ip = ip
            lin = ((cs << 4) + ip) & 0xFFFFF
            self.cur_cov.add(lin)
            op = mem[lin]
            cpu.ip = (ip + 1) & 0xFFFF
            ops[op](op)
            i += 1
        cpu.icount += i
        cpu.executed += i
        return i

    def dump(self, path, extra):
        xf = []
        for (kind, scs, sip, tcs, tip, var), cnt in sorted(self.xfers.items()):
            xf.append({"kind": kind, "site": "%04X:%04X" % (scs, sip), "target": "%04X:%04X" % (tcs, tip),
                       "variant": list(var), "count": cnt})
        cov = {",".join(str(x) for x in k): compress(sorted(v)) for k, v in self.cov.items() if v}
        data = {"load_seg": "%04X" % LOAD_SEG, "transfers": xf, "coverage": cov}
        data.update(extra)
        with open(path, "w") as f:
            json.dump(data, f, indent=0)


def compress(addrs):
    """Sorted addresses -> list of deltas (compact JSON)."""
    res = []
    prev = 0
    for a in addrs:
        res.append(a - prev)
        prev = a
    return res


def run_scenario(name, game_dir, out_dir, budget_minstr, log):
    work = os.path.join(ROOT, "build", "recomp", "prof-" + name)
    scripts.prepare_dir(game_dir, work, name)
    sc = scripts.SCENARIOS[name]
    drives = scripts.drives(work)
    s = Session(work, "NFL.EXE", sc["args"], drives, "C", log=log, quiet=True,
                shots=os.path.join(out_dir, "shots-" + name))
    prof = Profiler(s)
    s.actions = parse_keys(sc["keys"])
    t0 = time.time()
    s.run(int(budget_minstr * 1e6), progress=60)
    dt = time.time() - t0
    log("[%s] %d executed instructions in %.0fs; actions %d/%d; exited=%s" % (
        name, s.m.cpu.executed, dt, s.ai, len(s.actions), s.m.exited))
    path = os.path.join(out_dir, "profile_%s.json" % name)
    prof.dump(path, {"scenario": name, "executed": s.m.cpu.executed, "exited": s.m.exited,
                     "actions_done": s.ai, "actions": len(s.actions)})
    ntot = sum(len(v) for v in prof.cov.values())
    log("[%s] wrote %s: %d transfer kinds, %d covered addresses in %d variants" % (
        name, path, len(prof.xfers), ntot, len(prof.cov)))


def export(paths, out):
    """Merge scenario profiles into the compact translator input (tools/recomp/profile.json):
    only the transfers the translator uses, without counts."""
    keep = {}
    for p in paths:
        for t in json.load(open(p))["transfers"]:
            kind = t["kind"]
            if t["site"].startswith("%04X:" % MGR_SEG):
                continue                      # native overlay manager
            if kind in ("jmpind", "callind", "callfarind", "jmpfarind"):
                pass
            elif "unwind" in kind:
                kind = "unwind"               # only the site matters
                t = dict(t, target="0000:0000", variant=[])
            elif kind in ("ret", "retf", "iret"):
                if t["site"].startswith("F000:") and not t["target"].startswith("F000:"):
                    pass                      # ROM stub IRET into the program (int86 continuation)
                elif t["target"].startswith("F000:"):
                    continue
            else:
                continue
            keep[(kind, t["site"], t["target"], tuple(t["variant"]))] = 1
    data = {"_doc": "Dynamic control transfers of NFL.EXE v2.01 observed in the Python oracle by "
                    "tools/recomp/profile_oracle.py (runtime segments, load segment 0122h). "
                    "Addresses only; regenerate with --export.",
            "sources": [os.path.basename(p) for p in paths],
            "transfers": [{"kind": k[0], "site": k[1], "target": k[2], "variant": list(k[3])}
                          for k in sorted(keep)]}
    with open(out, "w") as f:
        json.dump(data, f, indent=0)
    print("wrote %s: %d transfers" % (out, len(keep)))


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--export":
        return export(sys.argv[3:], sys.argv[2])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-dir", default=os.environ.get("NFL_GAME_DIR", ""))
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "recomp"))
    ap.add_argument("--minstr", type=float, default=600, help="instruction budget per scenario (millions)")
    ap.add_argument("names", nargs="+", choices=list(scripts.SCENARIOS))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    def log(msg):
        print(msg, file=sys.stderr, flush=True)
    for n in a.names:
        run_scenario(n, a.game_dir, a.out, a.minstr, log)


if __name__ == "__main__":
    main()
