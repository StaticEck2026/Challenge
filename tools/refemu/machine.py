"""PC/XT machine model: memory map, ROM stubs, IVT, PIC, PIT, ports, keyboard.

The BIOS ROM is a set of tiny stubs at F000:E000 + 4*n, each ``F1 nn CF``
(emulator trap, vector number, IRET).  The trap calls ``Machine.hle(nn)`` which
dispatches to the Python BIOS (bios.py) / DOS (dos.py) service routines.
"""

from cpu import CPU

ROM_SEG = 0xF000
STUB_BASE = 0xE000          # offset of the generic per-vector stubs
INT08_OFF = 0xE400          # timer stub: F1 08 / INT 1Ch / EOI / IRET
FONT_OFF = 0xFA6E           # 8x8 font for chars 0..127 (IBM-compatible location)
FONT_HI_OFF = 0xF600        # chars 128..255 (INT 1Fh points here)
HW_VECTORS = set(range(0x08, 0x10)) | set(range(0x70, 0x78))
BLOCK = object()            # returned by a service to mean "retry later (wait for input)"


class PIC:
    """Minimal 8259A: IRR/ISR/IMR, fixed priority, non-specific EOI."""

    def __init__(self):
        self.irr = 0
        self.isr = 0
        self.imr = 0
        self.cpu = None
        self.icw_step = 0

    def _update(self):
        pend = self.irr & ~self.imr & 0xFF
        ok = False
        if pend:
            for n in range(8):
                bit = 1 << n
                if self.isr & bit:
                    break
                if pend & bit:
                    ok = True
                    break
        self.cpu.irq_flag = ok

    def raise_irq(self, n):
        self.irr |= 1 << n
        self._update()

    def ack(self):
        pend = self.irr & ~self.imr & 0xFF
        for n in range(8):
            bit = 1 << n
            if self.isr & bit:
                break
            if pend & bit:
                self.irr &= ~bit
                self.isr |= bit
                self._update()
                return 8 + n
        self._update()
        return -1

    def eoi(self):
        for n in range(8):
            if self.isr & (1 << n):
                self.isr &= ~(1 << n)
                break
        self._update()

    def write(self, port, v):
        if port == 0x20:
            if v & 0x10:            # ICW1
                self.icw_step = 1
                self.imr = 0
            elif (v & 0x18) == 0x00 and (v & 0x20):   # OCW2 EOI
                if v & 0x40:        # specific EOI
                    self.isr &= ~(1 << (v & 7))
                    self._update()
                else:
                    self.eoi()
        else:
            if self.icw_step:
                self.icw_step = self.icw_step + 1 if self.icw_step < 3 else 0
            else:
                self.imr = v & 0xFF
                self._update()

    def read(self, port):
        if port == 0x21:
            return self.imr
        return self.irr


class Machine:
    def __init__(self, ipt=20000, log=print):
        self.mem = bytearray(0x100000 + 16)
        self.cpu = CPU(self.mem)
        self.pic = PIC()
        self.pic.cpu = self.cpu
        self.cpu.pic = self.pic
        self.cpu.port_in = self.port_in
        self.cpu.port_out = self.port_out
        self.cpu.hle = self.hle
        self.cpu.on_unknown = self.on_unknown
        self.log = log
        self.handlers = {}          # vector -> callable() returning None or BLOCK
        self.seen = set()           # (kind, a, b) logged once
        self.trace_ints = False
        self.int_counts = {}        # (vec, ah) -> count
        self.port_counts = {}       # ('in'/'out', port) -> count
        self.exited = False
        self.exit_code = 0
        # timing
        self.ipt = ipt              # instructions per 18.2 Hz tick at PIT divisor 65536
        self.pit_div = [65536, 65536, 65536]
        self.pit_mode = [3, 3, 3]
        self.pit_rl = [3, 3, 3]
        self.pit_wlow = [None, None, None]
        self.pit_latch = [None, None, None]
        self.tick_len = ipt
        self.next_tick = ipt
        self.last_tick = 0
        self.ticks = 0
        self.speaker = 0            # port 61h bits
        # keyboard controller
        self.kbd_data = 0
        self.kbd_queue = []         # pending scancodes from the host
        self.kbd_busy = False       # IRQ1 raised, data not yet consumed by INT 9
        self.cga_toggle = 0
        self.cga_mode_reg = 0x29
        self.cga_color_reg = 0x30
        self.crtc_index = 0
        self.crtc = [0] * 32
        self.idle = False           # set by services that block waiting for input
        self.skip_to_tick = False   # set by the keyboard-poll heuristic
        self.tick_hooks = []
        self._init_memory()

    # ----------------------------------------------------------------- setup
    def _init_memory(self):
        m = self.mem
        rom = ROM_SEG << 4
        for n in range(256):
            off = STUB_BASE + n * 4
            a = rom + off
            m[a:a + 4] = bytes([0xF1, n, 0xCF, 0x90])
            m[n * 4:n * 4 + 4] = bytes([off & 0xFF, off >> 8, ROM_SEG & 0xFF, ROM_SEG >> 8])
        # timer: F1 08 ; INT 1C ; PUSH AX ; MOV AL,20 ; OUT 20,AL ; POP AX ; IRET
        a = rom + INT08_OFF
        m[a:a + 12] = bytes([0xF1, 0x08, 0xCD, 0x1C, 0x50, 0xB0, 0x20, 0xE6, 0x20, 0x58, 0xCF, 0x90])
        self.set_vec(0x08, ROM_SEG, INT08_OFF)
        # reset vector, BIOS date, model byte (FE = PC/XT)
        a = rom + 0xFFF0
        m[a:a + 5] = bytes([0xEA, 0x00, 0xE0, 0x00, 0xF0])
        m[rom + 0xFFF5:rom + 0xFFFD] = b"01/10/86"
        m[rom + 0xFFFE] = 0xFE
        # vectors that are data pointers on a real PC
        self.set_vec(0x1D, ROM_SEG, 0xF0A4)
        self.set_vec(0x1E, ROM_SEG, 0xEFC7)
        self.set_vec(0x1F, ROM_SEG, FONT_HI_OFF)
        m[rom + 0xEFC7:rom + 0xEFD2] = bytes([0xDF, 0x02, 0x25, 0x02, 0x09, 0x2A, 0xFF, 0x50, 0xF6, 0x19, 0x04])
        # BIOS data area
        self.ww(0x410, 0x4061)      # 1 printer, 2 floppies, 80x25 colour, no 8087
        self.ww(0x413, 640)         # KB of conventional memory
        self.ww(0x41A, 0x1E)        # keyboard buffer head
        self.ww(0x41C, 0x1E)        # keyboard buffer tail
        self.ww(0x480, 0x1E)        # buffer start (AT+ BIOS fields)
        self.ww(0x482, 0x3E)        # buffer end
        self.mem[0x417] = 0x20      # NumLock off? keep 0x00 to get arrow keys from keypad
        self.mem[0x417] = 0x00
        self.ww(0x408, 0x378)       # LPT1 base
        self.ww(0x400, 0x3F8)       # COM1 base

    def load_font(self, font8):
        rom = ROM_SEG << 4
        self.mem[rom + FONT_OFF:rom + FONT_OFF + 1024] = font8[:1024]
        self.mem[rom + FONT_HI_OFF:rom + FONT_HI_OFF + 1024] = font8[1024:2048]

    def set_vec(self, n, seg, off):
        self.ww(n * 4, off)
        self.ww(n * 4 + 2, seg)

    def get_vec(self, n):
        return self.rw(n * 4 + 2), self.rw(n * 4)

    def rb(self, a):
        return self.mem[a & 0xFFFFF]

    def rw(self, a):
        return self.mem[a & 0xFFFFF] | (self.mem[(a + 1) & 0xFFFFF] << 8)

    def wb(self, a, v):
        self.mem[a & 0xFFFFF] = v & 0xFF

    def ww(self, a, v):
        self.mem[a & 0xFFFFF] = v & 0xFF
        self.mem[(a + 1) & 0xFFFFF] = (v >> 8) & 0xFF

    # --------------------------------------------------------------- logging
    def once(self, kind, a, b=None, extra=""):
        key = (kind, a, b)
        if key in self.seen:
            return
        self.seen.add(key)
        cpu = self.cpu
        self.log("[unimpl] %s %s%s at %04X:%04X %s" % (
            kind, ("%02X" % a) if isinstance(a, int) else a,
            ("/%02X" % b) if isinstance(b, int) else "", cpu.op_cs, cpu.op_ip, extra))

    def on_unknown(self, kind, op, cs, ip):
        if kind == "fpu":
            self.once("fpu-opcode", op)
        else:
            self.once("opcode", op)

    # ------------------------------------------------------------------- HLE
    def hle(self, n):
        cpu = self.cpu
        h = self.handlers.get(n)
        if self.trace_ints:
            key = (n, cpu.r[0] >> 8)
            self.int_counts[key] = self.int_counts.get(key, 0) + 1
        if h is None:
            if n in HW_VECTORS:
                if n >= 0x0A:
                    self.pic.eoi()
                return
            self.once("int", n, cpu.r[0] >> 8)
            return
        res = h()
        if res is BLOCK:
            cpu.ip = (cpu.ip - 2) & 0xFFFF
            cpu.iflag = 1
            cpu.halted = True
            self.idle = True
            return
        if n not in HW_VECTORS:
            # copy the arithmetic flags into the FLAGS image IRET will pop
            a = (cpu.sr[2] << 4) + ((cpu.r[4] + 4) & 0xFFFF)
            f = self.rw(a)
            f = (f & ~0x08D5) | (cpu.get_flags() & 0x08D5)
            self.ww(a, f)

    # ----------------------------------------------------------------- ports
    def count_port(self, kind, port):
        if self.trace_ints:
            k = (kind, port)
            self.port_counts[k] = self.port_counts.get(k, 0) + 1

    def port_in(self, port, w):
        if w:
            return self.port_in(port, 0) | (self.port_in(port + 1, 0) << 8)
        port &= 0xFFFF
        if self.trace_ints:
            self.count_port("in", port)
        if port == 0x3DA or port == 0x3BA:
            self.cga_toggle = (self.cga_toggle + 1) & 3
            return 0x09 if self.cga_toggle >= 2 else 0x00
        if port == 0x3D5 or port == 0x3B5:
            return self.crtc[self.crtc_index & 31]
        if port == 0x60:
            self.kbd_busy = False
            return self.kbd_data
        if port == 0x61:
            return self.speaker
        if port == 0x64:
            return 0x1C | (1 if self.kbd_busy else 0)
        if port == 0x20 or port == 0x21:
            return self.pic.read(port)
        if 0x40 <= port <= 0x42:
            return self.pit_read(port - 0x40)
        if port == 0x62:
            return 0x00
        if port == 0x201:          # game port: nothing attached
            return 0xF0
        if port in (0x379, 0x279, 0x3BD):   # printer status: busy / not ready
            return 0x00
        if port in (0x378, 0x3BC, 0x278, 0x37A):
            return 0xFF
        if 0x3F8 <= port <= 0x3FF or 0x2F8 <= port <= 0x2FF:
            return 0xFF
        self.once("port-in", "%04X" % port)
        return 0xFF

    def port_out(self, port, v, w):
        if w:
            self.port_out(port, v & 0xFF, 0)
            self.port_out(port + 1, (v >> 8) & 0xFF, 0)
            return
        port &= 0xFFFF
        v &= 0xFF
        if self.trace_ints:
            self.count_port("out", port)
        if port == 0x20 or port == 0x21:
            self.pic.write(port, v)
        elif 0x40 <= port <= 0x43:
            self.pit_write(port - 0x40, v)
        elif port == 0x61:
            self.speaker = v
        elif port == 0x3D4 or port == 0x3B4:
            self.crtc_index = v
        elif port == 0x3D5 or port == 0x3B5:
            self.crtc[self.crtc_index & 31] = v
        elif port == 0x3D8:
            self.cga_mode_reg = v
        elif port == 0x3D9:
            self.cga_color_reg = v
        elif port == 0x3B8 or port == 0x3BF:
            pass
        elif port in (0x378, 0x37A, 0x3BC, 0x3BE, 0x278, 0x27A):
            pass
        elif 0x3F8 <= port <= 0x3FF or 0x2F8 <= port <= 0x2FF:
            pass
        elif port == 0x201 or port == 0xA0 or port == 0x80:
            pass
        else:
            self.once("port-out", "%04X" % port)

    # ------------------------------------------------------------------- PIT
    def pit_write(self, ch, v):
        if ch == 3:
            c = v >> 6
            if c == 3:
                return
            rl = (v >> 4) & 3
            if rl == 0:             # counter latch
                self.pit_latch[c] = self.pit_count(c)
                return
            self.pit_rl[c] = rl
            self.pit_mode[c] = (v >> 1) & 7
            self.pit_wlow[c] = None
            return
        rl = self.pit_rl[ch]
        if rl == 3:
            if self.pit_wlow[ch] is None:
                self.pit_wlow[ch] = v
                return
            val = self.pit_wlow[ch] | (v << 8)
            self.pit_wlow[ch] = None
        elif rl == 1:
            val = v
        else:
            val = v << 8
        if val == 0:
            val = 65536
        self.pit_div[ch] = val
        if ch == 0:
            self.tick_len = max(50, self.ipt * val // 65536)
            self.next_tick = self.cpu.icount + self.tick_len

    def pit_count(self, ch):
        div = self.pit_div[ch]
        if ch == 0:
            elapsed = self.cpu.icount - self.last_tick
            c = div - (elapsed * div // max(1, self.tick_len))
        else:
            c = div - (self.cpu.icount * 4) % div
        return max(0, min(0xFFFF, c)) & 0xFFFF

    def pit_read(self, ch):
        if self.pit_latch[ch] is not None:
            v = self.pit_latch[ch]
            if self.pit_wlow[ch] is None:
                self.pit_wlow[ch] = "hi"
                return v & 0xFF
            self.pit_wlow[ch] = None
            self.pit_latch[ch] = None
            return v >> 8
        c = self.pit_count(ch)
        if self.pit_rl[ch] == 3:
            if self.pit_wlow[ch] is None:
                self.pit_wlow[ch] = "hi"
                return c & 0xFF
            self.pit_wlow[ch] = None
            return c >> 8
        return c & 0xFF

    def speaker_freq(self):
        if (self.speaker & 3) == 3:
            return 1193182 // self.pit_div[2]
        return 0

    # -------------------------------------------------------------- keyboard
    def key_scancodes(self, codes):
        self.kbd_queue.extend(codes)

    def bios_kbd_empty(self):
        return self.rw(0x41A) == self.rw(0x41C)

    def pump_keyboard(self):
        """Deliver the next queued scancode through IRQ1 if the controller is free."""
        if self.kbd_busy or not self.kbd_queue:
            return False
        sc = self.kbd_queue[0]
        if not (sc & 0x80) and sc not in (0x1D, 0x2A, 0x36, 0x38) and not self.bios_kbd_empty():
            return False
        self.kbd_queue.pop(0)
        self.kbd_data = sc
        self.kbd_busy = True
        self.pic.raise_irq(1)
        return True

    # ------------------------------------------------------------- main loop
    def tick(self):
        self.ticks += 1
        self.last_tick = self.cpu.icount
        self.pic.raise_irq(0)
        for h in self.tick_hooks:
            h()
        self.pump_keyboard()

    def run(self, max_instr):
        """Run until max_instr more instructions (emulated time) have elapsed or exit."""
        cpu = self.cpu
        end = cpu.icount + max_instr
        while not self.exited and cpu.icount < end:
            if cpu.halted and self.skip_to_tick:
                cpu.halted = False
                self.skip_to_tick = False
                cpu.icount = max(cpu.icount, self.next_tick)
            elif cpu.halted:
                if not cpu.iflag and not self.idle:
                    self.log("HLT with interrupts disabled at %04X:%04X" % (cpu.op_cs, cpu.op_ip))
                    self.exited = True
                    break
                # fast-forward emulated time to the next tick
                cpu.icount = max(cpu.icount, self.next_tick)
            k = self.next_tick - cpu.icount
            if k > end - cpu.icount:
                k = end - cpu.icount
            if k > 0:
                cpu.run(k)
            if cpu.icount >= self.next_tick:
                self.next_tick += self.tick_len
                if self.next_tick <= cpu.icount:
                    self.next_tick = cpu.icount + self.tick_len
                self.idle = False
                self.tick()
