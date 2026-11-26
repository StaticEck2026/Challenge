#!/usr/bin/env python3
"""Static cross-reference of NFL.EXE: interrupts, ports, video segments, CPU level.

usage: python3 -I tools/disasm/xref.py path/to/NFL.EXE [--out build/disasm/xref.txt]

Works on the same recursive-descent analysis as disasm.py (it imports it), so
only *reached* code is considered unless stated otherwise.  For every site it
runs a tiny forward evaluator over the basic block that ends at the site and
reports what it could resolve:

  * direct ``int n`` with AH/AL (``mov ah,xx`` / ``mov ax,xxxx`` patterns);
  * calls of the C runtime wrappers, found by their code signature:
      int86(intno, &in, &out)   (simulates INT via the IVT + IRET; 8-word regs
                                 struct ax,bx,cx,dx,si,di,ds,es)
      intdos(&in, &out)         (same struct, INT 21h)
      bdos(ah, far dx)          (INT 21h)
      outp(port, value) / inp(port)
    with the interrupt number, AH/AL taken from the 'in' struct when it is
    filled with constants in the same block;
  * ``in``/``out`` instructions with immediate port or a constant DX;
  * immediates 0xB800 / 0xB000 / 0x0040 used as segments, and ``mov es/ds``
    with constant 0x0000 / 0x0040 (BIOS data area access);
  * 80186+ opcodes and 8087 escapes in reached vs. unreached bytes.
"""

import argparse
import os
import re
import struct
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mzlib import NflExe  # noqa: E402
import disasm  # noqa: E402

SIGS = {
    "int86": bytes.fromhex("fc1e06558bec8a5e0a80fb25"),
    "intdos": bytes.fromhex("fc1e06558becc45e0ab90800"),
    "bdos": bytes.fromhex("558bec8a66061ec55608cd21"),
    "outp": bytes.fromhex("558bec8b56068b4608b400ee"),
    "inp": bytes.fromhex("558bec8b5606ecb4"),
}

REG16 = ["ax", "cx", "dx", "bx", "sp", "bp", "si", "di"]
REG8 = {"al": ("ax", 0), "ah": ("ax", 8), "bl": ("bx", 0), "bh": ("bx", 8),
        "cl": ("cx", 0), "ch": ("cx", 8), "dl": ("dx", 0), "dh": ("dx", 8)}
IMM = re.compile(r"^(?:byte |word )?(?:\+|-)?0x([0-9a-f]+)$|^(?:byte |word )?(\d+)$")
BPMEM = re.compile(r"^(?:word |byte )?\[bp([+-])0x([0-9a-f]+)\]$")

FNAME = re.compile(r"^(?:[A-Q]|[A-Za-z0-9_]{1,8}\.(?:nfl|NFL|log|ros|ROS)|\.nfl)$")

CPU186 = {0x60: "pusha", 0x61: "popa", 0x62: "bound", 0x68: "push imm16", 0x6A: "push imm8",
          0x69: "imul r,rm,imm16", 0x6B: "imul r,rm,imm8", 0x6C: "insb", 0x6D: "insw",
          0x6E: "outsb", 0x6F: "outsw", 0xC0: "shift rm8,imm8", 0xC1: "shift rm16,imm8",
          0xC8: "enter", 0xC9: "leave", 0x0F: "0F xx (286+)"}


def imm(s):
    m = IMM.match(s.strip())
    if not m:
        return None
    v = int(m.group(1), 16) if m.group(1) else int(m.group(2))
    if s.strip().lstrip("byte word ").startswith("-"):
        v = -v
    return v & 0xFFFF


def bpslot(s):
    m = BPMEM.match(s.strip())
    if not m:
        return None
    v = int(m.group(2), 16)
    return -v if m.group(1) == "-" else v


class Eval:
    """Forward evaluator for a straight-line block (constants only)."""

    def __init__(self):
        self.r = {}
        self.mem = {}      # bp-relative word slots -> value
        self.stack = []

    def get(self, op):
        op = op.strip()
        if op in self.r:
            return self.r[op]
        if op in REG8:
            reg, sh = REG8[op]
            v = self.r.get(reg)
            return None if v is None else (v >> sh) & 0xFF
        v = imm(op)
        if v is not None:
            return v
        s = bpslot(op)
        if s is not None:
            return self.mem.get(s)
        return None

    def set(self, op, v):
        op = op.strip()
        if op in REG16 or op in ("es", "ds", "ss"):
            self.r[op] = v
        elif op in REG8:
            reg, sh = REG8[op]
            old = self.r.get(reg)
            if v is None or isinstance(v, tuple):
                self.r[reg] = None
            elif isinstance(old, int):
                mask = 0xFF << sh
                self.r[reg] = (old & ~mask & 0xFFFF) | ((v & 0xFF) << sh)
            elif sh == 8:
                self.r[reg] = ("hi", v & 0xFF)       # only the high half is known
            elif isinstance(old, tuple) and old[0] == "hi":
                self.r[reg] = (old[1] << 8) | (v & 0xFF)
            else:
                self.r[reg] = None
        else:
            s = bpslot(op)
            if s is not None:
                self.mem[s] = v

    def ah(self):
        v = self.r.get("ax")
        if isinstance(v, tuple):
            return v[1], None
        if v is None:
            return None, None
        return (v >> 8) & 0xFF, v & 0xFF

    def step(self, mn, ops):
        a = [x.strip() for x in ops.split(",")] if ops else []
        if mn == "mov" and len(a) == 2:
            v = self.get(a[1])
            if isinstance(v, tuple) and v[0] not in ("str", "lea"):
                v = None
            if isinstance(v, tuple) and a[0] in REG8:
                v = None
            self.set(a[0], v)
        elif mn == "lea" and len(a) == 2:
            s = bpslot(a[1])
            m = re.match(r"^\[0x([0-9a-f]+)\]$", a[1])
            if s is not None:
                self.set(a[0], ("lea", s))
            elif m:
                self.set(a[0], ("str", int(m.group(1), 16)))
            else:
                self.set(a[0], None)
        elif mn == "xor" and len(a) == 2 and a[0] == a[1]:
            self.set(a[0], 0)
        elif mn == "push" and a:
            v = self.get(a[0]) if a[0] not in ("ss", "ds", "es", "cs") else a[0]
            if a[0] in self.r and isinstance(self.r[a[0]], tuple) and \
                    self.r[a[0]][0] in ("lea", "str"):
                v = self.r[a[0]]
            self.stack.append(v)
        elif mn == "pop" and a:
            v = self.stack.pop() if self.stack else None
            self.set(a[0], v if not isinstance(v, str) else None)
        elif mn == "add" and a == ["sp", a[1]] and imm(a[1]) is not None:
            for _ in range(imm(a[1]) // 2):
                if self.stack:
                    self.stack.pop()
        elif mn in ("call",):
            self.r = {}
        elif mn in ("and", "or", "add", "sub", "shl", "shr", "inc", "dec", "neg", "not",
                    "adc", "sbb", "xor", "cbw", "cwd", "mul", "imul", "div", "idiv",
                    "lodsb", "lodsw", "in", "xchg", "les", "lds", "sar", "rol", "ror",
                    "rcl", "rcr", "sal", "daa", "das", "aaa", "aas", "aam", "aad", "lahf"):
            if mn == "and" and len(a) == 2 and self.get(a[0]) is not None and imm(a[1]) is not None:
                self.set(a[0], self.get(a[0]) & imm(a[1]))
            elif mn == "or" and len(a) == 2 and self.get(a[0]) is not None and imm(a[1]) is not None:
                self.set(a[0], self.get(a[0]) | imm(a[1]))
            elif mn in ("cbw", "cwd", "mul", "imul", "div", "idiv", "lodsb", "lodsw",
                        "in", "daa", "das", "aaa", "aas", "aam", "aad", "lahf"):
                self.r["ax"] = None
                if mn in ("cwd", "mul", "imul", "div", "idiv"):
                    self.r["dx"] = None
            elif a:
                self.set(a[0], None)
                if mn in ("xchg", "les", "lds") and len(a) == 2:
                    self.set(a[1], None)


def fmt(v, w=2):
    if v is None or isinstance(v, (tuple, str)):
        return "??" if w == 2 else "????"
    return f"{v:0{w}X}"


class XRef:
    def __init__(self, exe):
        self.exe = exe
        self.d = disasm.Disassembler(exe, 0)
        dg = [o for o in exe.overlays if o.preload][0]
        self.dgroup = exe.overlay_bytes(dg)[0]   # DS = DGROUP = preloaded overlay
        self.recs = {}       # unit -> list of (lin, fseg, off, bytes, mn, ops, reached)
        self.wrappers = {}   # (seg, off) -> name
        for u in self.d.units:
            if u.ovl is not None and u.ovl.preload and not self.d.entries[u.name]:
                continue
            insns, order, frame, inline = self.d.analyse(u)
            home = sorted(s for s in self.d.segstarts[u.name] if u.contains(s))
            out = []
            skip = -1
            for lin in order:
                if lin < skip:
                    continue
                if lin in inline and inline[lin][0]:
                    skip = lin + inline[lin][0]
                    out.append((lin, None, 0, b"", "<inline>", "", True))
                    continue
                hexb, text = insns[lin]
                b = bytes.fromhex(hexb)
                fseg = frame.get(lin)
                if fseg is None:
                    fseg = u.base_seg
                    for s in home:
                        if (s - u.base_seg) * 16 <= lin:
                            fseg = s
                off = lin - (fseg - u.base_seg) * 16
                mn, ops = disasm_classify(text)
                out.append((lin, fseg, off, b, mn, ops, lin in frame))
            self.recs[u.name] = out
        for (seg, off) in {e for es in self.d.entries.values() for e in es}:
            u = self.d.unit_for(seg, self.d.root)
            if u is None:
                continue
            lin = (seg - u.base_seg) * 16 + off
            for name, sig in SIGS.items():
                if u.data[lin:lin + len(sig)] == sig:
                    self.wrappers[(seg, off)] = name

    def blocks(self, recs, i):
        """Index where the basic block containing recs[i] starts (approx.)."""
        j = i
        while j > 0 and i - j < 60:
            mn = recs[j - 1][4]
            if mn in ("jmp", "ret", "retf", "iret", "<inline>") or mn.startswith("j") or \
                    mn.startswith("loop"):
                break
            if recs[j][4] == "push" and recs[j][5] == "bp" and j + 1 < len(recs) and \
                    recs[j + 1][4] == "mov" and recs[j + 1][5] == "bp,sp":
                break
            j -= 1
        return j

    def evaluate(self, recs, i):
        ev = Eval()
        for k in range(self.blocks(recs, i), i):
            ev.step(recs[k][4], recs[k][5])
        return ev

    def run(self):
        R = defaultdict(list)
        cpu = {True: Counter(), False: Counter()}
        cpu_sites = defaultdict(list)
        for uname, recs in self.recs.items():
            for i, (lin, fseg, off, b, mn, ops, reached) in enumerate(recs):
                if not b:
                    continue
                where = f"{uname:6s} {fseg:04X}:{off:04X}"
                k = 0
                while k < len(b) and b[k] in disasm.PREFIXES:
                    k += 1
                op0 = b[k] if k < len(b) else None
                if op0 in CPU186:
                    cpu[reached][CPU186[op0]] += 1
                    if reached:
                        cpu_sites[CPU186[op0]].append(where)
                if op0 is not None and (0xD8 <= op0 <= 0xDF or op0 == 0x9B):
                    cpu[reached]["8087 ESC/WAIT"] += 1
                    if reached:
                        cpu_sites["8087 ESC/WAIT"].append(f"{where} {mn} {ops}")
                if not reached:
                    continue
                if mn == "int":
                    n = imm(ops)
                    ev = self.evaluate(recs, i)
                    ah, al = ev.ah()
                    R["int"].append((n, ah, al, where, "direct"))
                elif mn in ("in", "out"):
                    a = [x.strip() for x in ops.split(",")]
                    port = a[1] if mn == "in" else a[0]
                    p = imm(port)
                    if port == "dx":
                        p = self.evaluate(recs, i).get("dx")
                    R["port"].append((mn, p, where, ops))
                elif mn == "call" and len(b) == 5 and b[0] == 0x9A:
                    toff, tseg = struct.unpack_from("<HH", b, 1)
                    w = self.wrappers.get((tseg, toff))
                    ev = None
                    if any(r[4] == "lea" and "[0x" in r[5] for r in recs[max(0, i - 12):i]):
                        ev = self.evaluate(recs, i)
                        strs = [self.dstr(v[1]) for v in reversed(ev.stack)
                                if isinstance(v, tuple) and v[0] == "str"]
                        if any(FNAME.match(t) for t in strs):
                            R["fname"].append((tseg, toff, strs, where))
                    if w:
                        ev = self.evaluate(recs, i)
                        st = ev.stack
                        args = list(reversed(st))
                        R["wrap"].append((w, args, ev, where))
                # segment immediates
                for tok in re.findall(r"0x[0-9a-f]+", ops):
                    v = int(tok, 16)
                    if v in (0xB800, 0xB000) and mn in ("mov", "push", "cmp") and \
                            not re.search(r"\[[^]]*" + tok, ops):
                        R["vseg"].append((v, where, f"{mn} {ops}"))
                if mn == "mov" and ops.split(",")[0] in ("es", "ds"):
                    src = ops.split(",")[1].strip()
                    direct = any(r[4] in ("mov", "xor") and r[5].startswith(src + ",")
                                 and (imm(r[5].split(",")[1]) is not None or r[5] == f"{src},{src}")
                                 for r in recs[max(0, i - 3):i])
                    v = self.evaluate(recs, i).get(src) if direct else None
                    if v in (0, 0x40, 0xB800, 0xB000, 0xF000):
                        R["segload"].append((v, where, f"{mn} {ops}",
                                             self.peek_mem(recs, i, ops.split(",")[0])))
        return R, cpu, cpu_sites

    def dstr(self, off):
        d = self.dgroup
        end = d.find(b"\0", off)
        if off >= len(d) or end < 0 or end - off > 80:
            return "?"
        return d[off:end].decode("latin-1")

    @staticmethod
    def peek_mem(recs, i, sreg):
        out = []
        for k in range(i + 1, min(i + 12, len(recs))):
            mn, ops = recs[k][4], recs[k][5]
            if mn in ("ret", "retf", "jmp", "call"):
                break
            if "[" in ops and (f"{sreg}:" in ops or sreg == "ds"):
                out.append(f"{mn} {ops}")
        return out


def disasm_classify(text):
    parts = text.split(None, 1)
    mn = parts[0] if parts else ""
    ops = parts[1] if len(parts) > 1 else ""
    while mn in ("rep", "repe", "repne", "lock", "cs", "ds", "es", "ss") and ops:
        p2 = ops.split(None, 1)
        mn = p2[0]
        ops = p2[1] if len(p2) > 1 else ""
    return mn, ops


def struct_regs(ev, ptr):
    """AX/BX/CX/DX values stored into a bp-relative regs struct."""
    if not (isinstance(ptr, tuple) and ptr[0] == "lea" and ptr[1] is not None):
        return None
    base = ptr[1]
    return {r: ev.mem.get(base + 2 * k) for k, r in enumerate(("ax", "bx", "cx", "dx"))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exe")
    ap.add_argument("--out", default="build/disasm/xref.txt")
    a = ap.parse_args()
    exe = NflExe(a.exe)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    disasm.TMPDIR = os.path.dirname(a.out) or "."
    x = XRef(exe)
    R, cpu, cpu_sites = x.run()
    lines = []
    P = lines.append
    P("== C runtime wrappers found by signature ==")
    for (s, o), n in sorted(x.wrappers.items()):
        P(f"  {s:04X}:{o:04X}  {n}")
    P("")
    P("== interrupt sites (reached code) ==")
    summ = Counter()
    for n, ah, al, where, how in R["int"]:
        P(f"  INT {fmt(n)}h AH={fmt(ah)} AL={fmt(al)}  {where}  ({how})")
        summ[(n, ah)] += 1
    for w, args, ev, where in R["wrap"]:
        if w == "int86":
            n = args[0] if args else None
            regs = struct_regs(ev, args[1] if len(args) > 1 else None) or {}
            axv = regs.get("ax")
            ah = None if axv is None else (axv >> 8) & 0xFF
            al = None if axv is None else axv & 0xFF
            extra = " ".join(f"{r.upper()}={fmt(v, 4)}" for r, v in regs.items() if v is not None)
            P(f"  INT {fmt(n)}h AH={fmt(ah)} AL={fmt(al)}  {where}  (int86) {extra}")
            summ[(n, ah)] += 1
        elif w == "intdos":
            regs = struct_regs(ev, args[0] if args else None) or {}
            axv = regs.get("ax")
            ah = None if axv is None else (axv >> 8) & 0xFF
            al = None if axv is None else axv & 0xFF
            P(f"  INT 21h AH={fmt(ah)} AL={fmt(al)}  {where}  (intdos)")
            summ[(0x21, ah)] += 1
        elif w == "bdos":
            ah = args[0] if args else None
            P(f"  INT 21h AH={fmt(ah)}        {where}  (bdos)")
            summ[(0x21, ah)] += 1
    P("")
    P("summary (INT, AH) -> sites:")
    for (n, ah), c in sorted(summ.items(), key=lambda t: (t[0][0] or 0, t[0][1] if isinstance(t[0][1], int) else 999)):
        P(f"  INT {fmt(n)}h AH={fmt(ah)}: {c}")
    P("")
    P("== port I/O (reached code) ==")
    for mn, p, where, ops in R["port"]:
        P(f"  {mn:3s} port {fmt(p, 4)}  {where}  {mn} {ops}")
    for w, args, ev, where in R["wrap"]:
        if w == "outp":
            P(f"  out port {fmt(args[0] if args else None, 4)} value {fmt(args[1] if len(args) > 1 else None)}  {where}  (outp)")
        elif w == "inp":
            P(f"  in  port {fmt(args[0] if args else None, 4)}  {where}  (inp)")
    P("")
    P("== calls with file-name string literals (callee, string args in order) ==")
    for tseg, toff, strs, where in R["fname"]:
        P(f"  {where}  call {tseg:04X}:{toff:04X}  " + ", ".join(repr(t) for t in strs))
    P("")
    P("== video segment immediates ==")
    for v, where, t in R["vseg"]:
        P(f"  {v:04X}  {where}  {t}")
    P("")
    P("== segment registers loaded with 0000/0040/B800/B000/F000 ==")
    for v, where, t, mem in R["segload"]:
        P(f"  {v:04X}  {where}  {t}   next: {'; '.join(mem[:4])}")
    P("")
    P("== CPU level ==")
    for reached in (True, False):
        P(f"  {'reached' if reached else 'unreached (data / dead code)'}: "
          + (", ".join(f"{k}={v}" for k, v in sorted(cpu[reached].items())) or "none"))
    for k, sites in sorted(cpu_sites.items()):
        P(f"  reached {k}: " + ", ".join(sites[:10]) + (" ..." if len(sites) > 10 else ""))
    with open(a.out, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
