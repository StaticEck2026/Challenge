"""8086 instruction decoder for the static recompiler.

decode(read, cs, ip) -> Insn.  ``read(lin)`` returns the byte at a linear
address of the image being translated.  The decoder follows the same operand
rules as tools/refemu/cpu.py (the oracle), including the 80186 forms the
oracle implements, so translation and oracle agree on every byte.

Operands:
  ("r", w, idx)        register; w=1 word (AX CX DX BX SP BP SI DI), w=0 byte (AL CL DL BL AH CH DH BH)
  ("s", idx)           segment register ES CS SS DS
  ("i", value)         immediate
  ("m", w, seg, base, disp)  memory: seg = 0..3 (after override), base = tuple of
                       general register indices summed with disp (16-bit wrap)
"""

ES, CS, SS, DS = 0, 1, 2, 3
AX, CX, DX, BX, SP, BP, SI, DI = range(8)

# flag bits (FLAGS register layout)
CF, PF, AF, ZF, SF, OF = 0x1, 0x4, 0x10, 0x40, 0x80, 0x800
ALL = CF | PF | AF | ZF | SF | OF
SZP = SF | ZF | PF

MODRM_BASE = [((BX, SI), DS), ((BX, DI), DS), ((BP, SI), SS), ((BP, DI), SS),
              ((SI,), DS), ((DI,), DS), ((BP,), SS), ((BX,), DS)]

ALU_NAMES = ["add", "or", "adc", "sbb", "and", "sub", "xor", "cmp"]
SHIFT_NAMES = ["rol", "ror", "rcl", "rcr", "shl", "shr", "shl", "sar"]
JCC_NAMES = ["jo", "jno", "jb", "jae", "jz", "jnz", "jbe", "ja",
             "js", "jns", "jp", "jnp", "jl", "jge", "jle", "jg"]
# flags read by each condition code
JCC_READS = [OF, OF, CF, CF, ZF, ZF, CF | ZF, CF | ZF, SF, SF, PF, PF,
             SF | OF, SF | OF, ZF | SF | OF, ZF | SF | OF]


class DecodeError(Exception):
    pass


class Insn:
    __slots__ = ("cs", "ip", "lin", "size", "op", "mn", "ops", "seg", "rep", "w",
                 "target", "far", "cond", "reads", "writes", "maywrite", "sub", "ext")

    def __init__(self):
        self.seg = None       # explicit segment override index or None
        self.rep = 0          # 0, 0xF2, 0xF3
        self.ops = ()
        self.w = 1
        self.target = None    # near branch target (ip) or (seg, off) for far
        self.far = False
        self.cond = None
        self.reads = 0        # flags read
        self.writes = 0       # flags certainly written
        self.maywrite = 0     # flags possibly written (count may be 0)
        self.sub = 0
        self.ext = None

    def next_ip(self):
        return (self.ip + self.size) & 0xFFFF

    def __repr__(self):
        return "%04X:%04X %s %s" % (self.cs, self.ip, self.mn, self.ops)


def decode(read, cs, ip):
    ins = Insn()
    ins.cs = cs
    ins.ip = ip
    ins.lin = ((cs << 4) + ip) & 0xFFFFF
    p = [ip]

    def b():
        v = read(((cs << 4) + p[0]) & 0xFFFFF)
        p[0] = (p[0] + 1) & 0xFFFF
        return v

    def w16():
        lo = b()
        return lo | (b() << 8)

    def s8():
        v = b()
        return v - 256 if v & 0x80 else v

    seg = None
    rep = 0
    op = b()
    n = 0
    while op in (0x26, 0x2E, 0x36, 0x3E, 0xF2, 0xF3, 0xF0):
        if op in (0xF2, 0xF3):
            rep = op
        elif op != 0xF0:
            seg = (op >> 3) & 3
        op = b()
        n += 1
        if n > 4:
            raise DecodeError("too many prefixes")
    ins.seg = seg
    ins.rep = rep
    ins.op = op

    def modrm(w):
        m = b()
        mod = m >> 6
        reg = (m >> 3) & 7
        rm = m & 7
        if mod == 3:
            return reg, ("r", w, rm)
        base, dseg = MODRM_BASE[rm]
        disp = 0
        if mod == 0 and rm == 6:
            base = ()
            dseg = DS
            disp = w16()
        elif mod == 1:
            disp = s8()
        elif mod == 2:
            disp = w16()
        if seg is not None:
            dseg = seg
        return reg, ("m", w, dseg, base, disp & 0xFFFF)

    def fin(mn, ops=(), w=1, reads=0, writes=0, maywrite=0):
        ins.mn = mn
        ins.ops = tuple(ops)
        ins.w = w
        ins.reads = reads
        ins.writes = writes
        ins.maywrite = maywrite
        ins.size = (p[0] - ip) & 0xFFFF
        return ins

    if op < 0x40 and (op & 7) < 6:
        alu = op >> 3
        form = op & 7
        name = ALU_NAMES[alu]
        rd = CF if alu in (2, 3) else 0
        if form < 4:
            w = form & 1
            reg, rm = modrm(w)
            r = ("r", w, reg)
            ops = (rm, r) if form < 2 else (r, rm)
        elif form == 4:
            ops = (("r", 0, AX), ("i", b()))
            w = 0
        else:
            ops = (("r", 1, AX), ("i", w16()))
            w = 1
        return fin(name, ops, w, rd, ALL)
    if op in (0x06, 0x0E, 0x16, 0x1E):
        return fin("push", [("s", op >> 3)])
    if op in (0x07, 0x17, 0x1F):
        return fin("pop", [("s", op >> 3)])
    if op == 0x27:
        return fin("daa", [], 0, CF | AF, CF | AF | SZP)
    if op == 0x2F:
        return fin("das", [], 0, CF | AF, CF | AF | SZP)
    if op == 0x37:
        return fin("aaa", [], 0, AF, CF | AF | SZP)
    if op == 0x3F:
        return fin("aas", [], 0, AF, CF | AF | SZP)
    if 0x40 <= op <= 0x47:
        return fin("inc", [("r", 1, op & 7)], 1, 0, ALL & ~CF)
    if 0x48 <= op <= 0x4F:
        return fin("dec", [("r", 1, op & 7)], 1, 0, ALL & ~CF)
    if 0x50 <= op <= 0x57:
        return fin("push", [("r", 1, op & 7)])
    if 0x58 <= op <= 0x5F:
        return fin("pop", [("r", 1, op & 7)])
    if op == 0x60:
        return fin("pusha")
    if op == 0x61:
        return fin("popa")
    if op == 0x68:
        return fin("push", [("i", w16())])
    if op == 0x6A:
        return fin("push", [("i", s8() & 0xFFFF)])
    if op in (0x69, 0x6B):
        reg, rm = modrm(1)
        imm = w16() if op == 0x69 else s8() & 0xFFFF
        return fin("imul3", [("r", 1, reg), rm, ("i", imm)], 1, 0, CF | OF | SZP)
    if 0x70 <= op <= 0x7F:
        d = s8()
        ins.cond = op & 15
        ins.target = (p[0] + d) & 0xFFFF
        return fin("jcc", [], 1, JCC_READS[op & 15], 0)
    if 0x80 <= op <= 0x83:
        w = op & 1
        alu, rm = modrm(w)
        if op == 0x81:
            imm = w16()
        elif op == 0x83:
            imm = s8() & 0xFFFF
        else:
            imm = b()
        return fin(ALU_NAMES[alu], [rm, ("i", imm)], w, CF if alu in (2, 3) else 0, ALL)
    if op in (0x84, 0x85):
        w = op & 1
        reg, rm = modrm(w)
        return fin("test", [rm, ("r", w, reg)], w, 0, ALL)
    if op in (0x86, 0x87):
        w = op & 1
        reg, rm = modrm(w)
        return fin("xchg", [rm, ("r", w, reg)], w)
    if 0x88 <= op <= 0x8B:
        w = op & 1
        reg, rm = modrm(w)
        r = ("r", w, reg)
        return fin("mov", (rm, r) if op < 0x8A else (r, rm), w)
    if op == 0x8C:
        reg, rm = modrm(1)
        return fin("mov", [rm, ("s", reg & 3)])
    if op == 0x8D:
        reg, rm = modrm(1)
        if rm[0] != "m":
            raise DecodeError("lea with register operand")
        return fin("lea", [("r", 1, reg), rm])
    if op == 0x8E:
        reg, rm = modrm(1)
        return fin("mov", [("s", reg & 3), rm])
    if op == 0x8F:
        reg, rm = modrm(1)
        return fin("pop", [rm])
    if op == 0x90:
        return fin("nop")
    if 0x91 <= op <= 0x97:
        return fin("xchg", [("r", 1, AX), ("r", 1, op & 7)])
    if op == 0x98:
        return fin("cbw")
    if op == 0x99:
        return fin("cwd")
    if op == 0x9A:
        off = w16()
        sg = w16()
        ins.target = (sg, off)
        ins.far = True
        return fin("callf")
    if op == 0x9B:
        return fin("nop")
    if op == 0x9C:
        return fin("pushf", [], 1, ALL, 0)
    if op == 0x9D:
        return fin("popf", [], 1, 0, ALL)
    if op == 0x9E:
        return fin("sahf", [], 1, 0, CF | PF | AF | ZF | SF)
    if op == 0x9F:
        return fin("lahf", [], 1, CF | PF | AF | ZF | SF, 0)
    if 0xA0 <= op <= 0xA3:
        off = w16()
        w = op & 1
        m = ("m", w, DS if seg is None else seg, (), off)
        a = ("r", w, AX)
        return fin("mov", (a, m) if op < 0xA2 else (m, a), w)
    if 0xA4 <= op <= 0xAF and op not in (0xA8, 0xA9):
        w = op & 1
        name = {0xA4: "movs", 0xA6: "cmps", 0xAA: "stos", 0xAC: "lods", 0xAE: "scas"}[op & 0xFE]
        if name in ("cmps", "scas"):
            if rep:
                return fin(name, [], w, ALL, 0, ALL)
            return fin(name, [], w, 0, ALL)
        return fin(name, [], w)
    if op in (0xA8, 0xA9):
        w = op & 1
        return fin("test", [("r", w, AX), ("i", w16() if w else b())], w, 0, ALL)
    if 0xB0 <= op <= 0xB7:
        return fin("mov", [("r", 0, op & 7), ("i", b())], 0)
    if 0xB8 <= op <= 0xBF:
        return fin("mov", [("r", 1, op & 7), ("i", w16())], 1)
    if op in (0xC0, 0xC1, 0xD0, 0xD1, 0xD2, 0xD3):
        w = op & 1
        sub, rm = modrm(w)
        if op <= 0xC1:
            cnt = ("i", b())
        elif op <= 0xD1:
            cnt = ("i", 1)
        else:
            cnt = ("r", 0, CX)
        ins.sub = sub
        rd, wr = shift_flags(sub, cnt)
        if cnt[0] == "i" and (cnt[1] & 0x1F) == 0:
            return fin(SHIFT_NAMES[sub], [rm, cnt], w, 0, 0)
        if cnt[0] == "i":
            return fin(SHIFT_NAMES[sub], [rm, cnt], w, rd, wr)
        return fin(SHIFT_NAMES[sub], [rm, cnt], w, rd | wr, 0, wr)
    if op == 0xC2:
        return fin("ret", [("i", w16())])
    if op == 0xC3:
        return fin("ret", [("i", 0)])
    if op in (0xC4, 0xC5):
        reg, rm = modrm(1)
        if rm[0] != "m":
            raise DecodeError("les/lds with register operand")
        return fin("les" if op == 0xC4 else "lds", [("r", 1, reg), rm])
    if op in (0xC6, 0xC7):
        w = op & 1
        _, rm = modrm(w)
        return fin("mov", [rm, ("i", w16() if w else b())], w)
    if op == 0xC8:
        size = w16()
        lvl = b()
        return fin("enter", [("i", size), ("i", lvl)])
    if op == 0xC9:
        return fin("leave")
    if op == 0xCA:
        return fin("retf", [("i", w16())])
    if op == 0xCB:
        return fin("retf", [("i", 0)])
    if op == 0xCC:
        return fin("int", [("i", 3)], 1, ALL, ALL)
    if op == 0xCD:
        return fin("int", [("i", b())], 1, ALL, ALL)
    if op == 0xCE:
        return fin("into", [], 1, ALL, ALL)
    if op == 0xCF:
        return fin("iret", [], 1, 0, ALL)
    if op == 0xD4:
        return fin("aam", [("i", b())], 0, 0, SZP)
    if op == 0xD5:
        return fin("aad", [("i", b())], 0, 0, SZP)
    if op == 0xD6:
        return fin("salc", [], 0, CF, 0)
    if op == 0xD7:
        return fin("xlat", [], 0)
    if 0xD8 <= op <= 0xDF:
        modrm(1)
        return fin("esc")
    if op in (0xE0, 0xE1, 0xE2, 0xE3):
        d = s8()
        ins.target = (p[0] + d) & 0xFFFF
        name = {0xE0: "loopnz", 0xE1: "loopz", 0xE2: "loop", 0xE3: "jcxz"}[op]
        return fin(name, [], 1, ZF if op in (0xE0, 0xE1) else 0, 0)
    if op in (0xE4, 0xE5):
        w = op & 1
        return fin("in", [("r", w, AX), ("i", b())], w)
    if op in (0xE6, 0xE7):
        w = op & 1
        return fin("out", [("i", b()), ("r", w, AX)], w)
    if op in (0xEC, 0xED):
        w = op & 1
        return fin("in", [("r", w, AX), ("r", 1, DX)], w)
    if op in (0xEE, 0xEF):
        w = op & 1
        return fin("out", [("r", 1, DX), ("r", w, AX)], w)
    if op == 0xE8:
        d = w16()
        ins.target = (p[0] + d) & 0xFFFF
        return fin("call")
    if op == 0xE9:
        d = w16()
        ins.target = (p[0] + d) & 0xFFFF
        return fin("jmp")
    if op == 0xEA:
        off = w16()
        sg = w16()
        ins.target = (sg, off)
        ins.far = True
        return fin("jmpf")
    if op == 0xEB:
        d = s8()
        ins.target = (p[0] + d) & 0xFFFF
        return fin("jmp")
    if op == 0xF4:
        return fin("hlt")
    if op == 0xF5:
        return fin("cmc", [], 1, CF, CF)
    if op in (0xF6, 0xF7):
        w = op & 1
        sub, rm = modrm(w)
        ins.sub = sub
        if sub in (0, 1):
            return fin("test", [rm, ("i", w16() if w else b())], w, 0, ALL)
        if sub == 2:
            return fin("not", [rm], w)
        if sub == 3:
            return fin("neg", [rm], w, 0, ALL)
        if sub == 4:
            return fin("mul", [rm], w, 0, CF | OF | SZP)
        if sub == 5:
            return fin("imul", [rm], w, 0, CF | OF | SZP)
        if sub == 6:
            return fin("div", [rm], w, 0, 0)
        return fin("idiv", [rm], w, 0, 0)
    if op == 0xF8:
        return fin("clc", [], 1, 0, CF)
    if op == 0xF9:
        return fin("stc", [], 1, 0, CF)
    if op == 0xFA:
        return fin("cli")
    if op == 0xFB:
        return fin("sti")
    if op == 0xFC:
        return fin("cld")
    if op == 0xFD:
        return fin("std")
    if op == 0xFE:
        sub, rm = modrm(0)
        if sub == 0:
            return fin("inc", [rm], 0, 0, ALL & ~CF)
        if sub == 1:
            return fin("dec", [rm], 0, 0, ALL & ~CF)
        raise DecodeError("FE /%d" % sub)
    if op == 0xFF:
        sub, rm = modrm(1)
        ins.sub = sub
        if sub == 0:
            return fin("inc", [rm], 1, 0, ALL & ~CF)
        if sub == 1:
            return fin("dec", [rm], 1, 0, ALL & ~CF)
        if sub == 2:
            return fin("callind", [rm])
        if sub == 3:
            if rm[0] != "m":
                raise DecodeError("call far reg")
            return fin("callfind", [rm])
        if sub == 4:
            return fin("jmpind", [rm])
        if sub == 5:
            if rm[0] != "m":
                raise DecodeError("jmp far reg")
            return fin("jmpfind", [rm])
        if sub == 6:
            return fin("push", [rm])
        raise DecodeError("FF /7")
    raise DecodeError("opcode %02X" % op)


def shift_flags(sub, cnt):
    """(reads, writes) for a group-2 op with a nonzero count."""
    if sub in (0, 1):
        return 0, CF | OF
    if sub in (2, 3):
        return CF, CF | OF
    return 0, ALL


# instructions after which execution does not continue at the next address
STOPS = {"jmp", "jmpf", "jmpind", "jmpfind", "ret", "retf", "iret", "hlt"}
