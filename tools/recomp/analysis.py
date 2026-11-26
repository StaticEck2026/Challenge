"""Discovery: functions, basic blocks, call graph, liveness.

A *function* is identified by (unit, cs, ip): the unit whose bytes it is
translated from ('root' or an overlay number), and the CS:IP it is entered
with (near branches are relative to CS).  Its body is every instruction
reachable from the entry through intra-procedural edges (fall-through, near
jumps, conditional branches, switch-table targets, same-segment indirect jump
targets).  Code shared by several functions (common epilogues, jumps into
another function's body) is translated into each of them.

Calls (near/far, through overlay thunks, through the dispatcher) start new
functions.  The compiler's two switch helpers are not called: each call site
becomes a direct multi-way branch built from the inline table.
"""
import json
from collections import defaultdict, deque

import x86
from x86 import DecodeError, Insn, decode

ALL = x86.ALL
REGS16 = ["ax", "cx", "dx", "bx", "sp", "bp", "si", "di"]
# register ids for liveness: 0..7 general, 8 es, 10 ss, 11 ds  (9 = cs never cached)
R_ES, R_SS, R_DS = 8, 10, 11
SEG_RID = {0: R_ES, 2: R_SS, 3: R_DS}
ALL_REGS = frozenset(list(range(8)) + [R_ES, R_SS, R_DS])


class Func:
    def __init__(self, key):
        self.key = key            # (unit, cs, ip)
        self.unit, self.cs, self.ip = key
        self.insns = {}           # ip -> Insn
        self.blocks = {}          # ip -> Block
        self.calls = []           # (insn, kind, callee key or None)
        self.dyn = {}             # ip of dynamic transfer insn -> list of targets
        self.switch = {}          # ip of switch call -> dict(kind, targets, default, table)
        self.mentioned = set()
        self.written = set()
        self.name = "f_%s_%04X_%04X" % (unit_tag(self.unit), self.cs, self.ip)
        self.file = None
        self.fin = 0              # flags live on entry
        self.fout = 0             # flags live at return (union over callers)
        self.defs = set()         # registers possibly modified (transitively)
        self.callers = set()
        self.has_dyn_call = False


class Block:
    def __init__(self, ip):
        self.ip = ip
        self.insns = []
        self.succs = []
        self.live_in = 0
        self.live_out = 0


def unit_tag(unit):
    return "r" if unit == "root" else "o%02d" % unit


def key_str(key):
    return "%s:%04X:%04X" % (unit_tag(key[0]), key[1], key[2])


class Analysis:
    def __init__(self, prog, profile=None, hints=None, log=print):
        self.p = prog
        self.log = log
        self.funcs = {}
        self.readers = {}
        self.base = prog.base
        rt = 0x13F3 + prog.base
        self.rt_seg = rt
        self.SW_DENSE = (rt, 0x00E7)
        self.SW_SPARSE = (rt, 0x07DC)
        self.dyn_targets = defaultdict(set)     # (unit, cs, ip) of site -> {(cs, ip)}
        self.ret_jump_sites = set()             # (cs, ip) of RET/RETF/IRET used as a jump
        self.entries = []
        self.warnings = []
        self.hints = hints or {}
        self.profile = profile or []
        self._load_profile()

    # ------------------------------------------------------------ profile
    def site_unit(self, cs, ip, variant):
        lin = ((cs << 4) + ip) & 0xFFFFF
        if self.p.root_lin <= lin < self.p.root_end:
            return "root"
        best = None
        for n in variant:
            if n == 12:
                continue
            o = self.p.ovl[n]
            if o["start"] <= lin < o["end"]:
                best = n
        return best

    def _load_profile(self):
        for t in self.profile:
            kind = t["kind"]
            scs, sip = (int(x, 16) for x in t["site"].split(":"))
            tcs, tip = (int(x, 16) for x in t["target"].split(":"))
            var = t["variant"]
            if scs == self.p.mgr_seg or scs == 0xF000 and kind.startswith("ret"):
                pass
            if kind in ("jmpind", "callind", "callfarind", "jmpfarind"):
                su = self.site_unit(scs, sip, var)
                if su is None:
                    continue
                self.dyn_targets[(su, scs, sip)].add((tcs, tip))
                if kind in ("callind", "callfarind", "jmpfarind") or tcs != scs:
                    tu = self.site_unit(tcs, tip, var)
                    if tu is not None:
                        self.entries.append(((tu, tcs, tip), "profile " + kind))
            elif kind == "iret" or kind == "retf" or kind == "ret":
                # a return to an address that no call on the stack expects:
                # the target must be enterable through the dispatcher
                if (scs, sip) in ((self.rt_seg, 0x00FD), (self.rt_seg, 0x07FA)):
                    continue          # switch helpers: handled at the call site
                if scs == self.p.mgr_seg:
                    continue          # overlay manager (native)
                if tcs == 0xF000:
                    continue          # into a ROM service stub (native)
                tu = self.site_unit(tcs, tip, var)
                if tu is not None:
                    self.entries.append(((tu, tcs, tip), "profile " + kind))
                    self.ret_jump_sites.add((scs, sip))

    # ------------------------------------------------------------ reading
    def reader(self, unit):
        r = self.readers.get(unit)
        if r is None:
            r = self.readers[unit] = self.p.reader(unit)
        return r

    def word(self, unit, lin):
        r = self.reader(unit)
        return r(lin) | (r((lin + 1) & 0xFFFFF) << 8)

    def callee_key(self, unit, cs, ip):
        lin = ((cs << 4) + ip) & 0xFFFFF
        u = self.p.unit_of(lin, unit)
        if u is None:
            return None
        return (u, cs, ip)

    # ------------------------------------------------------------ discovery
    def add_entry(self, key, why):
        self.entries.append((key, why))

    def run(self):
        work = deque()
        seen = set()

        def push(key, why):
            if key is None:
                return
            if key not in seen:
                seen.add(key)
                work.append((key, why))
        for k, why in self.entries:
            push(k, why)
        self.entry_set = {k for k, _ in self.entries}
        while work:
            key, why = work.popleft()
            try:
                f = self.discover(key)
            except (DecodeError, IndexError) as e:
                self.warnings.append("cannot translate %s (%s): %s" % (key_str(key), why, e))
                continue
            self.funcs[key] = f
            for ins, kind, ck in f.calls:
                if ck is not None:
                    push(ck, "called from %s" % f.name)
        for f in self.funcs.values():
            for ins, kind, ck in f.calls:
                if ck is not None and ck in self.funcs:
                    self.funcs[ck].callers.add(f.key)
        return self

    def discover(self, key):
        unit, cs, ip0 = key
        read = self.reader(unit)
        f = Func(key)
        leaders = {ip0}
        todo = [ip0]
        insns = f.insns
        succ = {}

        def add(ip):
            if ip not in leaders:
                leaders.add(ip)
            if ip not in insns:
                todo.append(ip)
        while todo:
            ip = todo.pop()
            while ip not in insns:
                ins = decode(read, cs, ip)
                insns[ip] = ins
                mn = ins.mn
                nxt = ins.next_ip()
                outs = []
                fall = True
                if mn == "jcc" or mn in ("loop", "loopz", "loopnz", "jcxz"):
                    outs = [ins.target, nxt]
                    add(ins.target)
                    add(nxt)
                elif mn == "jmp":
                    outs = [ins.target]
                    fall = False
                    add(ins.target)
                elif mn == "call":
                    if ins.target == nxt:
                        ins.ext = "puship"          # call $+3: push IP, continue
                    else:
                        f.calls.append((ins, "near", self.callee_key(unit, cs, ins.target)))
                elif mn == "callf":
                    tseg, toff = ins.target
                    if (tseg, toff) in (self.SW_DENSE, self.SW_SPARSE):
                        sw = self.parse_switch(unit, cs, nxt, (tseg, toff) == self.SW_DENSE)
                        f.switch[ip] = sw
                        ins.ext = "switch"
                        outs = sorted(set(sw["targets"]) | {sw["default"]})
                        for t in outs:
                            add(t)
                        fall = False
                    elif (tseg, toff) in self.p.thunk_at:
                        th = self.p.thunk_at[(tseg, toff)]
                        ins.ext = "thunk"
                        f.calls.append((ins, "thunk", (th["ovl"], th["tseg"], th["toff"])))
                    else:
                        f.calls.append((ins, "far", self.callee_key(unit, tseg, toff)))
                elif mn == "jmpf":
                    tseg, toff = ins.target
                    ck = self.callee_key(unit, tseg, toff)
                    if (tseg, toff) in self.p.thunk_at:
                        th = self.p.thunk_at[(tseg, toff)]
                        ins.ext = "thunk"
                        f.calls.append((ins, "tailthunk", (th["ovl"], th["tseg"], th["toff"])))
                    else:
                        f.calls.append((ins, "tail", ck))
                    fall = False
                elif mn in ("callind", "callfind", "jmpind", "jmpfind"):
                    tg = sorted(self.dyn_targets.get((unit, cs, ip), ()))
                    tg += self.hint_targets(unit, cs, ip)
                    tg = sorted(set(tg))
                    f.dyn[ip] = tg
                    if mn in ("callind", "callfind"):
                        f.has_dyn_call = True
                        for t in tg:
                            f.calls.append((ins, "dyncall", self.callee_key(unit, t[0], t[1])))
                    else:
                        fall = False
                        intra = [t for t in tg if t[0] == cs and mn == "jmpind"]
                        for t in tg:
                            if t[0] == cs and mn == "jmpind":
                                add(t[1])
                            else:
                                f.calls.append((ins, "dyntail", self.callee_key(unit, t[0], t[1])))
                        outs = sorted({t[1] for t in intra})
                elif mn in ("ret", "retf", "iret", "hlt"):
                    fall = False
                    if mn == "iret":
                        f.has_dyn_call = True
                elif mn == "int":
                    f.has_dyn_call = True
                if fall:
                    outs = list(outs) if outs else [nxt]
                    if nxt not in outs:
                        outs.append(nxt)
                succ[ip] = outs
                if not fall:
                    break
                if mn == "jcc" or mn in ("loop", "loopz", "loopnz", "jcxz"):
                    break
                ip = nxt
        # build blocks
        for lead in sorted(leaders):
            if lead not in insns:
                continue
            b = Block(lead)
            ip = lead
            while True:
                ins = insns[ip]
                b.insns.append(ins)
                outs = succ[ip]
                if ins.mn in x86.STOPS or ins.ext == "switch" or ins.mn in (
                        "jcc", "loop", "loopz", "loopnz", "jcxz") or ins.mn == "jmpf":
                    b.succs = outs
                    break
                nxt = ins.next_ip()
                if nxt in leaders:
                    b.succs = [nxt]
                    break
                ip = nxt
            f.blocks[lead] = b
        return f

    def hint_targets(self, unit, cs, ip):
        out = []
        for h in self.hints.get("jump_targets", []):
            hs, ho = (int(x, 16) for x in h["site"].split(":"))
            if (hs + self.base, ho) == (cs, ip):
                for t in h["targets"]:
                    ts, to = (int(x, 16) for x in t.split(":"))
                    out.append((ts + self.base, to))
        return out

    def parse_switch(self, unit, cs, tip, dense):
        lin = ((cs << 4) + tip) & 0xFFFFF
        w = lambda i: self.word(unit, (lin + 2 * i) & 0xFFFFF)  # noqa: E731
        if dense:
            lo, cnt, dflt = w(0), w(1), w(2)
            if cnt > 512:
                raise DecodeError("dense switch table too large at %04X:%04X" % (cs, tip))
            targets = [w(3 + i) for i in range(cnt)]
            return dict(kind="dense", table=tip, min=lo, count=cnt, default=dflt, targets=targets)
        n = w(0)
        if n > 512:
            raise DecodeError("sparse switch table too large at %04X:%04X" % (cs, tip))
        values = [w(1 + i) for i in range(n)]
        dflt = w(1 + n)
        targets = [w(2 + n + i) for i in range(n)]
        return dict(kind="sparse", table=tip, values=values, default=dflt, targets=targets, n=n)

    # ------------------------------------------------------------ hints
    def apply_ret_hints(self):
        """ret_via_jmp hints: a helper that returns with `jmp [bp+N]` (to its own
        return address) -> the jump's targets are the return addresses of every
        call to the helper.  Adds those targets and re-discovers affected functions."""
        changed = False
        callers = defaultdict(set)
        for f in self.funcs.values():
            for ins, kind, ck in f.calls:
                if kind == "near" and ck is not None:
                    callers[(ck[1], ck[2])].add((f.cs, ins.next_ip()))
        for h in self.hints.get("ret_via_jmp", []):
            hs, ho = (int(x, 16) for x in h["site"].split(":"))
            fs, fo = (int(x, 16) for x in h["helper"].split(":"))
            site = (hs + self.base, ho)
            helper = (fs + self.base, fo)
            for key, f in list(self.funcs.items()):
                if f.cs == site[0] and site[1] in f.insns:
                    dk = (f.unit, f.cs, site[1])
                    before = set(self.dyn_targets[dk])
                    self.dyn_targets[dk] |= callers.get(helper, set())
                    if self.dyn_targets[dk] != before:
                        changed = True
        return changed

    # ------------------------------------------------------------ registers
    def compute_regs(self):
        for f in self.funcs.values():
            m, w = set(), set()
            for ins in f.insns.values():
                rd, wr = insn_regs(ins)
                m |= rd | wr
                w |= wr
            # every function touches SP (return address) and may be entered with any stack
            m.add(4)
            w.add(4)
            f.mentioned = m
            f.written = w
            f.defs = set(w)
        # DEF* fixpoint
        changed = True
        while changed:
            changed = False
            for f in self.funcs.values():
                d = f.defs
                n0 = len(d)
                for ins, kind, ck in f.calls:
                    if kind in ("dyncall", "dyntail") or ck is None or ck not in self.funcs:
                        d |= ALL_REGS
                    else:
                        d |= self.funcs[ck].defs
                if f.has_dyn_call:
                    d |= ALL_REGS
                if len(d) != n0:
                    changed = True

    # ------------------------------------------------------------ flags
    def compute_flags(self):
        """Interprocedural flag liveness: fin[F] = flags read before written from F's
        entry; fout[F] = flags live after a return from F (union over call sites)."""
        for f in self.funcs.values():
            f.fin = 0
            f.fout = getattr(f, "fout_min", 0)
        changed = True
        it = 0
        while changed:
            changed = False
            it += 1
            for f in self.funcs.values():
                fin = self.flag_liveness(f)
                if fin != f.fin:
                    f.fin = fin
                    changed = True
            # propagate fout from call sites
            for f in self.funcs.values():
                for b in f.blocks.values():
                    live = b.live_out
                    for ins in reversed(b.insns):
                        live_after = live
                        live = self.flag_step(f, ins, live)
                        if ins.mn in ("call", "callf", "callind", "callfind") and ins.ext not in ("puship", "switch"):
                            for ck in self.call_targets(f, ins):
                                g = self.funcs.get(ck)
                                if g is not None and (g.fout | live_after) != g.fout:
                                    g.fout |= live_after
                                    changed = True
                        if ins.mn in ("jmpf", "jmpind", "jmpfind") and ins.ext != "switch":
                            # tail transfer: callee returns to our caller
                            for ck in self.call_targets(f, ins):
                                g = self.funcs.get(ck)
                                if g is not None and (g.fout | f.fout) != g.fout:
                                    g.fout |= f.fout
                                    changed = True
            if it > 200:
                raise RuntimeError("flag liveness does not converge")
        return it

    def call_targets(self, f, ins):
        out = []
        for i2, kind, ck in f.calls:
            if i2 is ins and ck is not None:
                out.append(ck)
        return out

    def flag_step(self, f, ins, live):
        """live-before from live-after for one instruction."""
        mn = ins.mn
        if mn in ("call", "callf", "callind", "callfind") and ins.ext not in ("puship", "switch"):
            tg = self.call_targets(f, ins)
            if mn in ("callind", "callfind") or not tg or any(t not in self.funcs for t in tg):
                return ALL
            fin = 0
            for t in tg:
                fin |= self.funcs[t].fin
            # the callee defines (or passes through) every flag we read later
            return fin
        if ins.ext == "switch":
            return 0          # the helper ends with flag-setting instructions
        if mn in ("ret", "retf"):
            return f.fout
        if mn == "iret":
            return 0
        if mn in ("jmpf", "jmpind", "jmpfind"):
            tg = self.call_targets(f, ins)
            if mn != "jmpf":
                intra = [t for t in f.dyn.get(ins.ip, []) if t[0] == f.cs and mn == "jmpind"]
                if not tg and intra:
                    return live
            if not tg or any(t not in self.funcs for t in tg) or (mn != "jmpf" and not f.dyn.get(ins.ip)):
                return ALL
            fin = 0
            for t in tg:
                fin |= self.funcs[t].fin
            if mn == "jmpind":
                fin |= live
            return fin
        if mn == "int" or mn == "into":
            return ALL
        if mn == "hlt":
            return 0
        return (live & ~ins.writes) | ins.reads

    def flag_liveness(self, f):
        blocks = f.blocks
        for b in blocks.values():
            b.live_in = 0
            b.live_out = 0
        changed = True
        order = sorted(blocks, reverse=True)
        while changed:
            changed = False
            for ip in order:
                b = blocks[ip]
                out = 0
                for s in b.succs:
                    sb = blocks.get(s)
                    if sb is not None:
                        out |= sb.live_in
                live = out
                for ins in reversed(b.insns):
                    live = self.flag_step(f, ins, live)
                if out != b.live_out or live != b.live_in:
                    b.live_out = out
                    b.live_in = live
                    changed = True
        return blocks[f.ip].live_in if f.ip in blocks else ALL


def insn_regs(ins):
    """(read, written) register-id sets of one instruction, including implicit operands."""
    rd, wr = set(), set()
    mn = ins.mn

    def opnd(o, write, read=True):
        if o[0] == "r":
            idx = o[2] if o[1] else (o[2] & 3)
            if read:
                rd.add(idx)
            if write:
                wr.add(idx)
                if not o[1]:
                    rd.add(idx)         # partial write keeps the other half
        elif o[0] == "s":
            if o[1] != 1:
                sid = SEG_RID[o[1]]
                if read:
                    rd.add(sid)
                if write:
                    wr.add(sid)
        elif o[0] == "m":
            for r in o[3]:
                rd.add(r)
            if o[2] != 1:
                rd.add(SEG_RID[o[2]])
    ops = ins.ops
    for i, o in enumerate(ops):
        dst = i == 0 and mn not in ("cmp", "test", "push", "out", "jmpind", "callind", "jmpfind", "callfind")
        is_read = not (i == 0 and mn in ("mov", "lea", "pop", "les", "lds", "in"))
        if mn == "lea" and i == 1:
            for r in o[3]:
                rd.add(r)
            continue
        if mn in ("xchg",):
            opnd(o, True, True)
            continue
        opnd(o, dst, is_read)
    if mn in ("push", "pop", "pushf", "popf", "call", "callf", "callind", "callfind", "ret", "retf",
              "iret", "int", "into", "pusha", "popa", "enter", "leave"):
        rd.update((4, R_SS))
        wr.add(4)
    if mn in ("les",):
        wr.add(R_ES)
    if mn in ("lds",):
        wr.add(R_DS)
    if mn in ("movs", "cmps", "stos", "lods", "scas"):
        if mn in ("movs", "cmps", "lods"):
            rd.add(6)
            wr.add(6)
            rd.add(SEG_RID.get(ins.seg if ins.seg is not None else 3, 11) if (ins.seg if ins.seg is not None else 3) != 1 else 11)
        if mn in ("movs", "cmps", "stos", "scas"):
            rd.update((7, R_ES))
            wr.add(7)
        if mn in ("stos", "scas"):
            rd.add(0)
        if mn == "lods":
            rd.add(0)
            wr.add(0)
        if ins.rep:
            rd.add(1)
            wr.add(1)
    if mn in ("mul", "imul", "div", "idiv"):
        rd.add(0)
        wr.add(0)
        if ins.w or mn in ("div", "idiv"):
            if ins.w:
                rd.add(2)
                wr.add(2)
    if mn in ("cbw", "daa", "das", "aaa", "aas", "aam", "aad", "salc", "lahf", "sahf"):
        rd.add(0)
        if mn != "sahf":
            wr.add(0)
    if mn == "cwd":
        rd.add(0)
        wr.add(2)
    if mn == "xlat":
        rd.update((0, 3, SEG_RID.get(ins.seg if ins.seg is not None else 3, 11) if ins.seg != 1 else 11))
        wr.add(0)
    if mn in ("loop", "loopz", "loopnz"):
        rd.add(1)
        wr.add(1)
    if mn == "jcxz":
        rd.add(1)
    if mn in ("pusha", "popa"):
        rd.update(range(8))
        if mn == "popa":
            wr.update(range(8))
    if mn in ("enter", "leave"):
        rd.add(5)
        wr.add(5)
    if mn in ("in", "out"):
        for o in ops:
            if o[0] == "r":
                rd.add(o[2] if o[1] else o[2] & 3)
    if ins.ext == "switch":
        wr.update((0, 1, 3, 7, R_ES))
        rd.update((0, 3, 11))
    return rd, wr


def load_json(path):
    with open(path) as f:
        return json.load(f)
