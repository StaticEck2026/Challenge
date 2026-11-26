#!/usr/bin/env python3
"""Reproducible disassembly of NFL.EXE (root image + PLINK86 overlays).

usage: python3 -I tools/disasm/disasm.py path/to/NFL.EXE [--out build/disasm] [--base 0]

For every unit (the root image and each overlay):
  * relocations are applied for load segment ``--base`` (default 0, so every
    segment immediate in the listing is the *link* segment that mz_info.py and
    the docs use; pass e.g. --base 0x1A2 to see the immediates a real machine
    would hold -- the SSSS:OOOO address column always stays in link segments);
  * the unit is disassembled with ``ndisasm -b16`` and a recursive descent
    marks which instructions are reachable from known entry points: header
    CS:IP, every relocated far call/jmp target, overlay thunk targets, and the
    targets of the C runtime's inline ``switch`` tables.  Near branch targets
    are computed from the instruction bytes inside the 64 KB frame of the
    segment the code was reached from (PLINK86 segment numbers are paragraph
    rounded, so a function's tail may lie past the next segment's start).
    Unreached bytes are kept but marked '?', so data mixed into code is
    visible.  Reached code is marked ' '.

Output (gitignored, never commit it):
  <out>/root.asm, <out>/ovlNN.asm   listings, one line per instruction:
        "<flag> SSSS:OOOO  HEXBYTES  mnemonic operands   ; annotations"
        annotation 'R' = the instruction contains a relocated (segment) word
  <out>/ovl12.hex                   hex/ASCII dump of the preloaded data overlay
  <out>/index.txt                   segment list and reachability statistics
"""

import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mzlib import NflExe, apply_relocs  # noqa: E402

# Signatures of helpers that are followed by inline data at their call site.
SIG_SWITCH_DENSE = bytes.fromhex("5f075b262b1d")     # pop di/pop es/pop bx/sub bx,[es:di]
SIG_SWITCH_SPARSE = bytes.fromhex("5f0758268b0d")    # pop di/pop es/pop ax/mov cx,[es:di]
SIG_OVL_STUB = bytes.fromhex("9c50535152555657")    # pushf/push ax..di (PLINK86 load stub)

LINE_RE = re.compile(r"^([0-9A-F]{8})  ([0-9A-F]+)\s+(.*)$")
CONT_RE = re.compile(r"^\s+-([0-9A-F]+)\s*$")
PREFIXES = {0x26, 0x2E, 0x36, 0x3E, 0xF0, 0xF2, 0xF3}

TMPDIR = None


class Unit:
    def __init__(self, name, base_seg, data, relocs_lin, ovl=None):
        self.name = name
        self.base_seg = base_seg
        self.data = data
        self.reloc_starts = set(relocs_lin)
        self.relocs_lin = set()
        for a in relocs_lin:
            self.relocs_lin.update((a, a + 1))
        self.ovl = ovl
        self.paras = (len(data) + 15) // 16

    def contains(self, seg):
        return self.base_seg <= seg < self.base_seg + self.paras


def run_ndisasm(blob, syncs):
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False, dir=TMPDIR) as f:
        f.write(blob)
        path = f.name
    try:
        args = ["ndisasm", "-b16", "-o0"]
        for s in sorted(syncs):
            if 0 <= s < len(blob):
                args += ["-s", str(s)]
        args.append(path)
        out = subprocess.run(args, check=True, capture_output=True, text=True).stdout
    finally:
        os.unlink(path)
    insns = {}
    order = []
    last = None
    for line in out.splitlines():
        m = LINE_RE.match(line)
        if m:
            off = int(m.group(1), 16)
            insns[off] = [m.group(2), m.group(3)]
            order.append(off)
            last = off
            continue
        m = CONT_RE.match(line)
        if m and last is not None:
            insns[last][0] += m.group(1)
    return insns, order


def flow(code):
    """Control flow of one instruction from its bytes.

    Returns (kind, value): kind in 'next', 'stop', 'jmp', 'jcc', 'call',
    'callfar', 'jmpfar'; value = rel displacement or (seg, off) for far.
    """
    i = 0
    while i < len(code) and code[i] in PREFIXES:
        i += 1
    if i >= len(code):
        return "next", None
    op = code[i]
    if op in (0xC3, 0xC2, 0xCB, 0xCA, 0xCF, 0xF4):
        return "stop", None
    if op == 0xCD and i + 1 < len(code) and code[i + 1] == 0x20:
        return "stop", None
    if op == 0xEB:
        return "jmp", struct.unpack_from("<b", code, i + 1)[0]
    if op == 0xE9:
        return "jmp", struct.unpack_from("<h", code, i + 1)[0]
    if 0x70 <= op <= 0x7F or 0xE0 <= op <= 0xE3:
        return "jcc", struct.unpack_from("<b", code, i + 1)[0]
    if op == 0xE8:
        return "call", struct.unpack_from("<h", code, i + 1)[0]
    if op == 0x9A:
        off, seg = struct.unpack_from("<HH", code, i + 1)
        return "callfar", (seg, off)
    if op == 0xEA:
        off, seg = struct.unpack_from("<HH", code, i + 1)
        return "jmpfar", (seg, off)
    if op == 0xFF and i + 1 < len(code):
        reg = (code[i + 1] >> 3) & 7
        if reg in (4, 5):          # jmp near/far indirect
            return "stop", None
    return "next", None


class Disassembler:
    def __init__(self, exe, base):
        self.exe = exe
        self.base = base
        self.units = []
        img = apply_relocs(exe.image, [(o, s) for o, s in exe.relocs], 0, base)
        self.root = Unit("root", 0, bytes(img), exe.reloc_linear())
        self.units.append(self.root)
        for ov in exe.overlays:
            code, rel = exe.overlay_bytes(ov)
            code = apply_relocs(code, rel, ov.seg_start, base)
            lin = [(s - ov.seg_start) * 16 + o for o, s in rel]
            self.units.append(Unit(f"ovl{ov.num:02d}", ov.seg_start, bytes(code), lin, ov))
        self.entries = defaultdict(set)     # unit name -> {(frame_seg, off)}
        self.segstarts = defaultdict(set)
        self.notes = defaultdict(dict)      # unit name -> {linear: note}
        self.helpers = {}                   # (seg, off) -> kind
        self._collect()

    def raw_word(self, unit, lin):
        """Word at ``lin`` with relocation removed (link value)."""
        v = struct.unpack_from("<H", unit.data, lin)[0]
        if lin in unit.relocs_lin:
            v = (v - self.base) & 0xFFFF
        return v

    def unit_for(self, seg, src):
        """Which unit does link segment ``seg`` referenced from ``src`` mean?"""
        if self.root.contains(seg):
            return self.root
        cands = [u for u in self.units[1:] if u.contains(seg)]
        if not cands:
            return None
        if src in cands:
            return src
        if src.ovl is not None:
            for u in cands:   # parent of the referencing overlay, or a child of it
                if u.ovl.num == src.ovl.parent or u.ovl.parent == src.ovl.num:
                    return u
        return None   # ambiguous (root -> overlay area is only legal via a thunk)

    def far_refs(self, u):
        """(insn_linear, opcode, link_seg, off) for relocated far call/jmp in u."""
        for lin in sorted(u.reloc_starts):
            if lin >= 3 and u.data[lin - 3] in (0x9A, 0xEA):
                seg = self.raw_word(u, lin)
                off = struct.unpack_from("<H", u.data, lin - 2)[0]
                yield lin - 3, u.data[lin - 3], seg, off

    def _collect(self):
        exe = self.exe
        self.entries["root"].add((exe.hdr.cs, exe.hdr.ip))
        for u in self.units:
            for at, op, seg, off in self.far_refs(u):
                tu = self.unit_for(seg, u)
                if tu is None:
                    continue
                t = (seg - tu.base_seg) * 16 + off
                sig = tu.data[t:t + 8]
                if sig.startswith(SIG_SWITCH_DENSE):
                    self.helpers[(seg, off)] = "switch_dense"
                elif sig.startswith(SIG_SWITCH_SPARSE):
                    self.helpers[(seg, off)] = "switch_sparse"
                elif sig.startswith(SIG_OVL_STUB):
                    self.helpers[(seg, off)] = "ovl_stub"
        for u in self.units:
            for lin in u.reloc_starts:
                seg = self.raw_word(u, lin)
                tu = self.unit_for(seg, u)
                if tu is not None:
                    self.segstarts[tu.name].add(seg)
            for at, op, seg, off in self.far_refs(u):
                tu = self.unit_for(seg, u)
                if tu is None:
                    continue
                self.entries[tu.name].add((seg, off))
        for t in exe.thunks:
            ou = self.units[t.ovl]
            self.segstarts[ou.name].add(t.target_seg)
            self.entries[ou.name].add((t.target_seg, t.target_off))
            self.notes["root"][t.addr] = (f"overlay thunk -> ovl {t.ovl} "
                                          f"{t.target_seg:04X}:{t.target_off:04X}")
        for u in self.units:
            self.segstarts[u.name].add(u.base_seg)

    @staticmethod
    def parse_switch(kind, blob, at):
        try:
            if kind == "switch_dense":
                lo_v, cnt, dflt = struct.unpack_from("<hHH", blob, at)
                if cnt > 512:
                    raise struct.error
                tg = [struct.unpack_from("<H", blob, at + 6 + 2 * i)[0] for i in range(cnt)]
                note = (f"switch table: min {lo_v}, {cnt} cases, default {dflt:04X}; "
                        + " ".join(f"{lo_v + i}:{t:04X}" for i, t in enumerate(tg)))
                return 6 + 2 * cnt, [dflt] + tg, note
            n = struct.unpack_from("<H", blob, at)[0]
            if n > 512:
                raise struct.error
            vals = [struct.unpack_from("<h", blob, at + 2 + 2 * i)[0] for i in range(n)]
            dflt = struct.unpack_from("<H", blob, at + 2 + 2 * n)[0]
            tg = [struct.unpack_from("<H", blob, at + 4 + 2 * n + 2 * i)[0] for i in range(n)]
            note = (f"sparse switch: {n} cases, default {dflt:04X}; "
                    + " ".join(f"{v}:{t:04X}" for v, t in zip(vals, tg)))
            return 4 + 4 * n, [dflt] + tg, note
        except struct.error:
            return 0, [], "switch table (unparsed)"

    def analyse(self, unit):
        blob = unit.data
        bseg = unit.base_seg
        entries = []
        for seg, off in self.entries[unit.name]:
            lin = (seg - bseg) * 16 + off
            if 0 <= lin < len(blob):
                entries.append((lin, seg))
        syncs = set(l for l, _ in entries)
        inline = {}        # linear -> (length, note)
        for _ in range(60):
            insns, order = run_ndisasm(blob, syncs)
            frame = {}     # linear -> frame segment it was reached with
            work = list(entries)
            new_sync = set()
            while work:
                lin, fseg = work.pop()
                while True:
                    if lin in frame or lin < 0 or lin >= len(blob):
                        break
                    if lin not in insns:
                        new_sync.add(lin)
                        break
                    hexb, text = insns[lin]
                    ln = len(hexb) // 2
                    frame[lin] = fseg
                    fbase = (fseg - bseg) * 16
                    ip_next = (lin - fbase + ln) & 0xFFFF
                    nxt = lin + ln
                    kind, val = flow(blob[lin:lin + ln])
                    if kind == "stop":
                        break
                    if kind in ("jmp", "jcc", "call"):
                        tgt = fbase + ((ip_next + val) & 0xFFFF)
                        work.append((tgt, fseg))
                        if kind == "jmp":
                            break
                    elif kind == "jmpfar":
                        break
                    elif kind == "callfar" and lin + 3 in unit.reloc_starts:
                        seg = self.raw_word(unit, lin + 3)
                        h = self.helpers.get((seg, val[1]))
                        if h == "ovl_stub":
                            n = struct.unpack_from("<H", blob, nxt)[0]
                            inline[nxt] = (2, f"dw {n} ; overlay number for the load stub")
                            nxt += 2
                            new_sync.add(nxt)
                        elif h in ("switch_dense", "switch_sparse"):
                            tl, tg, note = self.parse_switch(h, blob, nxt)
                            inline[nxt] = (tl, note)
                            for t in tg:
                                tl_ = fbase + t
                                work.append((tl_, fseg))
                                new_sync.add(tl_)
                            break
                    lin = nxt
            new_sync -= set(insns)
            new_sync -= syncs
            if not new_sync:
                break
            syncs |= new_sync
        return insns, order, frame, inline

    def render_unit(self, unit, fh):
        insns, order, frame, inline = self.analyse(unit)
        blob = unit.data
        bseg = unit.base_seg
        starts = sorted(s for s in self.segstarts[unit.name] if unit.contains(s))
        entry_lin = defaultdict(list)
        for seg, off in self.entries[unit.name]:
            entry_lin[(seg - bseg) * 16 + off].append((seg, off))

        def home_seg(lin):
            seg = bseg
            for s in starts:
                if (s - bseg) * 16 <= lin:
                    seg = s
                else:
                    break
            return seg

        cur = None
        reached = 0
        skip_until = -1
        per_seg = defaultdict(lambda: [0, 0])
        for lin in order:
            if lin < skip_until:
                continue
            if lin in inline:
                ln, note = inline[lin]
                if ln:
                    fseg = frame.get(lin - 1, home_seg(lin))
                    off = lin - (fseg - bseg) * 16
                    fh.write(f"  {fseg:04X}:{off:04X}  {blob[lin:lin + ln].hex().upper()[:24]:24s}"
                             f"  ; inline data: {note}\n")
                    skip_until = lin + ln
                    reached += ln
                    continue
            hexb, text = insns[lin]
            ln = len(hexb) // 2
            hs = home_seg(lin)
            fseg = frame.get(lin, hs)
            if hs != cur:
                cur = hs
                fh.write(f"\n;; ===== segment {hs:04X} (unit {unit.name}, linear "
                         f"0x{(hs - bseg) * 16:05X}) =====\n")
            off = lin - (fseg - bseg) * 16
            kind, val = flow(blob[lin:lin + ln])
            if kind in ("jmp", "jcc", "call") and off >= 0:
                # ndisasm printed a unit-linear target; show it frame-relative
                tgt = (off + ln + val) & 0xFFFF
                text = re.sub(r"0x[0-9a-f]+$", f"0x{tgt:x}", text)
            flag = " " if lin in frame else "?"
            per_seg[hs][0] += ln
            if lin in frame:
                reached += ln
                per_seg[hs][1] += ln
            for seg, eoff in sorted(entry_lin.get(lin, ())):
                fh.write(f"{seg:04X}:{eoff:04X}:\n")
            ann = []
            if any(a in unit.relocs_lin for a in range(lin, lin + ln)):
                ann.append("R")
            note = self.notes[unit.name].get(lin)
            if note:
                ann.append(note)
            line = f"{flag} {fseg:04X}:{off:04X}  {hexb[:24]:24s}  {text}"
            if ann:
                line = f"{line:72s} ; " + " ".join(ann)
            fh.write(line + "\n")
        return per_seg, reached


def hexdump(data, base_seg, fh):
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        asc = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        fh.write(f"{base_seg:04X}:{i:04X}  {chunk.hex(' '):47s}  {asc}\n")


def main():
    global TMPDIR
    ap = argparse.ArgumentParser()
    ap.add_argument("exe")
    ap.add_argument("--out", default="build/disasm")
    ap.add_argument("--base", default="0", help="load segment to relocate for (default 0)")
    ap.add_argument("--only", help="comma list of units (root,ovl01,...)")
    a = ap.parse_args()
    base = int(a.base, 0)
    exe = NflExe(a.exe)
    os.makedirs(a.out, exist_ok=True)
    TMPDIR = a.out
    d = Disassembler(exe, base)
    only = set(a.only.split(",")) if a.only else None
    with open(os.path.join(a.out, "index.txt"), "w") as idx:
        idx.write(f"NFL.EXE disassembly, load base {base:04X}\n")
        idx.write("helpers with inline data: " + ", ".join(
            f"{s:04X}:{o:04X}={k}" for (s, o), k in sorted(d.helpers.items())) + "\n")
        for u in d.units:
            if only and u.name not in only:
                continue
            if u.ovl is not None and u.ovl.preload and not d.entries[u.name]:
                path = os.path.join(a.out, u.name + ".hex")
                with open(path, "w") as fh:
                    fh.write(f";; {u.name}: preloaded data overlay (DGROUP) at {u.base_seg:04X}\n")
                    hexdump(u.data, u.base_seg, fh)
                idx.write(f"\n{u.name}: data overlay, hex dump in {path}\n")
                print(f"{u.name}: data -> {path}")
                continue
            path = os.path.join(a.out, u.name + ".asm")
            with open(path, "w") as fh:
                fh.write(f";; {u.name}: link segment {u.base_seg:04X}, {len(u.data)} bytes, "
                         f"relocated for load base {base:04X}\n")
                fh.write(";; flag ' ' = reached by recursive descent, '?' = not reached "
                         "(data, or only reachable through pointers)\n")
                per_seg, reached = d.render_unit(u, fh)
            idx.write(f"\n{u.name}: {len(per_seg)} segments, {len(u.data)} bytes, "
                      f"{reached} bytes reached\n")
            for seg in sorted(per_seg):
                n, r = per_seg[seg]
                idx.write(f"  {seg:04X} {n:6d} bytes  {r:6d} reached\n")
            print(f"{u.name}: {len(per_seg)} segments, {reached}/{len(u.data)} bytes reached -> {path}")


if __name__ == "__main__":
    main()
