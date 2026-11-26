"""PC BIOS services (HLE): INT 05 08 09 10 11 12 13 14 15 16 17 19 1A 1B 1C.

All services operate on emulated memory (BIOS data area at 0040:0000 and the
video buffer), so a program that peeks at the BDA or the screen sees the same
state that real BIOS code would leave.
"""
import datetime

from machine import BLOCK

# BIOS data area offsets (linear)
BDA_EQUIP = 0x410
BDA_KBFLAGS = 0x417
BDA_KBHEAD = 0x41A
BDA_KBTAIL = 0x41C
BDA_MODE = 0x449
BDA_COLS = 0x44A
BDA_PAGESIZE = 0x44C
BDA_PAGEOFF = 0x44E
BDA_CURPOS = 0x450
BDA_CURSHAPE = 0x460
BDA_PAGE = 0x462
BDA_CRTC = 0x463
BDA_MODEREG = 0x465
BDA_PALETTE = 0x466
BDA_TICKS = 0x46C
BDA_MIDNIGHT = 0x470
BDA_BREAK = 0x471

TICKS_PER_DAY = 0x1800B0

# scancode -> (normal, shift, ctrl, alt) BIOS key words; None = no key
_K = {}


def _build_keymap():
    def put(sc, n, s, c, a):
        _K[sc] = (n, s, c, a)
    put(0x01, 0x011B, 0x011B, 0x011B, 0x0100)
    digits = "1234567890-="
    shifted = "!@#$%^&*()_+"
    for i, ch in enumerate(digits):
        sc = 0x02 + i
        ctrl = None
        if ch == "2":
            ctrl = 0x0300
        elif ch == "6":
            ctrl = 0x071E
        elif ch == "-":
            ctrl = 0x0C1F
        alt = ((0x78 + i) << 8) if i < 12 else None
        put(sc, (sc << 8) | ord(ch), (sc << 8) | ord(shifted[i]), ctrl, alt)
    put(0x0E, 0x0E08, 0x0E08, 0x0E7F, 0x0E00)
    put(0x0F, 0x0F09, 0x0F00, 0x9400, 0xA500)
    rows = [(0x10, "qwertyuiop[]"), (0x1E, "asdfghjkl;'`"), (0x2B, "\\zxcvbnm,./")]
    shifted_map = {"[": "{", "]": "}", ";": ":", "'": '"', "`": "~", "\\": "|", ",": "<", ".": ">", "/": "?"}
    for base, letters in rows:
        for i, ch in enumerate(letters):
            sc = base + i
            if ch.isalpha():
                put(sc, (sc << 8) | ord(ch), (sc << 8) | ord(ch.upper()),
                    (sc << 8) | (ord(ch) - 0x60), sc << 8)
            else:
                ctrl = {"[": 0x1A1B, "]": 0x1B1D, "\\": 0x2B1C}.get(ch)
                put(sc, (sc << 8) | ord(ch), (sc << 8) | ord(shifted_map[ch]), ctrl, None)
    put(0x1C, 0x1C0D, 0x1C0D, 0x1C0A, 0xA600)
    put(0x39, 0x3920, 0x3920, 0x3920, 0x3920)
    put(0x37, 0x372A, 0x372A, None, None)
    put(0x4A, 0x4A2D, 0x4A2D, None, None)
    put(0x4E, 0x4E2B, 0x4E2B, None, None)
    for i in range(10):
        sc = 0x3B + i
        put(sc, sc << 8, (0x54 + i) << 8, (0x5E + i) << 8, (0x68 + i) << 8)
    put(0x57, 0x8500, 0x8700, 0x8900, 0x8B00)
    put(0x58, 0x8600, 0x8800, 0x8A00, 0x8C00)
    # cursor keypad (NumLock off)
    pad = {0x47: (0x4700, 0x4737, 0x7700), 0x48: (0x4800, 0x4838, 0x8D00),
           0x49: (0x4900, 0x4939, 0x8400), 0x4B: (0x4B00, 0x4B34, 0x7300),
           0x4C: (0x4C00, 0x4C35, 0x8F00), 0x4D: (0x4D00, 0x4D36, 0x7400),
           0x4F: (0x4F00, 0x4F31, 0x7500), 0x50: (0x5000, 0x5032, 0x9100),
           0x51: (0x5100, 0x5133, 0x7600), 0x52: (0x5200, 0x5230, 0x9200),
           0x53: (0x5300, 0x532E, 0x9300)}
    for sc, (n, s, c) in pad.items():
        put(sc, n, s, c, None)


_build_keymap()
KEYMAP = _K

CGA_PALETTE = [
    (0, 0, 0), (0, 0, 170), (0, 170, 0), (0, 170, 170), (170, 0, 0), (170, 0, 170),
    (170, 85, 0), (170, 170, 170), (85, 85, 85), (85, 85, 255), (85, 255, 85),
    (85, 255, 255), (255, 85, 85), (255, 85, 255), (255, 255, 85), (255, 255, 255)]

MODE_INFO = {
    # mode: (cols, rows, graphics bpp (0=text), page size, mode register)
    0: (40, 25, 0, 0x0800, 0x2C), 1: (40, 25, 0, 0x0800, 0x28),
    2: (80, 25, 0, 0x1000, 0x2D), 3: (80, 25, 0, 0x1000, 0x29),
    4: (40, 25, 2, 0x4000, 0x2A), 5: (40, 25, 2, 0x4000, 0x2E),
    6: (80, 25, 1, 0x4000, 0x1E), 7: (80, 25, 0, 0x1000, 0x29),
}


class BIOS:
    def __init__(self, machine, font8, start_time=None, printer_path=None):
        self.m = machine
        self.cpu = machine.cpu
        self.font8 = font8
        self.printer_path = printer_path
        self.printer = None
        self.start_time = start_time or datetime.datetime(1989, 8, 8, 12, 0, 0)
        self.poll_ip = -1
        self.poll_count = 0
        self.poll_icount = 0
        h = machine.handlers
        h[0x05] = self.int05
        h[0x08] = self.int08
        h[0x09] = self.int09
        h[0x10] = self.int10
        h[0x11] = self.int11
        h[0x12] = self.int12
        h[0x13] = self.int13
        h[0x14] = self.int14
        h[0x15] = self.int15
        h[0x16] = self.int16
        h[0x17] = self.int17
        h[0x18] = self.int19
        h[0x19] = self.int19
        h[0x1A] = self.int1a
        h[0x1B] = self.nop
        h[0x1C] = self.nop
        machine.load_font(font8)
        # start-up time of day in ticks
        t = self.start_time
        secs = t.hour * 3600 + t.minute * 60 + t.second
        machine.ww(BDA_TICKS, 0)
        self.set_ticks(int(secs * 18.2065))
        self.set_mode(3)

    def nop(self):
        return None

    # ------------------------------------------------------------- helpers
    def set_ticks(self, v):
        self.m.ww(BDA_TICKS, v & 0xFFFF)
        self.m.ww(BDA_TICKS + 2, (v >> 16) & 0xFFFF)

    def get_ticks(self):
        return self.m.rw(BDA_TICKS) | (self.m.rw(BDA_TICKS + 2) << 16)

    def now(self):
        """Emulated wall-clock time (deterministic: derived from the tick count)."""
        t = self.get_ticks()
        secs = t / 18.2065
        base = datetime.datetime(self.start_time.year, self.start_time.month, self.start_time.day)
        return base + datetime.timedelta(seconds=secs + 86400 * self.days)

    days = 0

    # ---------------------------------------------------------------- INT 05
    def int05(self):
        return None

    # ---------------------------------------------------------------- INT 08
    def int08(self):
        t = self.get_ticks() + 1
        if t >= TICKS_PER_DAY:
            t = 0
            self.m.wb(BDA_MIDNIGHT, 1)
            self.days += 1
        self.set_ticks(t)
        mc = self.m.rb(0x440)
        if mc:
            self.m.wb(0x440, mc - 1)
        return None

    # ---------------------------------------------------------------- INT 09
    def int09(self):
        m = self.m
        sc = m.port_in(0x60, 0)
        flags = m.rb(BDA_KBFLAGS)
        make = not (sc & 0x80)
        code = sc & 0x7F
        bit = {0x36: 0x01, 0x2A: 0x02, 0x1D: 0x04, 0x38: 0x08}.get(code)
        if bit is not None:
            flags = (flags | bit) if make else (flags & ~bit)
            m.wb(BDA_KBFLAGS, flags)
        elif make:
            tog = {0x46: 0x10, 0x45: 0x20, 0x3A: 0x40, 0x52: 0x80}.get(code)
            if tog is not None and code != 0x52:
                m.wb(BDA_KBFLAGS, flags ^ tog)
            else:
                if tog is not None:
                    m.wb(BDA_KBFLAGS, flags ^ tog)
                ent = KEYMAP.get(code)
                if ent is not None:
                    if flags & 0x08:
                        kw = ent[3]
                    elif flags & 0x04:
                        kw = ent[2]
                    elif flags & 0x03:
                        kw = ent[1]
                    else:
                        kw = ent[0]
                    if kw is not None and (flags & 0x40) and (kw & 0xFF) and chr(kw & 0xFF).isalpha():
                        kw ^= 0x20 if not (flags & 0x0C) else 0
                    if kw is not None:
                        self.kbd_put(kw)
        self.m.pic.eoi()
        return None

    def kbd_put(self, kw):
        m = self.m
        head = m.rw(BDA_KBHEAD)
        tail = m.rw(BDA_KBTAIL)
        nt = tail + 2
        if nt >= 0x3E:
            nt = 0x1E
        if nt == head:
            return False             # buffer full (real BIOS beeps)
        m.ww(0x400 + tail, kw)
        m.ww(BDA_KBTAIL, nt)
        return True

    def kbd_peek(self):
        m = self.m
        head = m.rw(BDA_KBHEAD)
        if head == m.rw(BDA_KBTAIL):
            return None
        return m.rw(0x400 + head)

    def kbd_get(self):
        m = self.m
        head = m.rw(BDA_KBHEAD)
        if head == m.rw(BDA_KBTAIL):
            return None
        kw = m.rw(0x400 + head)
        head += 2
        if head >= 0x3E:
            head = 0x1E
        m.ww(BDA_KBHEAD, head)
        return kw

    # ---------------------------------------------------------------- INT 16
    def int16(self):
        cpu = self.cpu
        r = cpu.r
        ah = r[0] >> 8
        if ah == 0x00 or ah == 0x10:
            kw = self.kbd_get()
            if kw is None:
                return BLOCK
            if ah == 0 and (kw & 0xFF) == 0xE0:
                kw &= 0xFF00
            r[0] = kw
        elif ah == 0x01 or ah == 0x11:
            kw = self.kbd_peek()
            if kw is None:
                cpu.zf = 1
                self.idle_poll()
            else:
                cpu.zf = 0
                r[0] = kw
        elif ah == 0x02 or ah == 0x12:
            f = self.m.rb(BDA_KBFLAGS)
            r[0] = (r[0] & 0xFF00) | f
        elif ah == 0x05:
            ok = self.kbd_put(r[1])
            r[0] = (r[0] & 0xFF00) | (0 if ok else 1)
        elif ah == 0x03:
            pass
        else:
            self.m.once("int", 0x16, ah)
        return None

    def idle_poll(self):
        """Heuristic: a tight loop polling for a key that is not there -> skip to next tick."""
        cpu = self.cpu
        here = (cpu.op_cs << 16) | cpu.op_ip
        # the caller's return address identifies the polling loop
        ret = self.m.rw((cpu.sr[2] << 4) + cpu.r[4]) | (self.m.rw((cpu.sr[2] << 4) + ((cpu.r[4] + 2) & 0xFFFF)) << 16)
        here = ret
        if here == self.poll_ip and cpu.icount - self.poll_icount < 2000:
            self.poll_count += 1
        else:
            self.poll_count = 0
        self.poll_ip = here
        self.poll_icount = cpu.icount
        if self.poll_count > 8:
            self.poll_count = 0
            self.m.skip_to_tick = True
            cpu.halted = True

    # ---------------------------------------------------------------- INT 11/12
    def int11(self):
        self.cpu.r[0] = self.m.rw(BDA_EQUIP)

    def int12(self):
        self.cpu.r[0] = self.m.rw(0x413)

    def int13(self):
        cpu = self.cpu
        ah = cpu.r[0] >> 8
        if ah == 0x00:
            cpu.r[0] &= 0x00FF
            cpu.cf = 0
        elif ah == 0x08:
            cpu.r[0] = 0x0100
            cpu.cf = 1
        else:
            self.m.once("int", 0x13, ah)
            cpu.r[0] = (cpu.r[0] & 0x00FF) | 0x8000
            cpu.cf = 1

    def int14(self):
        cpu = self.cpu
        cpu.r[0] = 0x8000 | (cpu.r[0] & 0xFF)

    def int15(self):
        cpu = self.cpu
        ah = cpu.r[0] >> 8
        cpu.r[0] = (cpu.r[0] & 0x00FF) | 0x8600
        cpu.cf = 1
        if ah not in (0x88, 0xC0, 0x41, 0x4F, 0x90, 0x91, 0x86):
            self.m.once("int", 0x15, ah)

    def int17(self):
        cpu = self.cpu
        ah = cpu.r[0] >> 8
        if ah == 0x00:
            if self.printer_path:
                if self.printer is None:
                    self.printer = open(self.printer_path, "ab")
                self.printer.write(bytes([cpu.r[0] & 0xFF]))
                self.printer.flush()
                cpu.r[0] = (cpu.r[0] & 0xFF) | 0x9000
            else:
                cpu.r[0] = (cpu.r[0] & 0xFF) | 0x2900   # out of paper / timeout / error
        elif ah == 0x01 or ah == 0x02:
            st = 0x90 if self.printer_path else 0x29
            cpu.r[0] = (cpu.r[0] & 0xFF) | (st << 8)
        return None

    def int19(self):
        self.m.log("INT 19h/18h (reboot / ROM BASIC) -> stop")
        self.m.exited = True
        self.cpu.halted = True

    def int1a(self):
        cpu = self.cpu
        r = cpu.r
        ah = r[0] >> 8
        if ah == 0x00:
            t = self.get_ticks()
            r[1] = (t >> 16) & 0xFFFF
            r[2] = t & 0xFFFF
            r[0] = (r[0] & 0xFF00) | self.m.rb(BDA_MIDNIGHT)
            self.m.wb(BDA_MIDNIGHT, 0)
        elif ah == 0x01:
            self.set_ticks((r[1] << 16) | r[2])
        elif ah == 0x02:
            n = self.now()

            def bcd(v):
                return ((v // 10) << 4) | (v % 10)
            r[1] = (bcd(n.hour) << 8) | bcd(n.minute)
            r[2] = bcd(n.second) << 8
            cpu.cf = 0
        elif ah == 0x04:
            n = self.now()

            def bcd(v):
                return ((v // 10) << 4) | (v % 10)
            r[1] = (bcd(n.year // 100) << 8) | bcd(n.year % 100)
            r[2] = (bcd(n.month) << 8) | bcd(n.day)
            cpu.cf = 0
        else:
            self.m.once("int", 0x1A, ah)
            cpu.cf = 1

    # =============================================================== video
    def mode(self):
        return self.m.rb(BDA_MODE)

    def cols(self):
        return self.m.rw(BDA_COLS) or 80

    def vbase(self):
        return 0xB0000 if self.mode() == 7 else 0xB8000

    def set_mode(self, al):
        m = self.m
        clear = not (al & 0x80)
        mode = al & 0x7F
        if mode not in MODE_INFO:
            self.m.once("video-mode", mode)
            mode = 3
        cols, rows, bpp, psize, mreg = MODE_INFO[mode]
        m.wb(BDA_MODE, mode)
        m.ww(BDA_COLS, cols)
        m.ww(BDA_PAGESIZE, psize)
        m.ww(BDA_PAGEOFF, 0)
        for p in range(8):
            m.ww(BDA_CURPOS + p * 2, 0)
        m.ww(BDA_CURSHAPE, 0x0607 if mode != 7 else 0x0B0C)
        m.wb(BDA_PAGE, 0)
        m.ww(BDA_CRTC, 0x3B4 if mode == 7 else 0x3D4)
        m.wb(BDA_MODEREG, mreg)
        pal = 0x30 if bpp == 0 else (0x3F if mode == 6 else 0x30)
        m.wb(BDA_PALETTE, pal)
        m.cga_mode_reg = mreg
        m.cga_color_reg = pal
        if mode == 7:
            eq = m.rw(BDA_EQUIP)
        if clear:
            base = 0xB0000 if mode == 7 else 0xB8000
            if bpp:
                self.m.mem[base:base + 0x4000] = bytes(0x4000)
            else:
                self.m.mem[base:base + 0x4000] = b"\x20\x07" * 0x2000

    def cursor(self, page=None):
        if page is None:
            page = self.m.rb(BDA_PAGE)
        v = self.m.rw(BDA_CURPOS + (page & 7) * 2)
        return v >> 8, v & 0xFF         # row, col

    def set_cursor(self, row, col, page=None):
        if page is None:
            page = self.m.rb(BDA_PAGE)
        self.m.ww(BDA_CURPOS + (page & 7) * 2, (row << 8) | col)

    def text_addr(self, page, row, col):
        return self.vbase() + page * self.m.rw(BDA_PAGESIZE) + (row * self.cols() + col) * 2

    def is_graphics(self):
        return MODE_INFO.get(self.mode(), (0, 0, 0))[2] != 0

    def put_pixel(self, x, y, color):
        mode = self.mode()
        mem = self.m.mem
        if mode in (4, 5):
            if not (0 <= x < 320 and 0 <= y < 200):
                return
            a = 0xB8000 + (y & 1) * 0x2000 + (y >> 1) * 80 + (x >> 2)
            sh = (3 - (x & 3)) * 2
            c = color & 3
            if color & 0x80:
                mem[a] ^= c << sh
            else:
                mem[a] = (mem[a] & ~(3 << sh)) | (c << sh)
        elif mode == 6:
            if not (0 <= x < 640 and 0 <= y < 200):
                return
            a = 0xB8000 + (y & 1) * 0x2000 + (y >> 1) * 80 + (x >> 3)
            sh = 7 - (x & 7)
            c = color & 1
            if color & 0x80:
                mem[a] ^= c << sh
            else:
                mem[a] = (mem[a] & ~(1 << sh)) | (c << sh)

    def get_pixel(self, x, y):
        mode = self.mode()
        mem = self.m.mem
        if mode in (4, 5):
            a = 0xB8000 + (y & 1) * 0x2000 + (y >> 1) * 80 + (x >> 2)
            return (mem[a] >> ((3 - (x & 3)) * 2)) & 3
        if mode == 6:
            a = 0xB8000 + (y & 1) * 0x2000 + (y >> 1) * 80 + (x >> 3)
            return (mem[a] >> (7 - (x & 7))) & 1
        return 0

    def gfx_char(self, row, col, ch, color):
        mode = self.mode()
        f = self.font8
        if mode == 6:
            for y in range(8):
                py = row * 8 + y
                a = 0xB8000 + (py & 1) * 0x2000 + (py >> 1) * 80 + col
                bits = f[ch * 8 + y]
                if color & 0x80:
                    self.m.mem[a] ^= bits
                else:
                    self.m.mem[a] = bits if color & 1 else 0
        else:
            c = color & 3
            for y in range(8):
                py = row * 8 + y
                a = 0xB8000 + (py & 1) * 0x2000 + (py >> 1) * 80 + col * 2
                bits = f[ch * 8 + y]
                w = 0
                for x in range(8):
                    if bits & (0x80 >> x):
                        w |= c << ((7 - x) * 2)
                if color & 0x80:
                    self.m.mem[a] ^= w >> 8
                    self.m.mem[a + 1] ^= w & 0xFF
                else:
                    self.m.mem[a] = w >> 8
                    self.m.mem[a + 1] = w & 0xFF

    def write_char(self, ch, attr, count, use_attr, page=None):
        if page is None:
            page = self.m.rb(BDA_PAGE)
        row, col = self.cursor(page)
        cols = self.cols()
        if self.is_graphics():
            for _ in range(count):
                if col >= cols:
                    break
                self.gfx_char(row, col, ch, attr)
                col += 1
            return
        a = self.text_addr(page, row, col)
        mem = self.m.mem
        end = self.text_addr(page, 25, 0)
        for _ in range(count):
            if a >= end:
                break
            mem[a] = ch
            if use_attr:
                mem[a + 1] = attr
            a += 2

    def scroll(self, up, lines, attr, top, left, bottom, right):
        cols = self.cols()
        right = min(right, cols - 1)
        bottom = min(bottom, 24)
        if top > bottom or left > right:
            return
        height = bottom - top + 1
        if lines == 0 or lines > height:
            lines = height
        mem = self.m.mem
        if self.is_graphics():
            bpb = 2 if self.mode() in (4, 5) else 1      # bytes per char cell per scanline
            fill = 0
            if self.mode() in (4, 5):
                c = attr & 3
                fill = c | (c << 2) | (c << 4) | (c << 6)
            elif attr & 1:
                fill = 0xFF
            x0 = left * bpb
            x1 = (right + 1) * bpb

            def line_addr(py):
                return 0xB8000 + (py & 1) * 0x2000 + (py >> 1) * 80
            rows = range(top, bottom + 1) if up else range(bottom, top - 1, -1)
            for rr in rows:
                src = rr + lines if up else rr - lines
                for y in range(8):
                    d = line_addr(rr * 8 + y)
                    if top <= src <= bottom:
                        s = line_addr(src * 8 + y)
                        mem[d + x0:d + x1] = mem[s + x0:s + x1]
                    else:
                        mem[d + x0:d + x1] = bytes([fill]) * (x1 - x0)
            return
        page = self.m.rb(BDA_PAGE)
        width = (right - left + 1) * 2
        blank = bytes([0x20, attr]) * (right - left + 1)
        rows = range(top, bottom + 1) if up else range(bottom, top - 1, -1)
        for rr in rows:
            d = self.text_addr(page, rr, left)
            src = rr + lines if up else rr - lines
            if top <= src <= bottom:
                s = self.text_addr(page, src, left)
                mem[d:d + width] = mem[s:s + width]
            else:
                mem[d:d + width] = blank

    def tty(self, ch, color=None):
        """Teletype output (INT 10h AH=0Eh semantics), used by DOS console output."""
        page = self.m.rb(BDA_PAGE)
        row, col = self.cursor(page)
        cols = self.cols()
        if ch == 7:
            return
        if ch == 8:
            if col > 0:
                col -= 1
        elif ch == 13:
            col = 0
        elif ch == 10:
            row += 1
        else:
            if self.is_graphics():
                self.gfx_char(row, col, ch, color if color is not None else 3)
            else:
                a = self.text_addr(page, row, col)
                self.m.mem[a] = ch
            col += 1
            if col >= cols:
                col = 0
                row += 1
        if row >= 25:
            if self.is_graphics():
                self.scroll(True, 1, 0, 0, 0, 24, cols - 1)
            else:
                a = self.text_addr(page, 24, cols - 1)
                attr = self.m.mem[a + 1]
                self.scroll(True, 1, attr, 0, 0, 24, cols - 1)
            row = 24
        self.set_cursor(row, col, page)

    def int10(self):
        cpu = self.cpu
        r = cpu.r
        m = self.m
        ax = r[0]
        ah = ax >> 8
        al = ax & 0xFF
        bh = r[3] >> 8
        bl = r[3] & 0xFF
        if ah == 0x00:
            self.set_mode(al)
        elif ah == 0x01:
            m.ww(BDA_CURSHAPE, r[1])
        elif ah == 0x02:
            dx = r[2]
            self.set_cursor(dx >> 8, dx & 0xFF, bh)
        elif ah == 0x03:
            row, col = self.cursor(bh)
            r[2] = (row << 8) | col
            r[1] = m.rw(BDA_CURSHAPE)
        elif ah == 0x04:
            r[0] = ax & 0x00FF
        elif ah == 0x05:
            m.wb(BDA_PAGE, al & 7)
            m.ww(BDA_PAGEOFF, (al & 7) * m.rw(BDA_PAGESIZE))
        elif ah == 0x06 or ah == 0x07:
            cx = r[1]
            dx = r[2]
            self.scroll(ah == 6, al, bh, cx >> 8, cx & 0xFF, dx >> 8, dx & 0xFF)
        elif ah == 0x08:
            if self.is_graphics():
                r[0] = (ax & 0xFF00) | self.gfx_read_char(bh)
            else:
                row, col = self.cursor(bh)
                a = self.text_addr(bh, row, col)
                r[0] = m.mem[a] | (m.mem[a + 1] << 8)
        elif ah == 0x09:
            self.write_char(al, bl, r[1], True, bh)
        elif ah == 0x0A:
            self.write_char(al, bl, r[1], False, bh)
        elif ah == 0x0B:
            if bh == 0:
                pal = (m.rb(BDA_PALETTE) & 0xE0) | (bl & 0x1F)
            else:
                pal = (m.rb(BDA_PALETTE) & 0xDF) | (0x20 if bl & 1 else 0)
            m.wb(BDA_PALETTE, pal)
            m.cga_color_reg = pal
        elif ah == 0x0C:
            self.put_pixel(r[1], r[2], al)
        elif ah == 0x0D:
            r[0] = (ax & 0xFF00) | self.get_pixel(r[1], r[2])
        elif ah == 0x0E:
            self.tty(al, bl)
        elif ah == 0x0F:
            r[0] = (self.cols() << 8) | self.mode()
            r[3] = (m.rb(BDA_PAGE) << 8) | bl
        elif ah == 0x10 or ah == 0x11:
            pass
        elif ah == 0x12:
            pass                        # BL unchanged -> "no EGA"
        elif ah == 0x13:
            self.write_string()
        elif ah == 0x1A:
            pass                        # AL unchanged -> "no VGA"
        elif ah == 0xFE or ah == 0xFF:
            pass
        else:
            m.once("int", 0x10, ah)
        return None

    def gfx_read_char(self, page):
        row, col = self.cursor(page)
        rows = []
        for y in range(8):
            bits = 0
            for x in range(8):
                if self.get_pixel(col * 8 + x if self.mode() == 6 else col * 8 + x, row * 8 + y):
                    bits |= 0x80 >> x
            rows.append(bits)
        pat = bytes(rows)
        for c in range(256):
            if self.font8[c * 8:c * 8 + 8] == pat:
                return c
        return 0

    def write_string(self):
        cpu = self.cpu
        r = cpu.r
        al = r[0] & 0xFF
        page = r[3] >> 8
        attr = r[3] & 0xFF
        row, col = r[2] >> 8, r[2] & 0xFF
        old = self.cursor(page)
        self.set_cursor(row, col, page)
        a = (cpu.sr[0] << 4) + r[5]
        for i in range(r[1]):
            ch = self.m.rb(a)
            a += 1
            if al & 2:
                attr = self.m.rb(a)
                a += 1
            if ch in (7, 8, 10, 13):
                self.tty(ch)
            else:
                self.write_char(ch, attr, 1, True, page)
                rr, cc = self.cursor(page)
                cc += 1
                if cc >= self.cols():
                    cc = 0
                    rr += 1
                    if rr >= 25:
                        self.scroll(True, 1, 7, 0, 0, 24, self.cols() - 1)
                        rr = 24
                self.set_cursor(rr, cc, page)
        if not (al & 1):
            self.set_cursor(old[0], old[1], page)
