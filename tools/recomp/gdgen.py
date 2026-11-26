"""GDScript backend: one function per discovered 8086 function.

Conventions of the generated code (see docs/RECOMPILATION.md):

* Registers are typed int locals (ax cx dx bx sp bp si di es ss ds); CS is a
  constant of the function.  The shared CpuState object ``st`` holds the
  authoritative copy between functions: a function loads the registers it
  mentions on entry, spills them before every call and on return, and reloads
  after a call the registers the callee may change (interprocedural DEF set).
* Flags are int locals (cf pf af zf sf of), computed only where liveness says
  they are read.  Flags that cross a call or return go through st.
* Memory is the 1 MB PackedByteArray ``m`` (shared by reference with the host).
* Basic blocks are sequential ``if b == IP:`` tests inside ``while true:``
  (chunked for big functions), so fall-through costs one comparison and any
  intra-function jump works.  Backward jumps ``continue`` and count a poll.
* Every function returns the CS:IP its RET popped, packed as (cs << 16) | ip.
  A caller compares it with the address after the call; anything else is
  propagated upward (stack unwinding) and ABORT (-1) ends the program.
"""
import x86
from analysis import ALL_REGS, R_DS, R_ES, R_SS, REGS16, SEG_RID

CF, PF, AF, ZF, SF, OF = x86.CF, x86.PF, x86.AF, x86.ZF, x86.SF, x86.OF
ALL = x86.ALL
FLAG_NAMES = [(CF, "cf"), (PF, "pf"), (AF, "af"), (ZF, "zf"), (SF, "sf"), (OF, "of")]
R8 = ["(ax & 0xFF)", "(cx & 0xFF)", "(dx & 0xFF)", "(bx & 0xFF)",
      "(ax >> 8)", "(cx >> 8)", "(dx >> 8)", "(bx >> 8)"]
SEGNAMES = {0: "es", 2: "ss", 3: "ds"}
RID_NAME = {i: REGS16[i] for i in range(8)}
RID_NAME.update({R_ES: "es", R_SS: "ss", R_DS: "ds"})
RID_SPILL = {i: "r[%d]" % i for i in range(8)}
RID_SPILL.update({R_ES: "sg[0]", R_SS: "sg[2]", R_DS: "sg[3]"})
CHUNK = 12
COMMENTS = False
ABORT = -1


def h(v):
    return "0x%X" % v


class GenError(Exception):
    pass


class FuncGen:
    def __init__(self, an, f, unit_ref):
        self.an = an
        self.f = f
        self.cs = f.cs
        self.unit_ref = unit_ref      # function key -> GDScript call prefix ("" or "u_xx.")
        self.lines = []
        self.ind = 0
        self.uses_par = False
        self.mentioned = sorted(f.mentioned)

    # ------------------------------------------------------------ emit
    def o(self, s):
        self.lines.append("\t" * self.ind + s)

    # ------------------------------------------------------------ operands
    def seg_base(self, s):
        if s == 1:
            return h(self.cs << 4)
        return "(%s << 4)" % SEGNAMES[s]

    def offset(self, base, disp):
        parts = [REGS16[r] for r in base]
        if not parts:
            return h(disp)
        e = " + ".join(parts)
        if disp:
            e += " + " + h(disp)
        return "((%s) & 0xFFFF)" % e if (len(parts) > 1 or disp) else e

    def addr(self, op):
        """Emit `a = linear address` for a memory operand; return 'a'."""
        _, w, s, base, disp = op
        off = self.offset(base, disp)
        if s == 1 and not base:
            self.o("a = %s" % h(((self.cs << 4) + disp) & 0xFFFFF))
        else:
            self.o("a = (%s + %s) & 0xFFFFF" % (self.seg_base(s), off))
        return "a"

    def rd(self, op, w=None):
        """Expression for reading an operand (memory: emits `a = ...` first)."""
        k = op[0]
        if k == "i":
            return h(op[1])
        if k == "r":
            return REGS16[op[2]] if op[1] else R8[op[2]]
        if k == "s":
            return h(self.cs) if op[1] == 1 else SEGNAMES[op[1]]
        a = self.addr(op)
        if op[1]:
            return "(m[%s] | (m[%s + 1] << 8))" % (a, a)
        return "m[%s]" % a

    def rd_mem_again(self, op):
        """Read a memory operand whose address is already in `a`."""
        if op[1]:
            return "(m[a] | (m[a + 1] << 8))"
        return "m[a]"

    def wr(self, op, e, have_addr=False, masked=True):
        """Emit a write of expression e (already masked to the operand size)."""
        k = op[0]
        if k == "r":
            i = op[2]
            if op[1]:
                self.o("%s = %s" % (REGS16[i], e))
            elif i < 4:
                self.o("%s = (%s & 0xFF00) | (%s)" % (REGS16[i], REGS16[i], e))
            else:
                self.o("%s = (%s & 0xFF) | ((%s) << 8)" % (REGS16[i - 4], REGS16[i - 4], e))
            return
        if k == "s":
            if op[1] == 1:
                raise GenError("write to CS")
            self.o("%s = %s" % (SEGNAMES[op[1]], e))
            return
        if not have_addr:
            self.addr(op)
        if op[1]:
            self.o("t = %s" % e)
            self.o("m[a] = t & 0xFF")
            self.o("m[a + 1] = t >> 8")
        else:
            self.o("m[a] = %s" % e)

    def simple(self, e):
        """True if expression e can be evaluated twice without side effects/cost."""
        return e.replace("_", "").isalnum() or e.startswith("0x") or e.startswith("(ax ") or e.startswith(
            "(cx ") or e.startswith("(dx ") or e.startswith("(bx ")

    # ------------------------------------------------------------ flags
    def szp(self, res, w, need):
        if need & ZF:
            self.o("zf = 1 if %s == 0 else 0" % res)
        if need & SF:
            self.o("sf = %s >> %d" % (res, 15 if w else 7))
        if need & PF:
            self.uses_par = True
            self.o("pf = par[%s & 0xFF]" % res if w else "pf = par[%s]" % res)

    def flagword(self):
        return ("(cf | 2 | (pf << 2) | (af << 4) | (zf << 6) | (sf << 7) | (st.tf << 8) | "
                "(st.iflag << 9) | (st.df << 10) | (of << 11) | 0xF000)")

    def set_flags_from(self, e):
        self.o("t = %s" % e)
        self.o("cf = t & 1")
        self.o("pf = (t >> 2) & 1")
        self.o("af = (t >> 4) & 1")
        self.o("zf = (t >> 6) & 1")
        self.o("sf = (t >> 7) & 1")
        self.o("of = (t >> 11) & 1")
        self.o("st.tf = (t >> 8) & 1")
        self.o("st.iflag = (t >> 9) & 1")
        self.o("st.df = (t >> 10) & 1")

    # ------------------------------------------------------------ stack
    def push(self, e):
        self.o("sp = (sp - 2) & 0xFFFF")
        if e != "t":
            self.o("t = %s" % e)
        self.o("a = (ss << 4) + sp")
        self.o("m[a] = t & 0xFF")
        self.o("m[a + 1] = t >> 8")

    def pop(self, dst="t"):
        self.o("a = (ss << 4) + sp")
        self.o("%s = m[a] | (m[a + 1] << 8)" % dst)
        self.o("sp = (sp + 2) & 0xFFFF")

    # ------------------------------------------------------------ calls
    def spill(self, regs=None, flags=0):
        self.o("st.eb = pl")
        for rid in self.mentioned if regs is None else regs:
            self.o("%s = %s" % (RID_SPILL[rid], RID_NAME[rid]))
        for bit, n in FLAG_NAMES:
            if flags & bit:
                self.o("st.%s = %s" % (n, n))

    def reload(self, regs, flags):
        self.o("pl = st.eb")
        for rid in self.mentioned:
            if rid in regs:
                self.o("%s = %s" % (RID_NAME[rid], RID_SPILL[rid]))
        for bit, n in FLAG_NAMES:
            if flags & bit:
                self.o("%s = st.%s" % (n, n))

    def callee_expr(self, ck):
        g = self.an.funcs.get(ck)
        if g is None:
            return None, None
        return "%s%s()" % (self.unit_ref(g), g.name), g

    def emit_call(self, ins, live_after, expected, call_expr, g):
        fin = ALL if g is None else g.fin
        defs = ALL_REGS if g is None else g.defs
        self.spill(None, fin)
        self.o("t = %s" % call_expr)
        self.o("if t != %s:" % h(expected))
        self.o("\treturn host.unwind(t, %s)" % h((ins.cs << 16) | ins.ip))
        self.reload(defs | {4}, live_after)

    # ------------------------------------------------------------ main
    def gen(self):
        f = self.f
        o = self.o
        o('@warning_ignore("unused_variable", "integer_division")')
        o("func %s() -> int:" % f.name)
        self.ind = 1
        body_start = len(self.lines)
        blocks = [f.blocks[ip] for ip in sorted(f.blocks)]
        # body first (to learn what is used), prologue inserted afterwards
        self.ind = 1
        o("while true:")
        self.ind = 2
        chunks = [blocks[i:i + CHUNK] for i in range(0, len(blocks), CHUNK)]
        multi = len(chunks) > 1
        for ci, ch in enumerate(chunks):
            if multi:
                end = chunks[ci + 1][0].ip if ci + 1 < len(chunks) else 0x10000
                o("if b < %s:" % h(end))
                self.ind = 3
            for bl in ch:
                o("if b == %s:" % h(bl.ip))
                self.ind += 1
                self.gen_block(bl)
                self.ind -= 1
            if multi:
                self.ind = 2
        body = self.lines[body_start:]
        del self.lines[body_start:]
        self.ind = 1
        o("var r: PackedInt32Array = st.r")
        o("var sg: PackedInt32Array = st.sr")
        o("var m: PackedByteArray = mem")
        for rid in self.mentioned:
            o("var %s: int = %s" % (RID_NAME[rid], RID_SPILL[rid]))
        for bit, n in FLAG_NAMES:
            if f.fin & bit:
                o("var %s: int = st.%s" % (n, n))
            else:
                o("var %s: int = 0" % n)
        o("var a: int = 0")
        o("var t: int = 0")
        o("var t2: int = 0")
        o("var t3: int = 0")
        o("var t4: int = 0")
        o("var pl: int = st.eb")
        o("var b: int = %s" % h(f.ip))
        self.lines.extend(body)
        self.ind = 1
        o("return %d" % ABORT)
        # silence unused-variable analysis of GDScript: nothing needed (all are used or harmless)
        return self.lines

    def gen_block(self, bl):
        f = self.f
        # per-instruction live-after flags
        lives = []
        live = bl.live_out
        for ins in reversed(bl.insns):
            lives.append(live)
            live = self.an.flag_step(f, ins, live)
        lives.reverse()
        n = len(bl.insns)
        i = 0
        while i < n:
            ins = bl.insns[i]
            la = lives[i]
            nxt = bl.insns[i + 1] if i + 1 < n else None
            if nxt is not None and nxt.mn == "jcc" and i + 1 == n - 1:
                cond = self.fused(ins, nxt, la, lives[i + 1])
                if cond is not None:
                    self.branch(bl, nxt, cond)
                    return
            if COMMENTS:
                self.o("# %04X:%04X %s" % (ins.cs, ins.ip, ins.mn))
            done = self.insn(ins, la, bl)
            if done:
                return
            i += 1
        # fell off the end of the block (next instruction is a leader)
        if bl.succs:
            self.goto(bl, bl.succs[0])
        else:
            self.o("return host.fatal(%s, \"fell off block\")" % h((self.cs << 16) | bl.ip))

    def goto(self, bl, target, force=False, count=False):
        """Jump to block `target`.  A backward conditional or direct jump
        (count=True) is a *back-edge*: it spends one unit of the poll budget
        `pl` (st.eb between functions); when the budget runs out the full
        state is stored and host.edge_poll() runs: the clock, a hooked timer
        interrupt, a stop request.  Multi-way jumps (switch, jmp reg) do not
        count."""
        o = self.o
        if target not in self.f.blocks:
            o("return host.fatal(%s, \"reached code the translator could not decode\")"
              % h((self.cs << 16) | target))
            return
        o("b = %s" % h(target))
        if target <= bl.ip:
            if count:
                o("pl -= 1")
                o("if pl <= 0:")
                self.ind += 1
                self.spill(None, ALL)
                o("if host.edge_poll(%s) < 0:" % h((self.cs << 16) | target))
                o("\treturn %d" % ABORT)
                self.reload(ALL_REGS, ALL)
                self.ind -= 1
            o("continue")
        elif force:
            o("continue")

    def branch(self, bl, ins, cond):
        nip = ins.next_ip()
        self.o("if %s:" % cond)
        self.ind += 1
        self.goto(bl, ins.target, count=True)
        self.ind -= 1
        self.o("else:")
        self.ind += 1
        self.goto(bl, nip)
        self.ind -= 1

    # ------------------------------------------------------------ fused cmp/jcc
    def fused(self, ins, j, la, lj):
        """Condition expression for `ins` followed by `jcc j` when the flags ins
        writes are dead after j; emits the instruction's side effects first."""
        mn = ins.mn
        if mn not in ("cmp", "sub", "test", "and", "or", "xor", "inc", "dec", "add"):
            return None
        if lj & ins.writes:
            return None
        c = j.cond
        w = ins.w
        sb = 0x8000 if w else 0x80
        mask = 0xFFFF if w else 0xFF
        if COMMENTS:
            self.o("# %04X:%04X %s (fused with %s)" % (ins.cs, ins.ip, mn, x86.JCC_NAMES[c]))
        if mn in ("cmp", "sub"):
            if c in (0, 1, 10, 11):
                return None
            dst, src = ins.ops
            av = self.rd(dst)
            if dst[0] == "m":
                self.o("t2 = %s" % av)
                av = "t2"
            elif not self.simple(av):
                self.o("t2 = %s" % av)
                av = "t2"
            bv = self.rd(src) if src[0] != "m" or dst[0] != "m" else None
            if src[0] == "m":
                self.o("t3 = %s" % bv)
                bv = "t3"
            if mn == "sub":
                if av not in ("t2",):
                    self.o("t2 = %s" % av)
                    av = "t2"
                if not (bv.startswith("0x") or bv == "t3"):
                    self.o("t3 = %s" % bv)
                    bv = "t3"
                self.wr(dst, "(t2 - %s) & %s" % (bv, h(mask)), have_addr=dst[0] == "m")
            return self.cmp_cond(c, av, bv, sb)
        if mn in ("test", "and", "or", "xor"):
            if c in (10, 11):
                return None
            dst, src = ins.ops
            av = self.rd(dst)
            if dst[0] == "m" or not self.simple(av):
                self.o("t2 = %s" % av)
                av = "t2"
            bv = self.rd(src)
            if mn == "test":
                if bv == av:
                    self.o("t = %s" % av)
                else:
                    self.o("t = %s & %s" % (av, bv))
            else:
                opc = {"and": "&", "or": "|", "xor": "^"}[mn]
                self.o("t = %s %s %s" % (av, opc, bv))
                self.wr(dst, "t", have_addr=dst[0] == "m")
            return self.logic_cond(c, "t", sb)
        if mn in ("inc", "dec", "add"):
            if c not in (4, 5, 8, 9):
                return None
            if mn == "add":
                if c in (8, 9) or True:
                    dst, src = ins.ops
                    av = self.rd(dst)
                    if dst[0] == "m" or not self.simple(av):
                        self.o("t2 = %s" % av)
                        av = "t2"
                    bv = self.rd(src)
                    self.o("t = (%s + %s) & %s" % (av, bv, h(mask)))
                    self.wr(dst, "t", have_addr=dst[0] == "m")
            else:
                dst = ins.ops[0]
                av = self.rd(dst)
                self.o("t = (%s %s 1) & %s" % (av, "+" if mn == "inc" else "-", h(mask)))
                self.wr(dst, "t", have_addr=dst[0] == "m")
            if c == 4:
                return "t == 0"
            if c == 5:
                return "t != 0"
            if c == 8:
                return "(t & %s) != 0" % h(sb)
            return "(t & %s) == 0" % h(sb)
        return None

    @staticmethod
    def cmp_cond(c, a, b, sb):
        if c == 2:
            return "%s < %s" % (a, b)
        if c == 3:
            return "%s >= %s" % (a, b)
        if c == 4:
            return "%s == %s" % (a, b)
        if c == 5:
            return "%s != %s" % (a, b)
        if c == 6:
            return "%s <= %s" % (a, b)
        if c == 7:
            return "%s > %s" % (a, b)
        if c == 8:
            return "((%s - %s) & %s) != 0" % (a, b, h(sb))
        if c == 9:
            return "((%s - %s) & %s) == 0" % (a, b, h(sb))
        sa = "(%s ^ %s)" % (a, h(sb))
        sbv = "(%s ^ %s)" % (b, h(sb))
        return {12: "%s < %s", 13: "%s >= %s", 14: "%s <= %s", 15: "%s > %s"}[c] % (sa, sbv)

    @staticmethod
    def logic_cond(c, r, sb):
        neg = "(%s & %s) != 0" % (r, h(sb))
        pos = "(%s & %s) == 0" % (r, h(sb))
        return {0: "false", 1: "true", 2: "false", 3: "true", 4: "%s == 0" % r, 5: "%s != 0" % r,
                6: "%s == 0" % r, 7: "%s != 0" % r, 8: neg, 9: pos,
                12: neg, 13: pos, 14: "%s == 0 or %s" % (r, neg), 15: "%s != 0 and %s" % (r, pos)}.get(c)

    @staticmethod
    def jcc_cond(c):
        return ["of != 0", "of == 0", "cf != 0", "cf == 0", "zf != 0", "zf == 0",
                "(cf | zf) != 0", "(cf | zf) == 0", "sf != 0", "sf == 0", "pf != 0", "pf == 0",
                "sf != of", "sf == of", "zf != 0 or sf != of", "zf == 0 and sf == of"][c]

    # ------------------------------------------------------------ instructions
    def insn(self, ins, la, bl):
        """Emit one instruction. Returns True if it ended the block."""
        mn = ins.mn
        fn = getattr(self, "i_" + mn, None)
        if fn is None:
            raise GenError("no codegen for %s at %04X:%04X" % (mn, ins.cs, ins.ip))
        return fn(ins, la, bl)

    def alu(self, op, w, av, bv, need, res="t"):
        """res = av OP bv with flags `need`; av/bv are simple expressions."""
        mask = 0xFFFF if w else 0xFF
        top = 16 if w else 8
        msb = 15 if w else 7
        o = self.o
        if op in ("add", "adc"):
            extra = " + cf" if op == "adc" else ""
            if need & CF:
                o("t4 = %s + %s%s" % (av, bv, extra))
                o("cf = t4 >> %d" % top)
                o("%s = t4 & %s" % (res, h(mask)))
            else:
                o("%s = (%s + %s%s) & %s" % (res, av, bv, extra, h(mask)))
            if need & OF:
                o("of = ((%s ^ %s) & (%s ^ %s)) >> %d" % (av, res, bv, res, msb))
            if need & AF:
                o("af = ((%s ^ %s ^ %s) >> 4) & 1" % (av, bv, res))
        elif op in ("sub", "cmp", "sbb", "neg"):
            extra = " - cf" if op == "sbb" else ""
            if need & CF:
                o("t4 = %s - %s%s" % (av, bv, extra))
                o("cf = 1 if t4 < 0 else 0")
                o("%s = t4 & %s" % (res, h(mask)))
            else:
                o("%s = (%s - %s%s) & %s" % (res, av, bv, extra, h(mask)))
            if need & OF:
                o("of = ((%s ^ %s) & (%s ^ %s)) >> %d" % (av, bv, av, res, msb))
            if need & AF:
                o("af = ((%s ^ %s ^ %s) >> 4) & 1" % (av, bv, res))
        else:
            opc = {"and": "&", "or": "|", "xor": "^", "test": "&"}[op]
            o("%s = %s %s %s" % (res, av, opc, bv))
            if need & CF:
                o("cf = 0")
            if need & OF:
                o("of = 0")
            if need & AF:
                o("af = 0")
        self.szp(res, w, need)

    def binop(self, ins, la, name):
        dst, src = ins.ops
        w = ins.w
        need = ins.writes & la
        av = self.rd(dst)
        if dst[0] == "m" or not self.simple(av):
            self.o("t2 = %s" % av)
            av = "t2"
        bv = self.rd(src)
        if not self.simple(bv) or bv == av:
            if bv != av:
                self.o("t3 = %s" % bv)
                bv = "t3"
        if name in ("xor", "sub") and dst == src and dst[0] == "r":
            # xor r,r / sub r,r
            self.o("t = 0")
            if need & CF:
                self.o("cf = 0")
            if need & OF:
                self.o("of = 0")
            if need & AF:
                self.o("af = 0")
            self.szp("t", w, need)
        else:
            self.alu(name, w, av, bv, need)
        if name not in ("cmp", "test"):
            self.wr(dst, "t", have_addr=dst[0] == "m")

    def i_add(self, ins, la, bl):
        self.binop(ins, la, "add")

    def i_or(self, ins, la, bl):
        self.binop(ins, la, "or")

    def i_adc(self, ins, la, bl):
        self.binop(ins, la, "adc")

    def i_sbb(self, ins, la, bl):
        self.binop(ins, la, "sbb")

    def i_and(self, ins, la, bl):
        self.binop(ins, la, "and")

    def i_sub(self, ins, la, bl):
        self.binop(ins, la, "sub")

    def i_xor(self, ins, la, bl):
        self.binop(ins, la, "xor")

    def i_cmp(self, ins, la, bl):
        self.binop(ins, la, "cmp")

    def i_test(self, ins, la, bl):
        self.binop(ins, la, "test")

    def i_neg(self, ins, la, bl):
        dst = ins.ops[0]
        need = ins.writes & la
        av = self.rd(dst)
        self.o("t2 = %s" % av)
        self.alu("neg", ins.w, "0", "t2", need)
        self.wr(dst, "t", have_addr=dst[0] == "m")

    def i_not(self, ins, la, bl):
        dst = ins.ops[0]
        av = self.rd(dst)
        self.wr(dst, "%s ^ %s" % (av, "0xFFFF" if ins.w else "0xFF"), have_addr=dst[0] == "m")

    def i_inc(self, ins, la, bl):
        self.incdec(ins, la, 1)

    def i_dec(self, ins, la, bl):
        self.incdec(ins, la, -1)

    def incdec(self, ins, la, d):
        dst = ins.ops[0]
        w = ins.w
        need = ins.writes & la
        mask = 0xFFFF if w else 0xFF
        av = self.rd(dst)
        self.o("t = (%s %s 1) & %s" % (av, "+" if d > 0 else "-", h(mask)))
        if need & OF:
            v = (0x8000 if w else 0x80) if d > 0 else (0x7FFF if w else 0x7F)
            self.o("of = 1 if t == %s else 0" % h(v))
        if need & AF:
            self.o("af = 1 if (t & 0x0F) == %s else 0" % ("0" if d > 0 else "0x0F"))
        self.szp("t", w, need)
        self.wr(dst, "t", have_addr=dst[0] == "m")

    def i_mov(self, ins, la, bl):
        dst, src = ins.ops
        e = self.rd(src)
        if src[0] == "m" and dst[0] == "m":
            raise GenError("mem-mem mov")
        self.wr(dst, e)

    def i_lea(self, ins, la, bl):
        dst, src = ins.ops
        self.wr(dst, self.offset(src[3], src[4]))

    def i_xchg(self, ins, la, bl):
        a_, b_ = ins.ops
        if a_ == b_:
            return
        av = self.rd(a_)
        self.o("t2 = %s" % av)
        bv = self.rd(b_)
        self.o("t3 = %s" % bv)
        if a_[0] == "m":
            self.addr(a_)
        self.wr(a_, "t3", have_addr=a_[0] == "m")
        self.wr(b_, "t2", have_addr=False)

    def i_les(self, ins, la, bl):
        self.lxs(ins, "es")

    def i_lds(self, ins, la, bl):
        self.lxs(ins, "ds")

    def lxs(self, ins, seg):
        dst, src = ins.ops
        _, w, s, base, disp = src
        off = self.offset(base, disp)
        self.o("t2 = %s" % off)
        sbase = self.seg_base(s)
        self.o("a = (%s + t2) & 0xFFFFF" % sbase)
        self.o("t3 = m[a] | (m[a + 1] << 8)")
        self.o("a = (%s + ((t2 + 2) & 0xFFFF)) & 0xFFFFF" % sbase)
        self.o("%s = m[a] | (m[a + 1] << 8)" % seg)
        self.wr(dst, "t3")

    def i_push(self, ins, la, bl):
        src = ins.ops[0]
        if src == ("r", 1, 4):
            self.push("(sp - 2) & 0xFFFF")
            return
        e = self.rd(src)
        self.push(e)

    def i_pop(self, ins, la, bl):
        dst = ins.ops[0]
        if dst[0] == "m":
            self.pop("t3")
            self.wr(dst, "t3")
        elif dst == ("r", 1, 4):
            self.a_pop_sp()
        else:
            self.pop("t")
            self.wr(dst, "t")

    def a_pop_sp(self):
        self.o("a = (ss << 4) + sp")
        self.o("sp = m[a] | (m[a + 1] << 8)")

    def i_pushf(self, ins, la, bl):
        self.push(self.flagword())

    def i_popf(self, ins, la, bl):
        self.pop("t3")
        self.set_flags_from("t3")

    def i_sahf(self, ins, la, bl):
        self.o("t = ax >> 8")
        self.o("cf = t & 1")
        self.o("pf = (t >> 2) & 1")
        self.o("af = (t >> 4) & 1")
        self.o("zf = (t >> 6) & 1")
        self.o("sf = (t >> 7) & 1")

    def i_lahf(self, ins, la, bl):
        self.o("ax = (ax & 0xFF) | ((cf | 2 | (pf << 2) | (af << 4) | (zf << 6) | (sf << 7)) << 8)")

    def i_cbw(self, ins, la, bl):
        self.o("ax = (ax & 0xFF) | 0xFF00 if (ax & 0x80) != 0 else (ax & 0xFF)")

    def i_cwd(self, ins, la, bl):
        self.o("dx = 0xFFFF if (ax & 0x8000) != 0 else 0")

    def i_nop(self, ins, la, bl):
        pass

    def i_esc(self, ins, la, bl):
        pass

    def i_clc(self, ins, la, bl):
        self.o("cf = 0")

    def i_stc(self, ins, la, bl):
        self.o("cf = 1")

    def i_cmc(self, ins, la, bl):
        self.o("cf ^= 1")

    def i_cld(self, ins, la, bl):
        self.o("st.df = 0")

    def i_std(self, ins, la, bl):
        self.o("st.df = 1")

    def i_cli(self, ins, la, bl):
        self.o("st.iflag = 0")

    def i_sti(self, ins, la, bl):
        self.o("st.iflag = 1")

    def i_salc(self, ins, la, bl):
        self.o("ax = (ax & 0xFF00) | (0xFF if cf != 0 else 0)")

    def i_xlat(self, ins, la, bl):
        s = 3 if ins.seg is None else ins.seg
        self.o("a = (%s + ((bx + (ax & 0xFF)) & 0xFFFF)) & 0xFFFFF" % self.seg_base(s))
        self.o("ax = (ax & 0xFF00) | m[a]")

    def i_daa(self, ins, la, bl):
        self.daa_das("+")

    def i_das(self, ins, la, bl):
        self.daa_das("-")

    def daa_das(self, sign):
        o = self.o
        o("t = ax & 0xFF")
        o("t2 = t")
        o("t3 = cf")
        o("if (t & 0x0F) > 9 or af != 0:")
        o("\tt = t %s 6" % sign)
        o("\taf = 1")
        o("else:")
        o("\taf = 0")
        o("if t2 > 0x99 or t3 != 0:")
        o("\tt = t %s 0x60" % sign)
        o("\tcf = 1")
        o("else:")
        o("\tcf = 0")
        o("t = t & 0xFF")
        o("ax = (ax & 0xFF00) | t")
        self.szp("t", 0, ZF | SF | PF)

    def i_aaa(self, ins, la, bl):
        o = self.o
        o("if (ax & 0x0F) > 9 or af != 0:")
        o("\tax = (ax + 0x106) & 0xFFFF")
        o("\taf = 1")
        o("\tcf = 1")
        o("else:")
        o("\taf = 0")
        o("\tcf = 0")
        o("ax = ax & 0xFF0F")
        o("t = ax & 0xFF")
        self.szp("t", 0, ZF | SF | PF)

    def i_aas(self, ins, la, bl):
        o = self.o
        o("if (ax & 0x0F) > 9 or af != 0:")
        o("\tax = ((((ax >> 8) - 1) & 0xFF) << 8) | (((ax & 0xFF) - 6) & 0xFF)")
        o("\taf = 1")
        o("\tcf = 1")
        o("else:")
        o("\taf = 0")
        o("\tcf = 0")
        o("ax = ax & 0xFF0F")
        o("t = ax & 0xFF")
        self.szp("t", 0, ZF | SF | PF)

    def i_aam(self, ins, la, bl):
        base = ins.ops[0][1]
        if base == 0:
            self.div_fault(ins)
            return True
        self.o("t = ax & 0xFF")
        self.o("ax = ((t / %d) << 8) | (t %% %d)" % (base, base))
        self.o("t = ax & 0xFF")
        self.szp("t", 0, ins.writes & la)

    def i_aad(self, ins, la, bl):
        base = ins.ops[0][1]
        self.o("ax = (((ax >> 8) * %d) + (ax & 0xFF)) & 0xFF" % base)
        self.szp("ax", 0, ins.writes & la)

    # ---- multiply / divide
    def i_mul(self, ins, la, bl):
        need = ins.writes & la
        v = self.rd(ins.ops[0])
        if ins.w:
            self.o("t = ax * %s" % v)
            self.o("ax = t & 0xFFFF")
            self.o("dx = t >> 16")
            if need & (CF | OF):
                self.o("cf = 1 if dx != 0 else 0")
                self.o("of = cf")
            self.szp("ax", 1, need)
        else:
            self.o("ax = (ax & 0xFF) * %s" % v)
            if need & (CF | OF):
                self.o("cf = 1 if (ax & 0xFF00) != 0 else 0")
                self.o("of = cf")
            self.o("t = ax & 0xFF")
            self.szp("t", 0, need)

    def i_imul(self, ins, la, bl):
        need = ins.writes & la
        v = self.rd(ins.ops[0])
        if ins.w:
            self.o("t2 = %s" % v)
            self.o("t = (ax - 0x10000 if (ax & 0x8000) != 0 else ax) * "
                   "(t2 - 0x10000 if (t2 & 0x8000) != 0 else t2)")
            if need & (CF | OF):
                self.o("cf = 0 if (t >= -0x8000 and t <= 0x7FFF) else 1")
                self.o("of = cf")
            self.o("ax = t & 0xFFFF")
            self.o("dx = (t >> 16) & 0xFFFF")
            self.szp("ax", 1, need)
        else:
            self.o("t2 = %s" % v)
            self.o("t3 = ax & 0xFF")
            self.o("t = (t3 - 256 if (t3 & 0x80) != 0 else t3) * (t2 - 256 if (t2 & 0x80) != 0 else t2)")
            if need & (CF | OF):
                self.o("cf = 0 if (t >= -128 and t <= 127) else 1")
                self.o("of = cf")
            self.o("ax = t & 0xFFFF")
            self.o("t = ax & 0xFF")
            self.szp("t", 0, need)

    def i_imul3(self, ins, la, bl):
        need = ins.writes & la
        dst, src, imm = ins.ops
        v = self.rd(src)
        self.o("t2 = %s" % v)
        iv = imm[1] - 0x10000 if imm[1] & 0x8000 else imm[1]
        self.o("t = (t2 - 0x10000 if (t2 & 0x8000) != 0 else t2) * %d" % iv)
        if need & (CF | OF):
            self.o("cf = 0 if (t >= -0x8000 and t <= 0x7FFF) else 1")
            self.o("of = cf")
        self.wr(dst, "t & 0xFFFF")
        self.o("t = t & 0xFFFF")
        self.szp("t", 1, need)

    def div_fault(self, ins):
        self.spill(None, ALL)
        self.o("return host.div_fault(%s)" % h((ins.cs << 16) | ins.ip))

    def i_div(self, ins, la, bl):
        v = self.rd(ins.ops[0])
        self.o("t2 = %s" % v)
        if ins.w:
            self.o("t3 = (dx << 16) | ax")
            self.o("if t2 == 0 or t3 / t2 > 0xFFFF:")
            self.ind += 1
            self.div_fault(ins)
            self.ind -= 1
            self.o("ax = t3 / t2")
            self.o("dx = t3 % t2")
        else:
            self.o("if t2 == 0 or ax / t2 > 0xFF:")
            self.ind += 1
            self.div_fault(ins)
            self.ind -= 1
            self.o("ax = ((ax % t2) << 8) | (ax / t2)")

    def i_idiv(self, ins, la, bl):
        v = self.rd(ins.ops[0])
        o = self.o
        o("t2 = %s" % v)
        if ins.w:
            o("t3 = (dx << 16) | ax")
            o("if t3 >= 0x80000000:")
            o("\tt3 -= 0x100000000")
            o("if t2 >= 0x8000:")
            o("\tt2 -= 0x10000")
            o("if t2 == 0:")
            self.ind += 1
            self.div_fault(ins)
            self.ind -= 1
            o("t = (t3 if t3 >= 0 else -t3) / (t2 if t2 >= 0 else -t2)")
            o("if (t3 < 0) != (t2 < 0):")
            o("\tt = -t")
            o("if t > 0x7FFF or t < -0x8000:")
            self.ind += 1
            self.div_fault(ins)
            self.ind -= 1
            o("dx = (t3 - t * t2) & 0xFFFF")
            o("ax = t & 0xFFFF")
        else:
            o("t3 = ax - 0x10000 if (ax & 0x8000) != 0 else ax")
            o("if t2 >= 0x80:")
            o("\tt2 -= 0x100")
            o("if t2 == 0:")
            self.ind += 1
            self.div_fault(ins)
            self.ind -= 1
            o("t = (t3 if t3 >= 0 else -t3) / (t2 if t2 >= 0 else -t2)")
            o("if (t3 < 0) != (t2 < 0):")
            o("\tt = -t")
            o("if t > 127 or t < -128:")
            self.ind += 1
            self.div_fault(ins)
            self.ind -= 1
            o("ax = (((t3 - t * t2) & 0xFF) << 8) | (t & 0xFF)")

    # ---- shifts / rotates
    def shiftop(self, ins, la):
        dst, cnt = ins.ops
        w = ins.w
        sub = ins.sub
        bits = 16 if w else 8
        mask = 0xFFFF if w else 0xFF
        msb = bits - 1
        need = (ins.writes | ins.maywrite) & la
        o = self.o
        v = self.rd(dst)
        o("t = %s" % v)
        if cnt[0] == "i":
            c = cnt[1] & 0x1F
            if c == 0:
                return
            cexpr = str(c)
            dyn = False
        else:
            o("t2 = cx & 0x1F")
            cexpr = "t2"
            dyn = True
            o("if t2 != 0:")
            self.ind += 1
        if sub == 0:     # ROL
            if dyn:
                o("t3 = t2 %% %d" % bits)
                o("if t3 != 0:")
                o("\tt = ((t << t3) | (t >> (%d - t3))) & %s" % (bits, h(mask)))
            else:
                k = c % bits
                if k:
                    o("t = ((t << %d) | (t >> %d)) & %s" % (k, bits - k, h(mask)))
            if need & (CF | OF):
                o("cf = t & 1")
                o("of = ((t >> %d) & 1) ^ cf" % msb)
        elif sub == 1:   # ROR
            if dyn:
                o("t3 = t2 %% %d" % bits)
                o("if t3 != 0:")
                o("\tt = ((t >> t3) | (t << (%d - t3))) & %s" % (bits, h(mask)))
            else:
                k = c % bits
                if k:
                    o("t = ((t >> %d) | (t << %d)) & %s" % (k, bits - k, h(mask)))
            if need & (CF | OF):
                o("cf = (t >> %d) & 1" % msb)
                o("of = ((t >> %d) ^ (t >> %d)) & 1" % (msb, msb - 1))
        elif sub in (2, 3):  # RCL / RCR
            if dyn:
                o("t3 = t2 %% %d" % (bits + 1))
            else:
                o("t3 = %d" % (c % (bits + 1)))
            o("while t3 > 0:")
            if sub == 2:
                o("\tt4 = (t >> %d) & 1" % msb)
                o("\tt = ((t << 1) | cf) & %s" % h(mask))
            else:
                o("\tt4 = t & 1")
                o("\tt = (t >> 1) | (cf << %d)" % msb)
            o("\tcf = t4")
            o("\tt3 -= 1")
            if need & OF:
                if sub == 2:
                    o("of = ((t >> %d) & 1) ^ cf" % msb)
                else:
                    o("of = ((t >> %d) ^ (t >> %d)) & 1" % (msb, msb - 1))
        elif sub in (4, 6):  # SHL
            if dyn:
                o("if t2 > %d:" % bits)
                o("\tcf = 0")
                o("\tt = 0")
                o("else:")
                o("\tcf = (t >> (%d - t2)) & 1" % bits)
                o("\tt = (t << t2) & %s" % h(mask))
            else:
                if c > bits:
                    o("cf = 0")
                    o("t = 0")
                else:
                    if need & (CF | OF):
                        o("cf = (t >> %d) & 1" % (bits - c))
                    o("t = (t << %d) & %s" % (c, h(mask)))
            if need & OF:
                o("of = ((t >> %d) & 1) ^ cf" % msb)
            if need & AF:
                o("af = 0")
            self.szp("t", w, need)
        elif sub == 5:   # SHR
            if need & OF:
                o("of = (t >> %d) & 1" % msb)
            if dyn:
                o("if t2 > %d:" % bits)
                o("\tcf = 0")
                o("\tt = 0")
                o("else:")
                o("\tcf = (t >> (t2 - 1)) & 1")
                o("\tt = t >> t2")
            else:
                if c > bits:
                    o("cf = 0")
                    o("t = 0")
                else:
                    if need & CF:
                        o("cf = (t >> %d) & 1" % (c - 1))
                    o("t = t >> %d" % c)
            if need & AF:
                o("af = 0")
            self.szp("t", w, need)
        else:            # SAR
            o("t4 = t - %d if (t & %s) != 0 else t" % (1 << bits, h(1 << msb)))
            if dyn:
                o("if t2 >= %d:" % bits)
                o("\tt2 = %d" % bits)
                o("cf = (t4 >> (t2 - 1)) & 1")
                o("t = (t4 >> t2) & %s" % h(mask))
            else:
                cc = min(c, bits)
                if need & CF:
                    o("cf = (t4 >> %d) & 1" % (cc - 1))
                o("t = (t4 >> %d) & %s" % (cc, h(mask)))
            if need & OF:
                o("of = 0")
            if need & AF:
                o("af = 0")
            self.szp("t", w, need)
        self.wr(dst, "t", have_addr=dst[0] == "m")
        if dyn:
            self.ind -= 1

    def i_rol(self, ins, la, bl):
        self.shiftop(ins, la)

    i_ror = i_rcl = i_rcr = i_shl = i_shr = i_sar = i_rol

    # ---- string instructions
    def strop(self, ins, la):
        mn = ins.mn
        w = ins.w
        o = self.o
        step = 2 if w else 1
        src = 3 if ins.seg is None else ins.seg
        o("t4 = %d if st.df != 0 else %d" % (-step, step))
        rep = ins.rep
        if rep:
            o("while cx != 0:")
            self.ind += 1
        sbase = self.seg_base(src)
        if mn == "movs":
            o("a = (%s + si) & 0xFFFFF" % sbase)
            o("t3 = ((es << 4) + di) & 0xFFFFF")
            if w:
                o("t = m[a] | (m[(%s + ((si + 1) & 0xFFFF)) & 0xFFFFF] << 8)" % sbase)
                o("m[t3] = t & 0xFF")
                o("m[((es << 4) + ((di + 1) & 0xFFFF)) & 0xFFFFF] = t >> 8")
            else:
                o("m[t3] = m[a]")
            o("si = (si + t4) & 0xFFFF")
            o("di = (di + t4) & 0xFFFF")
        elif mn == "stos":
            if w:
                o("a = ((es << 4) + di) & 0xFFFFF")
                o("m[a] = ax & 0xFF")
                o("m[((es << 4) + ((di + 1) & 0xFFFF)) & 0xFFFFF] = ax >> 8")
            else:
                o("m[((es << 4) + di) & 0xFFFFF] = ax & 0xFF")
            o("di = (di + t4) & 0xFFFF")
        elif mn == "lods":
            o("a = (%s + si) & 0xFFFFF" % sbase)
            if w:
                o("ax = m[a] | (m[(%s + ((si + 1) & 0xFFFF)) & 0xFFFFF] << 8)" % sbase)
            else:
                o("ax = (ax & 0xFF00) | m[a]")
            o("si = (si + t4) & 0xFFFF")
        elif mn in ("cmps", "scas"):
            need = (ins.writes | ins.maywrite) & la
            if rep:
                need |= ZF
            if mn == "cmps":
                o("a = (%s + si) & 0xFFFFF" % sbase)
                if w:
                    o("t2 = m[a] | (m[(%s + ((si + 1) & 0xFFFF)) & 0xFFFFF] << 8)" % sbase)
                else:
                    o("t2 = m[a]")
                o("si = (si + t4) & 0xFFFF")
            else:
                o("t2 = %s" % ("ax" if w else "(ax & 0xFF)"))
            o("a = ((es << 4) + di) & 0xFFFFF")
            if w:
                o("t3 = m[a] | (m[((es << 4) + ((di + 1) & 0xFFFF)) & 0xFFFFF] << 8)")
            else:
                o("t3 = m[a]")
            o("di = (di + t4) & 0xFFFF")
            # flags of the comparison (only the needed ones)
            save_t4 = "t4"
            o("a = t4")
            self.alu("cmp", w, "t2", "t3", need, res="t")
            o("t4 = a")
            del save_t4
            if rep:
                o("cx = (cx - 1) & 0xFFFF")
                if rep == 0xF3:
                    o("if zf == 0:")
                else:
                    o("if zf != 0:")
                o("\tbreak")
        if rep:
            if mn not in ("cmps", "scas"):
                o("cx = (cx - 1) & 0xFFFF")
            self.ind -= 1

    def i_movs(self, ins, la, bl):
        self.strop(ins, la)

    i_stos = i_lods = i_cmps = i_scas = i_movs

    # ---- I/O
    def i_in(self, ins, la, bl):
        dst, port = ins.ops
        pe = h(port[1]) if port[0] == "i" else "dx"
        self.o("st.eb = pl")
        if ins.w:
            self.o("ax = host.port_in(%s, 1) & 0xFFFF" % pe)
        else:
            self.o("ax = (ax & 0xFF00) | (host.port_in(%s, 0) & 0xFF)" % pe)
        self.o("pl = st.eb")

    def i_out(self, ins, la, bl):
        port, src = ins.ops
        pe = h(port[1]) if port[0] == "i" else "dx"
        if ins.w:
            self.o("host.port_out(%s, ax, 1)" % pe)
        else:
            self.o("host.port_out(%s, ax & 0xFF, 0)" % pe)

    # ---- control flow
    def i_jcc(self, ins, la, bl):
        self.branch(bl, ins, self.jcc_cond(ins.cond))
        return True

    def i_jcxz(self, ins, la, bl):
        self.branch(bl, ins, "cx == 0")
        return True

    def i_loop(self, ins, la, bl):
        self.o("cx = (cx - 1) & 0xFFFF")
        self.branch(bl, ins, "cx != 0")
        return True

    def i_loopz(self, ins, la, bl):
        self.o("cx = (cx - 1) & 0xFFFF")
        self.branch(bl, ins, "cx != 0 and zf != 0")
        return True

    def i_loopnz(self, ins, la, bl):
        self.o("cx = (cx - 1) & 0xFFFF")
        self.branch(bl, ins, "cx != 0 and zf == 0")
        return True

    def i_jmp(self, ins, la, bl):
        self.goto(bl, ins.target, count=True)
        return True

    def i_call(self, ins, la, bl):
        nip = ins.next_ip()
        self.push(h(nip))
        if ins.ext == "puship":
            return False
        ck = self.an.callee_key(self.f.unit, self.cs, ins.target)
        e, g = self.callee_expr(ck)
        if e is None:
            e = "host.dispatch(%s)" % h((self.cs << 16) | ins.target)
        if ins.ext == "pstr":
            # the callee consumes the inline Pascal string after the CALL and
            # returns past it
            cont = self.f.cont[ins.ip]
            self.emit_call(ins, la, (self.cs << 16) | cont, e, g)
            self.goto(bl, cont)
            return True
        self.emit_call(ins, la, (self.cs << 16) | nip, e, g)

    def push_far_ret(self, nip):
        o = self.o
        o("sp = (sp - 4) & 0xFFFF")
        o("a = (ss << 4) + sp")
        o("m[a] = %s" % h(nip & 0xFF))
        o("m[a + 1] = %s" % h(nip >> 8))
        o("m[a + 2] = %s" % h(self.cs & 0xFF))
        o("m[a + 3] = %s" % h(self.cs >> 8))

    def i_callf(self, ins, la, bl):
        nip = ins.next_ip()
        tseg, toff = ins.target
        if ins.ext == "switch":
            self.push_far_ret(nip)
            self.switch(ins, bl)
            return True
        self.push_far_ret(nip)
        exp = (self.cs << 16) | nip
        if ins.ext == "thunk":
            th = self.an.p.thunk_at[(tseg, toff)]
            ck = (th["ovl"], th["tseg"], th["toff"])
            e, g = self.callee_expr(ck)
            if e is None:
                raise GenError("thunk target not translated: %r" % (ck,))
            fin = g.fin
            self.spill(None, fin)
            self.o("host.ovl_ensure(%d)" % th["ovl"])
            self.o("t = %s" % e)
            self.o("if t != %s:" % h(exp))
            self.o("\treturn host.unwind(t, %s)" % h((ins.cs << 16) | ins.ip))
            self.reload(g.defs | {4}, la)
            return False
        ck = self.an.callee_key(self.f.unit, tseg, toff)
        e, g = self.callee_expr(ck)
        if e is None:
            e = "host.dispatch(%s)" % h((tseg << 16) | toff)
        self.emit_call(ins, la, exp, e, g)
        return False

    def switch(self, ins, bl):
        """Inline the C runtime's switch helper (13F3:00E7 dense / 07DC sparse)."""
        sw = self.f.switch[ins.ip]
        o = self.o
        tip = sw["table"]
        # helper: pop di (table) / pop es (our CS) / pop value
        o("a = (ss << 4) + sp")
        o("t2 = m[a + 4] | (m[a + 5] << 8)")
        o("sp = (sp + 6) & 0xFFFF")
        o("di = %s" % h(tip))
        o("es = %s" % h(self.cs))
        if sw["kind"] == "dense":
            o("bx = (t2 - %s) & 0xFFFF" % h(sw["min"]))
            o("if bx >= %s:" % h(sw["count"]))
            o("\tbx = 0xFFFF")
            # shl bx,1
            o("cf = bx >> 15")
            o("bx = (bx << 1) & 0xFFFF")
            o("zf = 1 if bx == 0 else 0")
            o("sf = bx >> 15")
            o("of = (bx >> 15) ^ cf")
            o("af = 0")
            self.uses_par = True
            o("pf = par[bx & 0xFF]")
            o("t3 = m[(%s + ((di + bx + 6) & 0xFFFF)) & 0xFFFFF]" % h(self.cs << 4))
            o("t3 = t3 | (m[(%s + ((di + bx + 7) & 0xFFFF)) & 0xFFFFF] << 8)" % h(self.cs << 4))
            # residue of push es / push target / retf
            o("a = (ss << 4) + ((sp - 4) & 0xFFFF)")
            o("m[a] = t3 & 0xFF")
            o("m[a + 1] = t3 >> 8")
            o("m[a + 2] = %s" % h(self.cs & 0xFF))
            o("m[a + 3] = %s" % h(self.cs >> 8))
            targets = sorted(set(sw["targets"]) | {sw["default"]})
            first = True
            for t in targets:
                o("%s t3 == %s:" % ("if" if first else "elif", h(t)))
                first = False
                self.ind += 1
                self.goto(bl, t, True)
                self.ind -= 1
            o("return host.fatal(%s, \"switch target\")" % h((self.cs << 16) | ins.ip))
        else:
            n = sw["n"]
            vals = sw["values"]
            # repne scasw backwards over values[n-1..0] and the count word: ax = value
            o("ax = t2")
            o("bx = %s" % h(2 * n))
            o("cx = %s" % h(n + 1))
            # emulate: di = table + 2n, scan down while cx != 0
            o("di = %s" % h((tip + 2 * n) & 0xFFFF))
            o("t3 = -1")
            o("while cx != 0:")
            o("\ta = (%s + di) & 0xFFFFF" % h(self.cs << 4))
            o("\tt4 = m[a] | (m[a + 1] << 8)")
            o("\tdi = (di - 2) & 0xFFFF")
            o("\tcx = (cx - 1) & 0xFFFF")
            o("\tif t4 == ax:")
            o("\t\tbreak")
            # flags of the last compare are overwritten by add di,4 / add di,bx below
            o("di = (di + 4) & 0xFFFF")
            o("t4 = di + bx")
            o("di = t4 & 0xFFFF")
            o("cf = t4 >> 16")
            o("zf = 1 if di == 0 else 0")
            o("sf = di >> 15")
            o("af = ((t4 - bx) ^ bx ^ di) >> 4 & 1")
            o("of = ((((t4 - bx) & 0xFFFF) ^ di) & (bx ^ di)) >> 15")
            self.uses_par = True
            o("pf = par[di & 0xFF]")
            o("a = (%s + di) & 0xFFFFF" % h(self.cs << 4))
            o("t3 = m[a] | (m[a + 1] << 8)")
            o("st.df = 0")
            o("ax = ds")
            o("es = ds")
            o("a = (ss << 4) + ((sp - 4) & 0xFFFF)")
            o("m[a] = t3 & 0xFF")
            o("m[a + 1] = t3 >> 8")
            o("m[a + 2] = %s" % h(self.cs & 0xFF))
            o("m[a + 3] = %s" % h(self.cs >> 8))
            targets = sorted(set(sw["targets"]) | {sw["default"]})
            first = True
            for t in targets:
                o("%s t3 == %s:" % ("if" if first else "elif", h(t)))
                first = False
                self.ind += 1
                self.goto(bl, t, True)
                self.ind -= 1
            o("return host.fatal(%s, \"switch target\")" % h((self.cs << 16) | ins.ip))
            del vals

    def i_callind(self, ins, la, bl):
        nip = ins.next_ip()
        v = self.rd(ins.ops[0])
        self.o("t2 = %s" % v)
        self.push(h(nip))
        self.dyn_call(ins, la, "(%s | t2)" % h(self.cs << 16), (self.cs << 16) | nip)

    def i_callfind(self, ins, la, bl):
        nip = ins.next_ip()
        op = ins.ops[0]
        _, w, s, base, disp = op
        off = self.offset(base, disp)
        self.o("t3 = %s" % off)
        sbase = self.seg_base(s)
        self.o("a = (%s + t3) & 0xFFFFF" % sbase)
        self.o("t2 = m[a] | (m[a + 1] << 8)")
        self.o("a = (%s + ((t3 + 2) & 0xFFFF)) & 0xFFFFF" % sbase)
        self.o("t2 = t2 | ((m[a] | (m[a + 1] << 8)) << 16)")
        self.push_far_ret(nip)
        self.dyn_call(ins, la, "t2", (self.cs << 16) | nip)

    def dyn_call(self, ins, la, target, exp):
        self.spill(None, ALL)
        self.o("t = host.dispatch(%s)" % target)
        self.o("if t != %s:" % h(exp))
        self.o("\treturn host.unwind(t, %s)" % h((ins.cs << 16) | ins.ip))
        self.reload(ALL_REGS, la)

    def i_jmpind(self, ins, la, bl):
        v = self.rd(ins.ops[0])
        self.o("t2 = %s" % v)
        if ins.ext == "jmpret":
            # the helper popped its return address and jumps to it: a return
            self.spill(None, self.f.fout)
            self.o("return %s | t2" % h(self.cs << 16))
            return True
        tg = self.f.dyn.get(ins.ip, [])
        intra = sorted({t[1] for t in tg if t[0] == self.cs})
        first = True
        for t in intra:
            self.o("%s t2 == %s:" % ("if" if first else "elif", h(t)))
            first = False
            self.ind += 1
            self.goto(bl, t, True)
            self.ind -= 1
        self.spill(None, ALL)
        self.o("return host.dispatch(%s | t2)" % h(self.cs << 16))
        return True

    def i_jmpfind(self, ins, la, bl):
        op = ins.ops[0]
        _, w, s, base, disp = op
        off = self.offset(base, disp)
        self.o("t3 = %s" % off)
        sbase = self.seg_base(s)
        self.o("a = (%s + t3) & 0xFFFFF" % sbase)
        self.o("t2 = m[a] | (m[a + 1] << 8)")
        self.o("a = (%s + ((t3 + 2) & 0xFFFF)) & 0xFFFFF" % sbase)
        self.o("t2 = t2 | ((m[a] | (m[a + 1] << 8)) << 16)")
        self.spill(None, ALL)
        self.o("return host.dispatch(t2)")
        return True

    def i_jmpf(self, ins, la, bl):
        tseg, toff = ins.target
        if ins.ext == "thunk":
            th = self.an.p.thunk_at[(tseg, toff)]
            ck = (th["ovl"], th["tseg"], th["toff"])
            e, g = self.callee_expr(ck)
            self.spill(None, g.fin)
            self.o("host.ovl_ensure(%d)" % th["ovl"])
            self.o("return %s" % e)
            return True
        ck = self.an.callee_key(self.f.unit, tseg, toff)
        e, g = self.callee_expr(ck)
        if e is None:
            self.spill(None, ALL)
            self.o("return host.dispatch(%s)" % h((tseg << 16) | toff))
            return True
        self.spill(None, g.fin)
        self.o("return %s" % e)
        return True

    def ret_jump(self, ins):
        """RET/RETF used as an indirect jump (the profile saw it go somewhere no
        call expects, e.g. the C runtime's call-through-far-pointer trampoline)."""
        return (ins.cs, ins.ip) in self.an.ret_jump_sites

    def i_ret(self, ins, la, bl):
        n = ins.ops[0][1]
        self.pop("t2")
        if n:
            self.o("sp = (sp + %s) & 0xFFFF" % h(n))
        if self.ret_jump(ins):
            self.spill(None, ALL)
            self.o("return host.dispatch(%s | t2)" % h(self.cs << 16))
            return True
        self.spill(None, self.f.fout)
        self.o("return %s | t2" % h(self.cs << 16))
        return True

    def i_retf(self, ins, la, bl):
        n = ins.ops[0][1]
        self.o("a = (ss << 4) + sp")
        self.o("t2 = m[a] | (m[a + 1] << 8) | (m[a + 2] << 16) | (m[a + 3] << 24)")
        self.o("sp = (sp + %s) & 0xFFFF" % h(4 + n))
        if self.ret_jump(ins):
            self.spill(None, ALL)
            self.o("return host.dispatch(t2)")
            return True
        self.spill(None, self.f.fout)
        self.o("return t2")
        return True

    def i_iret(self, ins, la, bl):
        self.o("a = (ss << 4) + sp")
        self.o("t2 = m[a] | (m[a + 1] << 8) | (m[a + 2] << 16) | (m[a + 3] << 24)")
        self.o("t3 = m[a + 4] | (m[a + 5] << 8)")
        self.o("sp = (sp + 6) & 0xFFFF")
        self.set_flags_from("t3")
        self.spill(None, ALL)
        self.o("if (t2 >> 16) == 0xF000:")
        self.o("\treturn host.iret_to_rom(t2)")
        self.o("return t2")
        return True

    def i_int(self, ins, la, bl):
        n = ins.ops[0][1]
        nip = ins.next_ip()
        self.push(self.flagword())
        self.o("st.iflag = 0")
        self.o("st.tf = 0")
        self.push_far_ret(nip)
        self.spill(None, ALL)
        self.o("t = host.do_int(%s)" % h(n))
        self.o("if t != %s:" % h((self.cs << 16) | nip))
        self.o("\treturn host.unwind(t, %s)" % h((ins.cs << 16) | ins.ip))
        self.reload(ALL_REGS, ALL)
        return False

    def i_into(self, ins, la, bl):
        raise GenError("INTO not supported")

    def i_hlt(self, ins, la, bl):
        self.spill(None, ALL)
        self.o("return host.fatal(%s, \"HLT\")" % h((ins.cs << 16) | ins.ip))
        return True

    def i_pusha(self, ins, la, bl):
        self.o("t3 = sp")
        for r_ in range(8):
            self.push("t3" if r_ == 4 else REGS16[r_])

    def i_popa(self, ins, la, bl):
        for r_ in (7, 6, 5, 4, 3, 2, 1, 0):
            if r_ == 4:
                self.o("sp = (sp + 2) & 0xFFFF")
            else:
                self.pop(REGS16[r_])

    def i_leave(self, ins, la, bl):
        self.o("sp = bp")
        self.pop("bp")

    def i_enter(self, ins, la, bl):
        size = ins.ops[0][1]
        lvl = ins.ops[1][1] & 0x1F
        if lvl:
            raise GenError("ENTER with nesting level")
        self.push("bp")
        self.o("bp = sp")
        self.o("sp = (sp - %s) & 0xFFFF" % h(size))


def gen_function(an, f, unit_ref):
    g = FuncGen(an, f, unit_ref)
    return g.gen()
