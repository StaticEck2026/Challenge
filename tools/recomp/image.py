"""Memory images of NFL.EXE at the fixed load segment used by the host.

The host (godot/host/dos.gd, like the oracle's dos.py) loads the program with
its PSP at 0112h, so the load segment is 0122h.  With a fixed load segment the
MZ relocations and every overlay's fixups are applied here, at translation
time, and every far address in the code is a constant.

Units: "root" (the resident image) and overlays 1..11 (overlay 12 is the
preloaded data segment DGROUP).  Each overlay occupies [seg_start, seg_end)
(runtime segments); top-level overlays share 196Dh+base.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "disasm"))
from mzlib import NflExe, apply_relocs  # noqa: E402

PSP_SEG = 0x0112
LOAD_SEG = PSP_SEG + 0x10


class Program:
    def __init__(self, exe_path, base=LOAD_SEG):
        self.exe = NflExe(exe_path)
        self.base = base
        e = self.exe
        self.root = bytes(apply_relocs(e.image, e.relocs, 0, base))
        self.root_lin = base << 4
        self.root_end = self.root_lin + len(self.root)
        self.ovl = {}
        for ov in e.overlays:
            code, rel = e.overlay_bytes(ov)
            img = bytes(apply_relocs(code, rel, ov.seg_start, base))
            start = (ov.seg_start + base) << 4
            self.ovl[ov.num] = dict(num=ov.num, parent=ov.parent, start=start,
                                    end=(ov.seg_end + base) << 4, data=img, info=ov)
        self.mgr_seg = e.mgr_seg + base
        self.table_seg = e.table_seg + base
        self.thunks = []
        for t in e.thunks:
            seg = (t.addr >> 4) + base
            self.thunks.append(dict(cs=self.table_seg, ip=t.addr - (e.table_seg << 4), ovl=t.ovl,
                                    tseg=t.target_seg + base, toff=t.target_off, lin=self.root_lin + t.addr))
            del seg
        self.thunk_at = {(t["cs"], t["ip"]): t for t in self.thunks}
        self.ovl_region = (self.ovl[1]["start"], self.ovl[12]["end"])
        self.dgroup_seg = e.overlays[11].seg_start + base

    # units --------------------------------------------------------------
    def family(self, unit):
        """Overlays whose bytes are present while `unit` runs (unit, its parent, DGROUP)."""
        if unit == "root":
            return [12]
        fam = [unit]
        p = self.ovl[unit]["parent"]
        while p:
            fam.append(p)
            p = self.ovl[p]["parent"]
        fam.append(12)
        return fam

    def reader(self, unit):
        fam = [self.ovl[n] for n in self.family(unit)]
        root, rl, re_ = self.root, self.root_lin, self.root_end

        def read(lin):
            if rl <= lin < re_:
                return root[lin - rl]
            for o in fam:
                if o["start"] <= lin < o["start"] + len(o["data"]):
                    return o["data"][lin - o["start"]]
            raise IndexError("no code byte at %05X for unit %s" % (lin, unit))
        return read

    def unit_of(self, lin, from_unit):
        """Unit that owns linear address `lin` as seen from code of `from_unit`.
        Returns 'root', an overlay number, or None if ambiguous (resident-dependent)."""
        if self.root_lin <= lin < self.root_end:
            return "root"
        if from_unit != "root":
            for n in self.family(from_unit):
                o = self.ovl[n]
                if n != 12 and o["start"] <= lin < o["end"]:
                    return n
        cands = [n for n, o in self.ovl.items() if n != 12 and o["start"] <= lin < o["end"]]
        if len(cands) == 1:
            return cands[0]
        return None

    def candidates(self, lin):
        return [n for n, o in self.ovl.items() if n != 12 and o["start"] <= lin < o["end"]]

    def word(self, unit, lin):
        r = self.reader(unit)
        return r(lin) | (r(lin + 1) << 8)
