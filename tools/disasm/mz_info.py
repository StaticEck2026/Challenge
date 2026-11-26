#!/usr/bin/env python3
"""Dump the MZ header, relocation summary, root segment map and the PLINK86
overlay table of NFL.EXE.

usage: python3 -I tools/disasm/mz_info.py path/to/NFL.EXE [--thunks] [--relocs]

Read-only; never executes anything from the file.
"""

import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mzlib import NflExe  # noqa: E402


def seg_owner(seg, exe):
    """Describe where a link segment lives."""
    if seg < exe.image_paras:
        return "root"
    hits = [o.num for o in exe.overlays if o.seg_start <= seg < o.seg_end]
    if hits:
        return "ovl " + "/".join(str(h) for h in hits)
    return "bss/stack"


def root_segment_map(exe):
    """Segment starts in the root, from relocation values and far-call targets."""
    vals = exe.reloc_values()
    lin = exe.reloc_linear()
    calls = exe.far_refs(exe.image, lin)
    starts = set(v for v in vals if v < exe.image_paras)
    starts.add(0)
    targets = Counter(seg for at, op, seg, off in calls)
    s = sorted(starts)
    rows = []
    for i, seg in enumerate(s):
        end = s[i + 1] if i + 1 < len(s) else exe.image_paras
        rows.append((seg, end, vals.get(seg, 0), targets.get(seg, 0)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exe")
    ap.add_argument("--thunks", action="store_true", help="list every overlay thunk")
    ap.add_argument("--relocs", action="store_true", help="list every root relocation")
    a = ap.parse_args()
    exe = NflExe(a.exe)
    h = exe.hdr

    print("== MZ header ==")
    print(f"file size            {len(exe.data):>8} bytes")
    print(f"e_cblp/e_cp          {h.cblp}/{h.cp} -> load image ends at {exe.image_end} (0x{exe.image_end:X})")
    print(f"e_cparhdr            {h.cparhdr} paragraphs = {exe.header_size} bytes")
    print(f"root image           {len(exe.image)} bytes = 0x{exe.image_paras:X} paragraphs "
          f"(file 0x{exe.header_size:X}-0x{exe.image_end:X})")
    print(f"e_crlc/e_lfarlc      {h.crlc} relocations at file offset {h.lfarlc}")
    print(f"e_minalloc/maxalloc  0x{h.minalloc:04X}/0x{h.maxalloc:04X} paragraphs")
    print(f"initial SS:SP        {h.ss:04X}:{h.sp:04X}")
    print(f"initial CS:IP        {h.cs:04X}:{h.ip:04X}  (linear 0x{exe.entry_linear:X}, file 0x{exe.entry_linear + exe.header_size:X})")
    print(f"e_csum/e_ovno        0x{h.csum:04X}/{h.ovno}")
    print(f"appended data        {len(exe.data) - exe.image_end} bytes after the load image")
    print()

    print("== relocations ==")
    rs = Counter(s for o, s in exe.relocs)
    print(f"{len(exe.relocs)} entries; {len(rs)} distinct fixup-page segments")
    vals = exe.reloc_values()
    print(f"{len(vals)} distinct relocated values (link segments referenced):")
    by_owner = defaultdict(list)
    for v, c in sorted(vals.items()):
        by_owner[seg_owner(v, exe)].append((v, c))
    for k in sorted(by_owner):
        items = by_owner[k]
        print(f"  {k:10s} {len(items):3d} segs, {sum(c for _, c in items):4d} refs: " +
              " ".join(f"{v:04X}x{c}" for v, c in items))
    if a.relocs:
        for o, s in exe.relocs:
            lin = s * 16 + o
            print(f"  {s:04X}:{o:04X} -> {exe.word(lin):04X}")
    print()

    print("== root segment map (link segments; starts = relocated values inside the root) ==")
    print("  seg   end    bytes  refs  farcall-targets")
    for seg, end, refs, tg in root_segment_map(exe):
        tag = ""
        if seg == exe.mgr_seg:
            tag = "  PLINK86 overlay manager code (entry point)"
        elif seg == exe.table_seg:
            tag = "  PLINK86 overlay table + call thunks"
        print(f"  {seg:04X}  {end:04X}  {(end - seg) * 16:6d}  {refs:4d}  {tg:4d}{tag}")
    print()

    print("== PLINK86 overlay table ==")
    print(f"table segment {exe.table_seg:04X} (linear 0x{exe.table_lin:X}, file 0x{exe.table_lin + exe.header_size:X})")
    print(f"header: word0={exe.table_mgr_cs:04X} (manager CS, replaced by load base at start-up), "
          f"word1={exe.table_stack_seg:04X} (manager start-up stack segment, SP=0100)")
    print("records start at table+4, 16 bytes each, terminated by flags=FFFF:")
    print("  +0 flags (bit15 loaded, bit14 preload at start-up, bits0-13 parent overlay)")
    print("  +2 relocation count   +4 load seg start   +6 load seg end (exclusive)")
    print("  +8 file-name offset in table segment   +A/+C file offset in paragraphs (lo/hi)")
    print("  +E image size in paragraphs")
    print()
    print("  ovl flags parent pre nrel  load seg     bytes   file off  code off  file name")
    for o in exe.overlays:
        print(f"  {o.num:3d} {o.flags:04X}  {o.parent:5d}  {'Y' if o.preload else '-':>3} {o.nrel:4d}  "
              f"{o.seg_start:04X}-{o.seg_end:04X}  {o.paras * 16:6d}  0x{o.file_off:06X}  0x{o.code_off:06X}  {o.fname}")
    last = exe.overlays[-1]
    end = last.code_off + last.paras * 16
    print(f"  last overlay ends at file offset {end} (file size {len(exe.data)})")
    print()
    print("overlay tree (children share the parent's area above the parent's end):")

    def tree(parent, depth):
        for o in exe.overlays:
            if o.parent == parent:
                print("  " + "  " * depth + f"ovl {o.num:2d}  {o.seg_start:04X}-{o.seg_end:04X}"
                      + ("  [preloaded]" if o.preload else ""))
                tree(o.num, depth + 1)
    tree(0, 0)
    print()

    print("== overlay call thunks in the root ==")
    if exe.thunk_target_off is not None:
        print(f"{len(exe.thunks)} thunks of the form "
              f"'call far {exe.mgr_seg:04X}:{exe.thunk_target_off:04X} / dw ovl / jmp far seg:off'")
    per = Counter(t.ovl for t in exe.thunks)
    print("thunks per overlay: " + ", ".join(f"{k}:{v}" for k, v in sorted(per.items())))
    if a.thunks:
        for t in exe.thunks:
            print(f"  {exe.table_seg:04X}:{t.addr - exe.table_lin:04X}  ovl {t.ovl:2d} -> {t.target_seg:04X}:{t.target_off:04X}")

    print()
    print("== overlay relocation targets ==")
    for o in exe.overlays:
        code, rel = exe.overlay_bytes(o)
        segs = Counter(s for _, s in rel)
        refs = Counter()
        for off, s in rel:
            lin = (s - o.seg_start) * 16 + off
            if 0 <= lin <= len(code) - 2:
                refs[seg_owner(code[lin] | code[lin + 1] << 8, exe)] += 1
            else:
                refs["outside"] += 1
        print(f"  ovl {o.num:2d}: {len(rel)} fixups in {len(segs)} pages; values point to: "
              + ", ".join(f"{k}={v}" for k, v in sorted(refs.items())))


if __name__ == "__main__":
    main()
