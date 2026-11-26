"""DOS 3.30 emulation (HLE): MZ/COM loader, PSP, MCB chain, INT 20h/21h/2Fh/33h.

Drives map to host directories.  Name lookup is case-insensitive and limited
to 8.3 names.  New files are created in upper case.
"""
import os
import struct

from machine import BLOCK

ENV_SEG = 0x0101
FIRST_MCB = 0x0100
MEM_TOP = 0xA000            # first paragraph above conventional memory
DTA_DEFAULT_OFF = 0x80

ERR_FUNC, ERR_NOFILE, ERR_NOPATH, ERR_TOOMANY, ERR_ACCESS, ERR_HANDLE = 1, 2, 3, 4, 5, 6
ERR_MCB, ERR_NOMEM, ERR_BADBLOCK, ERR_DRIVE, ERR_NOMORE = 7, 8, 9, 15, 18


class DOSFile:
    def __init__(self, fp, path, dev=None, drive=2):
        self.fp = fp
        self.path = path
        self.dev = dev          # None for disk files, else "CON" / "PRN" / "AUX" / "NUL"
        self.drive = drive
        self.refs = 1


class DOS:
    def __init__(self, machine, bios, drives, cur_drive="C", log=print, printer_path=None,
                 env=None, echo_stdout=None):
        self.m = machine
        self.cpu = machine.cpu
        self.bios = bios
        self.log = log
        self.drives = {k.upper(): v for k, v in drives.items()}   # "C" -> host dir
        self.cur_drive = cur_drive.upper()
        self.cwd = {k: "" for k in self.drives}                   # DOS path, no leading '\'
        self.files = [None] * 20
        self.files[0] = DOSFile(None, "CON", "CON")
        self.files[1] = DOSFile(None, "CON", "CON")
        self.files[2] = DOSFile(None, "CON", "CON")
        self.files[3] = DOSFile(None, "AUX", "AUX")
        self.files[4] = DOSFile(None, "PRN", "PRN")
        self.printer_path = printer_path
        self.psp = 0
        self.dta = (0, DTA_DEFAULT_OFF)
        self.find_state = {}
        self.last_error = 0
        self.line_buf = None        # pending cooked input for handle 0 / AH=0Ah
        self.read_queue = b""       # cooked line already completed, not yet consumed
        self.pending_scan = None    # second byte of an extended key (AH=01/06/07/08)
        self.ctrl_break = 0
        self.verify = 0
        self.exit_code = 0
        self.env = env or {"COMSPEC": "C:\\COMMAND.COM", "PATH": "C:\\"}
        self.echo_stdout = echo_stdout
        self.file_log = []          # (op, dos name, host path) for reports
        self.program_name = ""
        h = machine.handlers
        h[0x20] = self.int20
        h[0x21] = self.int21
        h[0x22] = self.nop
        h[0x23] = self.nop
        h[0x24] = self.int24
        h[0x27] = self.int27
        h[0x28] = self.nop
        h[0x29] = self.int29
        h[0x2A] = self.nop
        h[0x2F] = self.int2f
        h[0x33] = self.int33
        h[0x00] = self.int00
        h[0x67] = self.int67

    def nop(self):
        return None

    # ----------------------------------------------------------- memory/MCB
    def mcb_write(self, seg, kind, owner, size):
        a = seg << 4
        self.m.wb(a, kind)
        self.m.ww(a + 1, owner)
        self.m.ww(a + 3, size)
        for i in range(5, 16):
            self.m.wb(a + i, 0)

    def mcb_read(self, seg):
        a = seg << 4
        return self.m.rb(a), self.m.rw(a + 1), self.m.rw(a + 3)

    def mcb_chain(self):
        seg = FIRST_MCB
        out = []
        for _ in range(1000):
            kind, owner, size = self.mcb_read(seg)
            if kind not in (0x4D, 0x5A):
                return None
            out.append((seg, kind, owner, size))
            if kind == 0x5A:
                return out
            seg = seg + 1 + size
        return None

    def mcb_merge(self):
        chain = self.mcb_chain()
        if chain is None:
            return
        i = 0
        while i < len(chain) - 1:
            seg, kind, owner, size = chain[i]
            nseg, nkind, nowner, nsize = chain[i + 1]
            if owner == 0 and nowner == 0:
                size = size + 1 + nsize
                self.mcb_write(seg, nkind, 0, size)
                chain[i] = (seg, nkind, 0, size)
                del chain[i + 1]
            else:
                i += 1

    def alloc(self, paras, owner):
        """First fit. Returns (segment, 0) or (None, largest)."""
        self.mcb_merge()
        chain = self.mcb_chain()
        if chain is None:
            return None, 0, ERR_MCB
        largest = 0
        for seg, kind, own, size in chain:
            if own != 0:
                continue
            if size >= paras:
                if size > paras:
                    self.mcb_write(seg + 1 + paras, kind, 0, size - paras - 1)
                    self.mcb_write(seg, 0x4D, owner, paras)
                else:
                    self.mcb_write(seg, kind, owner, paras)
                return seg + 1, 0, 0
            largest = max(largest, size)
        return None, largest, ERR_NOMEM

    def free_block(self, block):
        seg = block - 1
        kind, owner, size = self.mcb_read(seg)
        if kind not in (0x4D, 0x5A):
            return ERR_BADBLOCK
        self.mcb_write(seg, kind, 0, size)
        return 0

    def resize(self, block, paras):
        seg = block - 1
        kind, owner, size = self.mcb_read(seg)
        if kind not in (0x4D, 0x5A):
            return ERR_BADBLOCK, 0
        if paras <= size:
            if paras < size:
                self.mcb_write(seg + 1 + paras, kind, 0, size - paras - 1)
                self.mcb_write(seg, 0x4D, owner, paras)
                self.mcb_merge()
            return 0, paras
        # grow: absorb following free block(s)
        self.mcb_merge()
        kind, owner, size = self.mcb_read(seg)
        avail = size
        if kind == 0x4D:
            nk, no, ns = self.mcb_read(seg + 1 + size)
            if no == 0:
                avail = size + 1 + ns
                if avail >= paras:
                    if avail > paras:
                        self.mcb_write(seg + 1 + paras, nk, 0, avail - paras - 1)
                        self.mcb_write(seg, 0x4D, owner, paras)
                    else:
                        self.mcb_write(seg, nk, owner, paras)
                    return 0, paras
        return ERR_NOMEM, avail

    # ---------------------------------------------------------------- loader
    def build_env(self, seg, program_path):
        data = b""
        for k, v in self.env.items():
            data += ("%s=%s" % (k, v)).encode("ascii") + b"\0"
        data += b"\0" + struct.pack("<H", 1) + program_path.encode("ascii") + b"\0"
        a = seg << 4
        self.m.mem[a:a + len(data)] = data
        return (len(data) + 15) // 16

    def build_psp(self, psp, env_seg, tail, mem_end):
        a = psp << 4
        m = self.m
        m.mem[a:a + 256] = bytes(256)
        m.wb(a, 0xCD)
        m.wb(a + 1, 0x20)
        m.ww(a + 2, mem_end)
        # far call to DOS dispatcher at PSP:0005 -> use INT 21h stub style
        m.wb(a + 5, 0x9A)
        m.ww(a + 6, 0xE000 + 0x21 * 4)
        m.ww(a + 8, 0xF000)
        for i, vec in enumerate((0x22, 0x23, 0x24)):
            seg, off = m.get_vec(vec)
            m.ww(a + 0x0A + i * 4, off)
            m.ww(a + 0x0C + i * 4, seg)
        m.ww(a + 0x16, psp)             # parent PSP = self (like COMMAND.COM)
        for i in range(20):
            m.wb(a + 0x18 + i, i if i < 5 else 0xFF)
        m.ww(a + 0x2C, env_seg)
        m.ww(a + 0x32, 20)
        m.ww(a + 0x34, 0x18)
        m.ww(a + 0x36, psp)
        m.mem[a + 0x50:a + 0x53] = b"\xCD\x21\xCB"
        # FCBs from the first two arguments
        args = tail.split()
        for i, base in enumerate((0x5C, 0x6C)):
            m.mem[a + base:a + base + 12] = b"\0" + b" " * 11
            if i < len(args) and not args[i].startswith("/"):
                self.parse_fcb_name(a + base, args[i])
        t = tail.encode("ascii")[:126]
        if t and not t.startswith(b" "):
            t = b" " + t
        m.wb(a + 0x80, len(t))
        m.mem[a + 0x81:a + 0x81 + len(t)] = t
        m.wb(a + 0x81 + len(t), 0x0D)

    def parse_fcb_name(self, a, name):
        drive = 0
        if len(name) >= 2 and name[1] == ":":
            drive = ord(name[0].upper()) - 64
            name = name[2:]
        base, _, ext = name.upper().partition(".")
        self.m.wb(a, drive)
        self.m.mem[a + 1:a + 9] = base[:8].ljust(8).encode("ascii", "replace")
        self.m.mem[a + 9:a + 12] = ext[:3].ljust(3).encode("ascii", "replace")

    def load_program(self, dos_path, tail=""):
        host = self.resolve(dos_path)
        if host is None:
            raise FileNotFoundError(dos_path)
        data = open(host, "rb").read()
        full = self.full_dos_path(dos_path)
        self.program_name = full
        # environment block (own MCB)
        env_paras = 0x10
        self.mcb_write(FIRST_MCB, 0x4D, 0x0000, env_paras)
        self.build_env(ENV_SEG, full)
        prog_mcb = ENV_SEG + env_paras
        psp = prog_mcb + 1
        self.mcb_write(prog_mcb, 0x5A, psp, MEM_TOP - psp)
        self.mcb_write(FIRST_MCB, 0x4D, psp, env_paras)
        self.psp = psp
        cpu = self.cpu
        mem = self.m.mem
        if data[:2] in (b"MZ", b"ZM"):
            (sig, cblp, cp, crlc, cparhdr, minalloc, maxalloc, ss, sp, csum, ip, cs,
             lfarlc, ovno) = struct.unpack("<14H", data[:28])
            img_end = cp * 512 - ((512 - cblp) if cblp else 0)
            hdr = cparhdr * 16
            image = data[hdr:img_end]
            load_seg = psp + 0x10
            need = (len(image) + 15) // 16 + minalloc + 0x10
            avail = MEM_TOP - psp
            want = min(avail, max(need, (len(image) + 15) // 16 + maxalloc + 0x10 if maxalloc else need))
            self.mcb_write(prog_mcb, 0x5A, psp, avail)
            if want < avail:
                self.resize(psp, want)
            a = load_seg << 4
            mem[a:a + len(image)] = image
            for i in range(crlc):
                off, seg = struct.unpack_from("<HH", data, lfarlc + i * 4)
                la = ((load_seg + seg) << 4) + off
                v = (mem[la] | (mem[la + 1] << 8)) + load_seg
                mem[la] = v & 0xFF
                mem[la + 1] = (v >> 8) & 0xFF
            mem_end = MEM_TOP
            self.build_psp(psp, ENV_SEG, tail, mem_end)
            cpu.sr[0] = cpu.sr[3] = psp
            cpu.sr[2] = (ss + load_seg) & 0xFFFF
            cpu.r[4] = sp
            cpu.sr[1] = (cs + load_seg) & 0xFFFF
            cpu.ip = ip
            self.load_info = dict(psp=psp, load_seg=load_seg, image_size=len(image),
                                  relocs=crlc, cs=cs, ip=ip, ss=ss, sp=sp,
                                  minalloc=minalloc, maxalloc=maxalloc, overlay_bytes=len(data) - img_end)
        else:
            avail = MEM_TOP - psp
            self.mcb_write(prog_mcb, 0x5A, psp, avail)
            self.build_psp(psp, ENV_SEG, tail, MEM_TOP)
            a = (psp << 4) + 0x100
            mem[a:a + len(data)] = data
            for i in range(4):
                cpu.sr[i] = psp
            cpu.ip = 0x100
            cpu.r[4] = 0xFFFE
            self.m.ww((psp << 4) + 0xFFFE, 0)
            self.load_info = dict(psp=psp, com_size=len(data))
        self.dta = (psp, 0x80)
        cpu.r[0] = 0
        cpu.r[3] = 0
        cpu.r[1] = 0xFF
        cpu.r[2] = psp
        cpu.r[6] = cpu.ip
        cpu.r[7] = cpu.r[4]
        cpu.r[5] = 0x91C
        cpu.iflag = 1
        return self.load_info

    # ------------------------------------------------------------ file names
    def read_asciiz(self, seg, off, maxlen=128):
        a = (seg << 4) + off
        out = bytearray()
        for i in range(maxlen):
            c = self.m.rb(a + i)
            if c == 0:
                break
            out.append(c)
        return out.decode("latin-1")

    def split_path(self, name):
        name = name.strip().replace("/", "\\")
        drive = self.cur_drive
        if len(name) >= 2 and name[1] == ":":
            drive = name[0].upper()
            name = name[2:]
        if name.startswith("\\"):
            parts = name[1:].split("\\")
        else:
            cwd = self.cwd.get(drive, "")
            parts = (cwd.split("\\") if cwd else []) + name.split("\\")
        out = []
        for p in parts:
            if p in ("", "."):
                continue
            if p == "..":
                if out:
                    out.pop()
                continue
            out.append(p.upper())
        return drive, out

    def full_dos_path(self, name):
        drive, parts = self.split_path(name)
        return "%s:\\%s" % (drive, "\\".join(parts))

    def canon_component(self, p):
        base, dot, ext = p.partition(".")
        return base[:8] + ("." + ext[:3] if ext else "")

    def lookup(self, hostdir, comp):
        comp = self.canon_component(comp).upper()
        try:
            names = os.listdir(hostdir)
        except OSError:
            return None
        for n in names:
            if n.upper() == comp:
                return os.path.join(hostdir, n)
        return None

    def resolve(self, name, create=False, want_dir=False):
        """DOS name -> host path (None if not found).  With create, the final
        component may be missing and is returned in upper case."""
        drive, parts = self.split_path(name)
        if drive not in self.drives:
            return None
        if parts and parts[-1] in ("CON", "NUL", "PRN", "AUX", "CLOCK$", "LPT1", "COM1"):
            return "DEV:" + parts[-1]
        if parts:
            base = parts[-1].split(".")[0]
            if base in ("CON", "NUL", "PRN", "AUX", "LPT1", "COM1"):
                return "DEV:" + base
        cur = self.drives[drive]
        for i, p in enumerate(parts):
            nxt = self.lookup(cur, p)
            if nxt is None:
                if create and i == len(parts) - 1:
                    return os.path.join(cur, self.canon_component(p).upper())
                return None
            cur = nxt
        if want_dir and not os.path.isdir(cur):
            return None
        return cur

    def drive_num(self, name):
        drive, _ = self.split_path(name)
        return ord(drive) - 65

    # --------------------------------------------------------------- results
    def ok(self, ax=None):
        if ax is not None:
            self.cpu.r[0] = ax & 0xFFFF
        self.cpu.cf = 0

    def err(self, code):
        self.cpu.r[0] = code
        self.cpu.cf = 1
        self.last_error = code

    def alloc_handle(self, f):
        for i in range(5, 20):
            if self.files[i] is None:
                self.files[i] = f
                return i
        return -1

    # ------------------------------------------------------------- console
    def con_out(self, ch):
        if self.echo_stdout:
            self.echo_stdout(ch)
        self.bios.tty(ch)

    def con_in_available(self):
        return self.bios.kbd_peek() is not None

    def con_getkey(self):
        """Return ASCII (0 for extended keys, scan code returned next call) or None."""
        if getattr(self, "pending_scan", None) is not None:
            v = self.pending_scan
            self.pending_scan = None
            return v
        kw = self.bios.kbd_get()
        if kw is None:
            return None
        al = kw & 0xFF
        if al == 0 or al == 0xE0:
            self.pending_scan = kw >> 8
            return 0
        return al

    def cooked_line(self, maxlen):
        """Collect a line with echo. Returns bytes (without CR) when Enter is pressed, else None."""
        if self.line_buf is None:
            self.line_buf = bytearray()
        while True:
            c = self.con_getkey()
            if c is None:
                return None
            if c == 0:
                self.con_getkey()   # swallow scan code
                continue
            if c == 13:
                line = bytes(self.line_buf)
                self.line_buf = None
                return line
            if c == 8:
                if self.line_buf:
                    self.line_buf.pop()
                    self.con_out(8)
                    self.con_out(32)
                    self.con_out(8)
                continue
            if c == 27:
                while self.line_buf:
                    self.line_buf.pop()
                    self.con_out(8)
                    self.con_out(32)
                    self.con_out(8)
                continue
            if len(self.line_buf) < maxlen:
                self.line_buf.append(c)
                self.con_out(c)

    # =============================================================== INT 21h
    def int20(self):
        self.terminate(0)

    def int27(self):
        self.log("INT 27h TSR -> treated as exit")
        self.terminate(0)

    def int00(self):
        for ch in b"\r\nDivide overflow\r\n":
            self.con_out(ch)
        self.log("Divide overflow at %04X:%04X" % (self.cpu.op_cs, self.cpu.op_ip))
        self.terminate(0xFF)

    def int24(self):
        self.cpu.r[0] = (self.cpu.r[0] & 0xFF00) | 3    # fail

    def int29(self):
        self.con_out(self.cpu.r[0] & 0xFF)

    def int2f(self):
        ax = self.cpu.r[0]
        if (ax >> 8) not in (0x16, 0x43, 0x11, 0x12, 0x15, 0x1A, 0xB7, 0xAE):
            self.m.once("int", 0x2F, ax >> 8)
        self.cpu.r[0] = ax & 0xFF00          # AL=0: not installed

    def int33(self):
        self.cpu.r[0] = 0                    # no mouse driver

    def int67(self):
        self.cpu.r[0] = 0x8400               # EMS: function not supported

    def terminate(self, code):
        self.exit_code = code
        self.m.exit_code = code
        self.m.exited = True
        self.cpu.halted = True
        for i in range(5, 20):
            f = self.files[i]
            if f is not None and f.fp is not None:
                f.fp.close()
            self.files[i] = None

    def int21(self):
        cpu = self.cpu
        r = cpu.r
        sr = cpu.sr
        ax = r[0]
        ah = ax >> 8
        al = ax & 0xFF
        m = self.m
        if ah == 0x00:
            self.terminate(0)
        elif ah == 0x01 or ah == 0x07 or ah == 0x08:
            c = self.con_getkey()
            if c is None:
                return BLOCK
            if ah == 0x01:
                self.con_out(c)
            r[0] = (ax & 0xFF00) | c
        elif ah == 0x02:
            self.con_out(r[2] & 0xFF)
            r[0] = (ax & 0xFF00) | (r[2] & 0xFF)
        elif ah == 0x05:
            self.printer_out(bytes([r[2] & 0xFF]))
        elif ah == 0x06:
            dl = r[2] & 0xFF
            if dl == 0xFF:
                if not self.con_in_available() and getattr(self, "pending_scan", None) is None:
                    cpu.zf = 1
                    r[0] = ax & 0xFF00
                    self.bios.idle_poll()
                else:
                    c = self.con_getkey()
                    cpu.zf = 0
                    r[0] = (ax & 0xFF00) | c
            else:
                self.con_out(dl)
                r[0] = (ax & 0xFF00) | dl
        elif ah == 0x09:
            a = (sr[3] << 4) + r[2]
            for i in range(65536):
                c = m.rb(a + i)
                if c == 0x24:
                    break
                self.con_out(c)
            r[0] = (ax & 0xFF00) | 0x24
        elif ah == 0x0A:
            a = (sr[3] << 4) + r[2]
            maxlen = m.rb(a)
            if maxlen == 0:
                return None
            line = self.cooked_line(maxlen - 1)
            if line is None:
                return BLOCK
            m.wb(a + 1, len(line))
            for i, c in enumerate(line):
                m.wb(a + 2 + i, c)
            m.wb(a + 2 + len(line), 13)
            self.con_out(13)
        elif ah == 0x0B:
            avail = self.con_in_available() or getattr(self, "pending_scan", None) is not None
            r[0] = (ax & 0xFF00) | (0xFF if avail else 0)
            if not avail:
                self.bios.idle_poll()
        elif ah == 0x0C:
            # flush buffer then call function AL
            while self.bios.kbd_get() is not None:
                pass
            self.pending_scan = None
            if al in (0x01, 0x06, 0x07, 0x08, 0x0A):
                # continue as function AL; if it blocks, the retry runs AH=AL
                # directly, so the buffer is not flushed again
                r[0] = (al << 8) | al
                return self.int21()
            r[0] = ax & 0xFF00
        elif ah == 0x0D:
            pass
        elif ah == 0x0F or ah == 0x10 or ah == 0x27:
            return self.fcb_io(ah)
        elif ah == 0x29:
            return self.fcb_parse(al)
        elif ah == 0x0E:
            if 0 <= (r[2] & 0xFF) < 26 and chr(65 + (r[2] & 0xFF)) in self.drives:
                self.cur_drive = chr(65 + (r[2] & 0xFF))
            r[0] = (ax & 0xFF00) | 5
        elif ah == 0x19:
            r[0] = (ax & 0xFF00) | (ord(self.cur_drive) - 65)
        elif ah == 0x1A:
            self.dta = (sr[3], r[2])
        elif ah == 0x2F:
            sr[0] = self.dta[0]
            r[3] = self.dta[1]
        elif ah == 0x25:
            m.set_vec(al, sr[3], r[2])
        elif ah == 0x35:
            seg, off = m.get_vec(al)
            sr[0] = seg
            r[3] = off
        elif ah == 0x2A:
            n = self.bios.now()
            r[1] = n.year
            r[2] = (n.month << 8) | n.day
            r[0] = (ax & 0xFF00) | ((n.weekday() + 1) % 7)
        elif ah == 0x2B:
            r[0] = (ax & 0xFF00)
        elif ah == 0x2C:
            n = self.bios.now()
            r[1] = (n.hour << 8) | n.minute
            r[2] = (n.second << 8) | self.bios.hundredths()
        elif ah == 0x2D:
            r[0] = ax & 0xFF00
        elif ah == 0x2E:
            self.verify = al & 1
        elif ah == 0x30:
            r[0] = 0x1E03                 # DOS 3.30
            r[3] = 0
            r[1] = 0
        elif ah == 0x33:
            if al == 0:
                r[2] = (r[2] & 0xFF00) | self.ctrl_break
            elif al == 1:
                self.ctrl_break = r[2] & 1
            elif al == 5:
                r[2] = (r[2] & 0xFF00) | 3
        elif ah == 0x36:
            d = r[2] & 0xFF
            drive = self.cur_drive if d == 0 else chr(64 + d)
            if drive not in self.drives:
                r[0] = 0xFFFF
            else:
                r[0] = 4          # sectors per cluster
                r[3] = 2000       # free clusters
                r[1] = 512        # bytes per sector
                r[2] = 2400       # total clusters
        elif ah == 0x37:
            if al == 0:
                r[2] = (r[2] & 0xFF00) | ord("/")
            r[0] = ax & 0xFF00
        elif ah == 0x38:
            if al == 0:
                a = (sr[3] << 4) + r[2]
                info = struct.pack("<H5sBBB2sBBB", 0, b"$\0\0\0\0", 0, ord(","), 0, b".\0", ord("/"), 0, ord(":"))
                for i in range(34):
                    m.wb(a + i, info[i] if i < len(info) else 0)
                r[3] = 1
            self.ok()
        elif ah == 0x39 or ah == 0x3A:
            name = self.read_asciiz(sr[3], r[2])
            host = self.resolve(name, create=True)
            try:
                if ah == 0x39:
                    os.mkdir(host)
                else:
                    os.rmdir(host)
                self.ok()
            except (OSError, TypeError):
                self.err(ERR_NOPATH if ah == 0x39 else ERR_ACCESS)
        elif ah == 0x3B:
            name = self.read_asciiz(sr[3], r[2])
            host = self.resolve(name, want_dir=True)
            if host is None and self.split_path(name)[1] != []:
                self.err(ERR_NOPATH)
            else:
                drive, parts = self.split_path(name)
                self.cwd[drive] = "\\".join(parts)
                self.ok()
        elif ah == 0x3C or ah == 0x5B:
            name = self.read_asciiz(sr[3], r[2])
            host = self.resolve(name, create=True)
            if host is None:
                return self.err(ERR_NOPATH)
            if host.startswith("DEV:"):
                h = self.alloc_handle(DOSFile(None, name, host[4:]))
                return self.ok(h) if h >= 0 else self.err(ERR_TOOMANY)
            if ah == 0x5B and os.path.exists(host):
                return self.err(80)
            try:
                fp = open(host, "w+b")
            except OSError:
                return self.err(ERR_ACCESS)
            self.file_log.append(("create", name, host))
            h = self.alloc_handle(DOSFile(fp, host, None, self.drive_num(name)))
            if h < 0:
                fp.close()
                return self.err(ERR_TOOMANY)
            self.ok(h)
        elif ah == 0x3D:
            name = self.read_asciiz(sr[3], r[2])
            host = self.resolve(name)
            if host is None:
                self.file_log.append(("open-fail", name, None))
                return self.err(ERR_NOFILE)
            if host.startswith("DEV:"):
                h = self.alloc_handle(DOSFile(None, name, host[4:]))
                return self.ok(h) if h >= 0 else self.err(ERR_TOOMANY)
            if os.path.isdir(host):
                return self.err(ERR_ACCESS)
            mode = al & 3
            try:
                fp = open(host, "rb" if mode == 0 else "r+b")
            except OSError:
                return self.err(ERR_ACCESS)
            self.file_log.append(("open", name, host))
            h = self.alloc_handle(DOSFile(fp, host, None, self.drive_num(name)))
            if h < 0:
                fp.close()
                return self.err(ERR_TOOMANY)
            self.ok(h)
        elif ah == 0x3E:
            h = r[3]
            f = self.files[h] if h < 20 else None
            if f is None:
                return self.err(ERR_HANDLE)
            f.refs -= 1
            if f.refs <= 0 and f.fp is not None:
                f.fp.close()
            self.files[h] = None
            self.ok()
        elif ah == 0x3F:
            return self.read_handle()
        elif ah == 0x40:
            return self.write_handle()
        elif ah == 0x41:
            name = self.read_asciiz(sr[3], r[2])
            host = self.resolve(name)
            if host is None or host.startswith("DEV:"):
                return self.err(ERR_NOFILE)
            try:
                os.remove(host)
                self.file_log.append(("delete", name, host))
                self.ok()
            except OSError:
                self.err(ERR_ACCESS)
        elif ah == 0x42:
            h = r[3]
            f = self.files[h] if h < 20 else None
            if f is None:
                return self.err(ERR_HANDLE)
            off = (r[1] << 16) | r[2]
            if f.fp is None:
                r[0] = 0
                r[2] = 0
                return self.ok()
            if al == 1 or al == 2:
                if off & 0x80000000:
                    off -= 0x100000000
            try:
                f.fp.seek(off, al if al in (0, 1, 2) else 0)
            except (OSError, ValueError):
                return self.err(25)
            pos = f.fp.tell()
            r[0] = pos & 0xFFFF
            r[2] = (pos >> 16) & 0xFFFF
            self.ok(pos & 0xFFFF)
        elif ah == 0x43:
            name = self.read_asciiz(sr[3], r[2])
            host = self.resolve(name)
            if host is None:
                return self.err(ERR_NOFILE)
            if al == 0:
                r[1] = 0x10 if os.path.isdir(host) else 0x20
            self.ok()
        elif ah == 0x44:
            return self.ioctl()
        elif ah == 0x45:
            h = r[3]
            f = self.files[h] if h < 20 else None
            if f is None:
                return self.err(ERR_HANDLE)
            nh = self.alloc_handle(f)
            if nh < 0:
                return self.err(ERR_TOOMANY)
            f.refs += 1
            self.ok(nh)
        elif ah == 0x46:
            h = r[3]
            nh = r[1]
            f = self.files[h] if h < 20 else None
            if f is None or nh >= 20:
                return self.err(ERR_HANDLE)
            if self.files[nh] is not None:
                old = self.files[nh]
                old.refs -= 1
                if old.refs <= 0 and old.fp is not None:
                    old.fp.close()
            self.files[nh] = f
            f.refs += 1
            self.ok()
        elif ah == 0x47:
            d = r[2] & 0xFF
            drive = self.cur_drive if d == 0 else chr(64 + d)
            if drive not in self.drives:
                return self.err(ERR_DRIVE)
            p = self.cwd.get(drive, "").encode("ascii") + b"\0"
            a = (sr[3] << 4) + r[6]
            for i, c in enumerate(p):
                m.wb(a + i, c)
            self.ok(0x0100)
        elif ah == 0x48:
            seg, largest, e = self.alloc(r[3], self.psp)
            if seg is None:
                r[3] = largest
                return self.err(e)
            self.ok(seg)
        elif ah == 0x49:
            e = self.free_block(sr[0])
            if e:
                return self.err(e)
            self.ok()
        elif ah == 0x4A:
            e, avail = self.resize(sr[0], r[3])
            if e:
                r[3] = avail
                return self.err(e)
            self.ok()
        elif ah == 0x4B:
            name = self.read_asciiz(sr[3], r[2])
            m.once("int", 0x21, 0x4B, "exec %s" % name)
            self.err(ERR_NOFILE)
        elif ah == 0x4C:
            self.terminate(al)
        elif ah == 0x4D:
            r[0] = self.exit_code
            self.ok()
        elif ah == 0x4E:
            return self.find_first()
        elif ah == 0x4F:
            return self.find_next()
        elif ah == 0x50:
            self.psp = r[3]
        elif ah == 0x51 or ah == 0x62:
            r[3] = self.psp
        elif ah == 0x54:
            r[0] = (ax & 0xFF00) | self.verify
        elif ah == 0x56:
            old = self.read_asciiz(sr[3], r[2])
            new = self.read_asciiz(sr[0], r[7])
            ho = self.resolve(old)
            hn = self.resolve(new, create=True)
            if ho is None or hn is None:
                return self.err(ERR_NOFILE)
            try:
                os.rename(ho, hn)
                self.ok()
            except OSError:
                self.err(ERR_ACCESS)
        elif ah == 0x57:
            h = r[3]
            f = self.files[h] if h < 20 else None
            if f is None:
                return self.err(ERR_HANDLE)
            if al == 0:
                r[1] = 0x6000          # 12:00:00
                r[2] = ((1989 - 1980) << 9) | (8 << 5) | 8
            self.ok()
        elif ah == 0x58:
            if al == 0:
                self.ok(0)
            else:
                self.ok()
        elif ah == 0x59:
            r[0] = self.last_error
            r[3] = 0x0101
            r[1] = (r[1] & 0xFF) | 0x0000
        elif ah == 0x5A:
            self.err(ERR_ACCESS)
        elif ah == 0x63 or ah == 0x65 or ah == 0x66:
            self.err(ERR_FUNC)
        elif ah == 0x67:
            self.ok()
        elif ah == 0x68:
            h = r[3]
            f = self.files[h] if h < 20 else None
            if f is not None and f.fp is not None:
                f.fp.flush()
            self.ok()
        else:
            m.once("int", 0x21, ah)
            r[0] = (ax & 0xFF00)
            cpu.cf = 1
        return None

    def ioctl(self):
        cpu = self.cpu
        r = cpu.r
        al = r[0] & 0xFF
        h = r[3]
        if al in (0, 1, 6, 7):
            f = self.files[h] if h < 20 else None
            if f is None:
                return self.err(ERR_HANDLE)
            if al == 0:
                if f.dev == "CON":
                    r[2] = 0x80D3 if h in (0, 1, 2) else 0x80D3
                elif f.dev is not None:
                    r[2] = 0x80C0 | (0x04 if f.dev == "NUL" else 0)
                else:
                    r[2] = f.drive & 0x3F
                return self.ok(r[2])
            if al == 1:
                return self.ok()
            if al == 6:
                if f.dev == "CON":
                    st = 0xFF if self.con_in_available() else 0x00
                elif f.dev is not None:
                    st = 0xFF
                else:
                    pos = f.fp.tell()
                    f.fp.seek(0, 2)
                    end = f.fp.tell()
                    f.fp.seek(pos)
                    st = 0xFF if pos < end else 0x00
                return self.ok((r[0] & 0xFF00) | st)
            return self.ok((r[0] & 0xFF00) | 0xFF)
        if al == 8:
            d = r[3] & 0xFF
            drive = self.cur_drive if d == 0 else chr(64 + d)
            return self.ok(0 if drive in "AB" else 1)
        if al == 9:
            r[2] = 0
            return self.ok()
        if al == 0x0E:
            return self.ok(r[0] & 0xFF00)
        self.m.once("int", 0x21, 0x44, "ioctl al=%02X" % al)
        return self.err(ERR_FUNC)

    def printer_out(self, data):
        if self.printer_path:
            with open(self.printer_path, "ab") as fp:
                fp.write(data)

    def read_handle(self):
        cpu = self.cpu
        r = cpu.r
        h = r[3]
        n = r[1]
        f = self.files[h] if h < 20 else None
        if f is None:
            return self.err(ERR_HANDLE)
        a = (cpu.sr[3] << 4) + r[2]
        if f.dev == "CON":
            if not self.read_queue:
                line = self.cooked_line(126)
                if line is None:
                    return BLOCK
                self.con_out(13)
                self.con_out(10)
                self.read_queue = line + b"\r\n"
            data = self.read_queue[:n]
            self.read_queue = self.read_queue[n:]
        elif f.dev is not None:
            data = b""
        else:
            data = f.fp.read(n)
        mem = self.m.mem
        for i, c in enumerate(data):
            mem[(a + i) & 0xFFFFF] = c
        self.ok(len(data))

    def write_handle(self):
        cpu = self.cpu
        r = cpu.r
        h = r[3]
        n = r[1]
        f = self.files[h] if h < 20 else None
        if f is None:
            return self.err(ERR_HANDLE)
        a = (cpu.sr[3] << 4) + r[2]
        data = bytes(self.m.mem[(a + i) & 0xFFFFF] for i in range(n))
        if f.dev == "CON":
            for c in data:
                self.con_out(c)
        elif f.dev == "PRN":
            self.printer_out(data)
        elif f.dev is not None:
            pass
        else:
            if n == 0:
                f.fp.truncate()
            else:
                f.fp.write(data)
                f.fp.flush()
        self.ok(n)

    # ------------------------------------------------------------------ FCBs
    def fcb_addr(self):
        a = (self.cpu.sr[3] << 4) + self.cpu.r[2]
        if self.m.rb(a) == 0xFF:          # extended FCB
            a += 7
        return a

    def fcb_name(self, a):
        drv = self.m.rb(a)
        raw = bytes(self.m.rb(a + 1 + i) for i in range(11)).decode("latin-1")
        base, ext = raw[:8].rstrip(), raw[8:].rstrip()
        name = base + ("." + ext if ext else "")
        if drv:
            name = chr(64 + drv) + ":" + name
        return name

    def fcb_io(self, ah):
        """AH=0Fh open, 10h close, 27h random block read (DOS 1.x style file I/O)."""
        cpu = self.cpu
        r = cpu.r
        m = self.m
        a = self.fcb_addr()
        if not hasattr(self, "fcbs"):
            self.fcbs = {}
        if ah == 0x0F:
            host = self.resolve(self.fcb_name(a))
            if host is None or host.startswith("DEV:") or os.path.isdir(host):
                r[0] = (r[0] & 0xFF00) | 0xFF
                return None
            self.fcbs[a] = open(host, "rb")
            self.file_log.append(("fcb-open", self.fcb_name(a), host))
            if m.rb(a) == 0:
                m.wb(a, ord(self.cur_drive) - 64)
            m.ww(a + 0x0C, 0)
            m.ww(a + 0x0E, 128)
            size = os.path.getsize(host)
            m.ww(a + 0x10, size & 0xFFFF)
            m.ww(a + 0x12, size >> 16)
            m.ww(a + 0x14, ((1989 - 1980) << 9) | (8 << 5) | 8)
            m.ww(a + 0x16, 0x6000)
            m.wb(a + 0x20, 0)
            r[0] &= 0xFF00
        elif ah == 0x10:
            fp = self.fcbs.pop(a, None)
            if fp:
                fp.close()
            r[0] &= 0xFF00
        else:
            fp = self.fcbs.get(a)
            if fp is None:
                r[0] = (r[0] & 0xFF00) | 1
                r[1] = 0
                return None
            recsize = m.rw(a + 0x0E) or 128
            rec = m.rw(a + 0x21) | (m.rw(a + 0x23) << 16 if recsize < 64 else (m.rb(a + 0x23) << 16))
            n = r[1]
            fp.seek(rec * recsize)
            data = fp.read(n * recsize)
            full = len(data) // recsize
            part = len(data) % recsize
            seg, off = self.dta
            base = (seg << 4) + off
            for i, c in enumerate(data):
                m.wb(base + i, c)
            if part:
                for i in range(part, recsize):
                    m.wb(base + full * recsize + i, 0)
            got = full + (1 if part else 0)
            rec += got
            m.ww(a + 0x21, rec & 0xFFFF)
            m.wb(a + 0x23, (rec >> 16) & 0xFF)
            m.ww(a + 0x0C, rec // 128)
            m.wb(a + 0x20, rec % 128)
            r[1] = got
            al = 0 if got == n and not part else (3 if part else 1)
            r[0] = (r[0] & 0xFF00) | al
        return None

    def fcb_parse(self, al):
        """AH=29h: parse DS:SI into the FCB at ES:DI."""
        cpu = self.cpu
        r = cpu.r
        m = self.m
        src = cpu.sr[3] << 4
        si = r[6]
        dst = (cpu.sr[0] << 4) + r[7]

        def ch(i):
            return chr(m.rb(src + ((si + i) & 0xFFFF)))
        i = 0
        if al & 1:
            while ch(i) in " \t;,=+:":
                i += 1
        else:
            while ch(i) in " \t":
                i += 1
        result = 0
        drv = 0
        if ch(i + 1) == ":" and ch(i).isalpha():
            drv = ord(ch(i).upper()) - 64
            if chr(64 + drv) not in self.drives:
                result = 0xFF
            i += 2
        if drv or not (al & 2):
            m.wb(dst, drv)
        term = set(' \t\r\n.;,=+:"/[]<>|\0')

        def field(i, width, keep):
            out = ""
            while ch(i) not in term and len(out) < 64:
                out += ch(i).upper()
                i += 1
            if not out and keep:
                return i, None
            out = out[:width]
            if "*" in out:
                out = out[:out.index("*")] + "?" * (width - out.index("*"))
            return i, out.ljust(width)
        i, base = field(i, 8, al & 4)
        if base is not None:
            for k in range(8):
                m.wb(dst + 1 + k, ord(base[k]))
        elif not (al & 4):
            for k in range(8):
                m.wb(dst + 1 + k, 32)
        ext = None
        if ch(i) == ".":
            i, ext = field(i + 1, 3, False)
        if ext is not None:
            for k in range(3):
                m.wb(dst + 9 + k, ord(ext[k]))
        elif not (al & 8):
            for k in range(3):
                m.wb(dst + 9 + k, 32)
        name = bytes(m.rb(dst + 1 + k) for k in range(11))
        if result != 0xFF and b"?" in name:
            result = 1
        r[6] = (si + i) & 0xFFFF
        r[0] = (r[0] & 0xFF00) | result
        return None

    # ------------------------------------------------------------- find file
    def match83(self, pattern, name):
        def split(s):
            b, _, e = s.upper().partition(".")
            return b[:8].ljust(8), e[:3].ljust(3)
        pb, pe = split(pattern)
        if "*" in pb:
            i = pb.index("*")
            pb = pb[:i] + "?" * (8 - i)
        if "*" in pe:
            i = pe.index("*")
            pe = pe[:i] + "?" * (3 - i)
        nb, ne = split(name)
        for p, c in zip(pb + pe, nb + ne):
            if p != "?" and p != c:
                return False
        return True

    def find_first(self):
        cpu = self.cpu
        r = cpu.r
        spec = self.read_asciiz(cpu.sr[3], r[2])
        attr = r[1]
        drive, parts = self.split_path(spec)
        pattern = parts[-1] if parts else "*.*"
        dirspec = "%s:\\%s" % (drive, "\\".join(parts[:-1]))
        hostdir = self.resolve(dirspec, want_dir=True)
        if hostdir is None:
            return self.err(ERR_NOPATH)
        entries = []
        for n in sorted(os.listdir(hostdir)):
            base, _, ext = n.partition(".")
            if len(base) > 8 or len(ext) > 3 or "." in ext:
                continue
            full = os.path.join(hostdir, n)
            isdir = os.path.isdir(full)
            if isdir and not (attr & 0x10):
                continue
            if self.match83(pattern, n):
                entries.append((n.upper(), full, isdir))
        self.find_list = entries
        self.find_pos = 0
        return self.find_next()

    def find_next(self):
        cpu = self.cpu
        if getattr(self, "find_list", None) is None or self.find_pos >= len(self.find_list):
            return self.err(ERR_NOMORE)
        name, full, isdir = self.find_list[self.find_pos]
        self.find_pos += 1
        seg, off = self.dta
        a = (seg << 4) + off
        m = self.m
        for i in range(21):
            m.wb(a + i, 0)
        m.wb(a + 21, 0x10 if isdir else 0x20)
        m.ww(a + 22, 0x6000)
        m.ww(a + 24, ((1989 - 1980) << 9) | (8 << 5) | 8)
        size = 0 if isdir else os.path.getsize(full)
        m.ww(a + 26, size & 0xFFFF)
        m.ww(a + 28, size >> 16)
        nb = name.encode("ascii")[:12] + b"\0"
        for i in range(13):
            m.wb(a + 30 + i, nb[i] if i < len(nb) else 0)
        self.ok(0)
