#!/usr/bin/env python3
"""Differential test of the translator's instruction semantics.

Random straight-line and branching 8086 snippets (ALU, INC/DEC, NEG/NOT,
shifts and rotates by 1/imm/CL, MUL/IMUL/DIV/IDIV, the BCD adjusts,
LAHF/SAHF, PUSHF/POPF, XCHG, LEA, LES, string instructions with and without
REP, conditional jumps that exercise the fused compare-and-branch paths, LOOP)
are assembled with nasm and run twice:

* in the oracle CPU (tools/refemu/cpu.py);
* translated by tools/recomp (decoder, liveness, GDScript backend) into a
  GDScript function, transpiled to Python with tools/gdport/gd2py.py, and run.

All registers, the six arithmetic flags, DF and a 256-byte data area must
agree.  The snippet's function returns with every flag live, so the flag
code is fully exercised.

  python3 -I tools/recomp/test_translate.py [--count 400] [--seed 1]
"""
import argparse
import importlib
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "build", "gdpy")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "refemu"))
sys.path.insert(0, os.path.join(ROOT, "tools", "gdport"))
sys.path.insert(0, OUT)

import analysis  # noqa: E402
import gdgen  # noqa: E402
import x86  # noqa: E402

CODE_SEG, DATA_SEG, STACK_SEG, RET_SEG = 0x1000, 0x2000, 0x3000, 0x4000
R16 = ["ax", "cx", "dx", "bx", "sp", "bp", "si", "di"]
REGS = ["ax", "bx", "cx", "dx", "si", "di", "bp"]
R8 = ["al", "bl", "cl", "dl", "ah", "bh", "ch", "dh"]
ALU = ["add", "adc", "sub", "sbb", "and", "or", "xor", "cmp", "test"]
JCC = ["jo", "jno", "jb", "jae", "jz", "jnz", "jbe", "ja", "js", "jns", "jp", "jnp", "jl", "jge", "jle", "jg"]


def rnd_imm(w):
    return random.choice([0, 1, 0x7F, 0x80, 0xFF, 0x7FFF, 0x8000, 0xFFFF, random.randrange(0x10000)]) & (0xFFFF if w else 0xFF)


BASES = ["mov bx, 0x20", "mov si, 0x30", "mov di, 0x50"]


def mem_op(w):
    # (callers re-load BX/SI/DI first: a word at offset FFFFh does not wrap in
    # the translation, a documented limitation the program never hits)
    base = random.choice(["[bx]", "[si]", "[di]", "[bx+si]", "[bx+di+3]", "[0x40]", "[si+0x10]"])
    return ("word " if w else "byte ") + base


def snippet(n):
    lines = ["mov bx, 0x20", "mov si, 0x30", "mov di, 0x50"]
    lab = 0
    for _ in range(n):
        k = random.randrange(22)
        w = random.random() < 0.6
        reg = random.choice(REGS if w else R8)
        if reg in ("bx", "si", "di") and random.random() < 0.7:
            reg = random.choice(["ax", "cx", "dx", "bp"]) if w else "al"
        src = random.choice((REGS if w else R8) + ["imm"])
        if k < 5:
            op = random.choice(ALU)
            s = str(rnd_imm(w)) if src == "imm" else src
            if src != "imm" and (src in R8) != (not w):
                s = str(rnd_imm(w))
            if random.random() < 0.3:
                lines += BASES
                lines.append("%s %s, %s" % (op, mem_op(w), s if s.isdigit() else str(rnd_imm(w))))
            else:
                lines.append("%s %s, %s" % (op, reg, s))
        elif k == 5:
            lines += BASES
            lines.append("%s %s" % (random.choice(["inc", "dec", "neg", "not"]),
                                    reg if random.random() < 0.7 else mem_op(w)))
        elif k == 6:
            op = random.choice(["rol", "ror", "rcl", "rcr", "shl", "shr", "sar"])
            cnt = random.choice(["1", "cl", str(random.randrange(0, 18))])
            if cnt == "cl":
                lines.append("mov cl, %d" % random.choice([0, 1, 2, 7, 8, 9, 15, 16, 17, 31, 33]))
            dst = reg if reg not in ("cl", "cx") else "ax"
            lines.append("%s %s, %s" % (op, dst, cnt))
        elif k == 7:
            lines.append("%s %s" % (random.choice(["mul", "imul"]), random.choice(R8 + REGS)))
        elif k == 8:
            # divide with a safe divisor/dividend
            if random.random() < 0.5:
                lines += ["mov dx, %d" % random.randrange(0, 0x40), "mov bp, %d" % random.randrange(0x41, 0xFFFF),
                          random.choice(["div bp", "idiv bp"]) if True else ""]
                if lines[-1] == "idiv bp":
                    lines[-3] = "cwd"
                    lines[-2] = "mov bp, %d" % random.choice([3, 7, 100, 0xFFF0, 0x8001])
            else:
                lines += ["mov ah, %d" % random.randrange(0, 0x20), "mov ch, %d" % random.randrange(0x21, 0x100),
                          "div ch"]
        elif k == 9:
            lines.append(random.choice(["daa", "das", "aaa", "aas", "aam", "aad", "cbw", "cwd", "lahf",
                                        "sahf", "clc", "stc", "cmc", "salc" if False else "cbw"]))
        elif k == 10:
            lines += ["pushf", "pop ax"] if random.random() < 0.5 else ["mov ax, %d" % rnd_imm(1), "push ax", "popf"]
        elif k == 11:
            a, b = random.sample(REGS, 2)
            lines.append("xchg %s, %s" % (a, b))
        elif k == 12:
            lines.append("lea %s, [bx+si+%d]" % (random.choice(["ax", "cx", "dx", "bp"]), random.randrange(256)))
        elif k == 13:
            lines += ["cld" if random.random() < 0.7 else "std", "push ds", "pop es", "mov cx, %d" % random.randrange(0, 5)]
            lines.append(random.choice(["rep movsb", "rep movsw", "rep stosb", "rep stosw", "repe cmpsb", "repne cmpsb",
                                        "repe scasw", "repne scasb", "lodsb", "lodsw", "movsb", "cmpsw", "scasb"]))
            lines += ["mov si, 0x30", "mov di, 0x50", "cld"]
        elif k in (14, 15, 16, 17):
            # flag-setting op + conditional branch (fused path when flags die)
            op = random.choice(["cmp", "sub", "test", "and", "or", "xor", "add", "inc", "dec"])
            if op in ("inc", "dec"):
                lines.append("%s %s" % (op, reg))
            else:
                s = str(rnd_imm(w)) if src == "imm" or (src in R8) != (not w) else src
                lines.append("%s %s, %s" % (op, reg, s))
            lab += 1
            lines.append("%s L%d" % (random.choice(JCC), lab))
            lines.append("mov bp, 0x1234")
            if random.random() < 0.5:
                lines.append("add bp, ax")
            lines.append("L%d:" % lab)
        elif k == 18:
            lines += ["mov cx, %d" % random.randrange(1, 5)]
            lab += 1
            lines += ["L%d:" % lab, "add ax, cx", random.choice(["loop", "loopz", "loopnz"]) + " L%d" % lab]
        elif k == 19:
            lines += BASES
            lines.append("les %s, [bx]" % random.choice(["ax", "dx", "bp"]))
            lines += ["push ds", "pop es"]
        elif k == 20:
            lines += BASES
            lines.append("mov %s, %s" % (mem_op(w), str(rnd_imm(w))))
        else:
            lines.append("adc %s, %s" % (("ax", "dx") if w else ("al", "dl")))
    lines.append("retf")
    return "\n".join(lines)


def assemble(srcs):
    """Assemble each snippet at CODE_SEG:0x100 (one nasm call per snippet)."""
    out = []
    os.makedirs(OUT, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=OUT) as d:
        for i, src in enumerate(srcs):
            asm = os.path.join(d, "t%d.asm" % i)
            bin_ = os.path.join(d, "t%d.bin" % i)
            with open(asm, "w") as fp:
                fp.write("bits 16\ncpu 186\norg 0x100\n" + src + "\n")
            subprocess.run(["nasm", "-f", "bin", "-o", bin_, asm], check=True)
            out.append(open(bin_, "rb").read())
    return out


def initial_state(seed):
    rng = random.Random(seed)
    regs = {r: rng.randrange(0x10000) for r in ("ax", "cx", "dx", "bp")}
    flags = rng.randrange(0x10000) & 0x0CD5
    data = bytes(rng.randrange(256) for _ in range(256))
    return regs, flags, data


def oracle_run(code, regs, flags, data):
    from cpu import CPU
    cpu = CPU(bytearray(0x100000 + 16))
    m = cpu.mem
    m[(CODE_SEG << 4) + 0x100:(CODE_SEG << 4) + 0x100 + len(code)] = code
    m[DATA_SEG << 4:(DATA_SEG << 4) + 256] = data
    m[RET_SEG << 4] = 0xF4          # hlt
    cpu.sr[:] = [DATA_SEG, CODE_SEG, STACK_SEG, DATA_SEG]
    for k, v in regs.items():
        cpu.r[R16.index(k)] = v
    cpu.r[4] = 0x1000
    cpu.push(RET_SEG)
    cpu.push(0)
    cpu.set_flags(flags | 0x0002)
    cpu.ip = 0x100
    for _ in range(100000):
        cpu.run(1)
        if cpu.halted:
            break
    return cpu


class FakeProg:
    """Just enough of image.Program for Analysis on a snippet."""

    def __init__(self, code):
        self.code = code
        self.base = 0
        self.root_lin = CODE_SEG << 4
        self.root_end = (CODE_SEG << 4) + 0x10000
        self.thunk_at = {}
        self.mgr_seg = 0xFFFF
        self.ovl = {}

    def reader(self, unit):
        lo = (CODE_SEG << 4) + 0x100
        code = self.code

        def read(lin):
            i = lin - lo
            if 0 <= i < len(code):
                return code[i]
            raise IndexError("outside snippet: %05X" % lin)
        return read

    def unit_of(self, lin, from_unit):
        return "root"


def translate(i, code):
    an = analysis.Analysis(FakeProg(code), [], {}, print)
    an.SW_DENSE = an.SW_SPARSE = (0xFFFF, 0xFFFF)
    key = ("root", CODE_SEG, 0x100)
    an.add_entry(key, "test")
    an.run()
    f = an.funcs[key]
    f.fout_min = x86.ALL
    an.compute_regs()
    an.compute_flags()
    f.name = "t%d" % i
    lines = gdgen.gen_function(an, f, lambda g: "")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=400)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--length", type=int, default=12)
    a = ap.parse_args()
    random.seed(a.seed)
    srcs = [snippet(a.length) for _ in range(a.count)]
    codes = assemble(srcs)
    funcs = [translate(i, c) for i, c in enumerate(codes)]
    os.makedirs(OUT, exist_ok=True)
    gd = os.path.join(OUT, "tt_unit.gd")
    with open(gd, "w") as fp:
        fp.write("extends RecompUnit\n\n\n" + "\n\n\n".join(funcs) + "\n")
    host_files = [os.path.join(ROOT, "godot", "host", f) for f in ("cpu_state.gd", "recomp_unit.gd")]
    dst = os.path.join(OUT, "tt_mod.py")
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gdport", "gd2py.py"), dst] + host_files + [gd],
                   check=True)
    if "tt_mod" in sys.modules:
        del sys.modules["tt_mod"]
    mod = importlib.import_module("tt_mod")

    class Host:
        def __init__(self):
            self.st = mod.CpuState()

        def unwind(self, t, site):
            return t

        def poll(self):
            return False

        def fatal(self, t, msg):
            raise RuntimeError(msg)

        def div_fault(self, t):
            raise RuntimeError("div fault")
    fails = 0
    for i, code in enumerate(codes):
        regs, flags, data = initial_state(a.seed * 1000 + i)
        ref = oracle_run(code, regs, flags, data)
        host = Host()
        st = host.st
        unit = mod.tt_unit()
        unit.setup(host)
        mem = st.mem
        mem[(CODE_SEG << 4) + 0x100:(CODE_SEG << 4) + 0x100 + len(code)] = code
        mem[DATA_SEG << 4:(DATA_SEG << 4) + 256] = data
        st.sr[0], st.sr[1], st.sr[2], st.sr[3] = DATA_SEG, CODE_SEG, STACK_SEG, DATA_SEG
        for k, v in regs.items():
            st.r[R16.index(k)] = v
        st.r[4] = 0x1000
        st.push(RET_SEG)
        st.push(0)
        st.set_flags(flags | 0x0002)
        try:
            ret = getattr(unit, "t%d" % i)()
            err = None if ret == (RET_SEG << 16) else "returned %08X" % ret
        except Exception as e:      # noqa: BLE001
            err = "exception %r" % e
        diffs = []
        for j, n in enumerate(R16):
            if ref.r[j] != st.r[j]:
                diffs.append("%s %04X/%04X" % (n, ref.r[j], st.r[j]))
        for j, n in ((0, "es"), (2, "ss"), (3, "ds")):
            if ref.sr[j] != st.sr[j]:
                diffs.append("%s %04X/%04X" % (n, ref.sr[j], st.sr[j]))
        for n in ("cf", "pf", "af", "zf", "sf", "of", "df"):
            if getattr(ref, n) != getattr(st, n):
                diffs.append("%s %d/%d" % (n, getattr(ref, n), getattr(st, n)))
        lo = DATA_SEG << 4
        if bytes(ref.mem[lo:lo + 256]) != bytes(st.mem[lo:lo + 256]):
            diffs.append("data memory")
        lo = (STACK_SEG << 4) + 0xF00
        if bytes(ref.mem[lo:lo + 0x100]) != bytes(st.mem[lo:lo + 0x100]):
            diffs.append("stack memory")
        if err or diffs:
            fails += 1
            if fails <= 5:
                print("FAIL snippet %d: %s %s\n%s\n" % (i, err or "", ", ".join(diffs), srcs[i]))
    print("[test_translate] %d snippets x %d instructions: %d passed, %d failed" % (
        a.count, a.length, a.count - fails, fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
