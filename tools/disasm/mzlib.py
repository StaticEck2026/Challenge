"""Shared parsing code for NFL.EXE (MZ + PLINK86 overlays).

Everything here only *reads* the executable; nothing is executed.
Scripts in this directory are meant to be run with ``python3 -I``; they add
their own directory to ``sys.path`` explicitly so this module can be imported.

Terminology
-----------
* "link segment": a segment number as the linker assigned it, i.e. relative
  to load segment 0.  At run time the DOS loader adds the real load segment
  (PSP + 10h) to every relocated word, so ``runtime = link + load_base``.
* root image: the part of the file the DOS loader loads (header excluded).
* overlay: a PLINK86 overlay stored after the root image.  Each overlay starts
  with its own relocation table (4-byte entries, padded to a paragraph),
  followed by the code/data image that is read to its link segment.
"""

import struct
from collections import Counter, namedtuple

MZHeader = namedtuple(
    "MZHeader",
    "magic cblp cp crlc cparhdr minalloc maxalloc ss sp csum ip cs lfarlc ovno",
)

Overlay = namedtuple(
    "Overlay",
    "num rec_off flags parent preload nrel seg_start seg_end fname_off fname "
    "file_off code_off paras",
)

Thunk = namedtuple("Thunk", "addr ovl target_seg target_off")


class NflExe:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        d = self.data
        self.hdr = MZHeader(*struct.unpack_from("<2s13H", d, 0))
        if self.hdr.magic not in (b"MZ", b"ZM"):
            raise ValueError("not an MZ executable")
        h = self.hdr
        self.image_end = (h.cp - 1) * 512 + h.cblp if h.cblp else h.cp * 512
        self.header_size = h.cparhdr * 16
        self.image = d[self.header_size:self.image_end]
        self.image_paras = (len(self.image) + 15) // 16
        self.relocs = [struct.unpack_from("<HH", d, h.lfarlc + 4 * i)
                       for i in range(h.crlc)]
        self.overlay_region = (self.image_end, len(d))
        self._find_overlay_manager()

    # ------------------------------------------------------------------ root
    def word(self, lin):
        return struct.unpack_from("<H", self.image, lin)[0]

    def reloc_linear(self):
        """Linear image offsets of every relocated word."""
        return [s * 16 + o for o, s in self.relocs]

    def reloc_values(self):
        """Counter of link-segment values stored at relocated words."""
        return Counter(self.word(a) for a in self.reloc_linear())

    def far_refs(self, image, reloc_lin):
        """Far call/jmp (9A/EA) instructions found via relocations.

        A relocation that points at the segment word of a 5-byte far
        call/jump (opcode at -3) is an exact, unambiguous code reference.
        Returns list of (insn_linear, opcode, seg, off).
        """
        out = []
        for a in reloc_lin:
            if a >= 3 and image[a - 3] in (0x9A, 0xEA):
                seg = struct.unpack_from("<H", image, a)[0]
                off = struct.unpack_from("<H", image, a - 2)[0]
                out.append((a - 3, image[a - 3], seg, off))
        return out

    # ------------------------------------------------------- overlay manager
    def _find_overlay_manager(self):
        """Locate the PLINK86 overlay descriptor table.

        The entry point (CS:IP from the header) is the overlay manager's
        start-up code.  Its first instructions are
            cli / mov di,es / mov si,ss / mov cx,sp / mov dx,TABLESEG /
            mov es,dx / mov ss,[es:0002] / mov sp,0100
        so the table segment is the immediate of the first ``BA iw 8E C2``.
        """
        h = self.hdr
        entry = h.cs * 16 + h.ip
        self.entry_linear = entry
        win = self.image[entry:entry + 64]
        i = win.find(b"\x8e\xc2")
        if i < 3 or win[i - 3] != 0xBA:
            raise ValueError("overlay manager start-up pattern not found")
        self.table_seg = struct.unpack_from("<H", win, i - 2)[0]
        self.mgr_seg = h.cs
        t = self.table_seg * 16
        self.table_lin = t
        self.table_mgr_cs, self.table_stack_seg = struct.unpack_from("<2H", self.image, t)
        ovls = []
        n = 1
        while True:
            rec = t + 4 + (n - 1) * 16
            (flags, nrel, s0, s1, fn, flo, fhi, paras) = struct.unpack_from(
                "<8H", self.image, rec)
            if flags == 0xFFFF:
                break
            fname = self.image[t + fn:self.image.index(b"\0", t + fn)].decode("latin-1")
            file_off = ((fhi << 16) | flo) * 16
            code_off = file_off + ((nrel + 3) // 4) * 16
            ovls.append(Overlay(n, rec, flags, flags & 0x3FFF, bool(flags & 0x4000),
                                nrel, s0, s1, fn, fname, file_off, code_off, paras))
            n += 1
            if n > 1000:
                raise ValueError("runaway overlay table")
        self.overlays = ovls
        self._find_thunks()

    def _find_thunks(self):
        """Overlay call thunks in the root.

        Each thunk is 12 bytes:  9A off mgrseg | dw ovl | EA off seg
        (call far to the manager's "ensure loaded" routine, the overlay
        number as inline data, then a far jump into the overlay).
        """
        img = self.image
        pat = struct.pack("<H", self.mgr_seg)
        thunks = []
        i = 0
        self.thunk_target_off = None
        while True:
            i = img.find(b"\x9a", i)
            if i < 0 or i + 12 > len(img):
                break
            if img[i + 3:i + 5] == pat and img[i + 7] == 0xEA:
                off = struct.unpack_from("<H", img, i + 1)[0]
                ovl = struct.unpack_from("<H", img, i + 5)[0]
                toff, tseg = struct.unpack_from("<HH", img, i + 8)
                if 1 <= ovl <= len(self.overlays):
                    self.thunk_target_off = off
                    thunks.append(Thunk(i, ovl, tseg, toff))
                    i += 12
                    continue
            i += 1
        self.thunks = thunks

    def overlay_bytes(self, ov):
        """(code_bytes, relocs) for an overlay.  relocs = [(off, linkseg)]."""
        d = self.data
        rel = []
        for k in range(ov.nrel):
            o, s = struct.unpack_from("<HH", d, ov.file_off + 4 * k)
            if o == 0 and s == 0:
                continue
            rel.append((o, s))
        # The manager reads min(paras, seg_end-seg_start) paragraphs.
        n = min(ov.paras, ov.seg_end - ov.seg_start) * 16
        code = d[ov.code_off:ov.code_off + n]
        return code, rel


def apply_relocs(image, relocs, image_seg, load_base):
    """Return a relocated copy.

    relocs: iterable of (offset, linkseg) where linkseg is absolute (link
    address space); the word lives at linear (linkseg - image_seg)*16+offset
    within ``image``.  Adds ``load_base`` to each word (DOS/PLINK semantics).
    """
    b = bytearray(image)
    if load_base == 0:
        return b
    for o, s in relocs:
        a = (s - image_seg) * 16 + o
        if 0 <= a <= len(b) - 2:
            v = (struct.unpack_from("<H", b, a)[0] + load_base) & 0xFFFF
            struct.pack_into("<H", b, a, v)
    return b
