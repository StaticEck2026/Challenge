"""8086 + 80186 real-mode CPU core for the NFL Challenge reference emulator.

Design notes (kept identical in godot/emu/cpu.gd so the two stay diffable):

* 1 MB flat memory in a bytearray, linear address = (seg << 4) + off, 20-bit wrap.
* General registers live in ``r`` (AX CX DX BX SP BP SI DI, 16-bit values);
  segment registers in ``sr`` (ES CS SS DS).
* Flags are kept as separate 0/1 ints (cf pf af zf sf tf iflag df of) and are
  computed eagerly by every instruction.
* Opcode ``F1 nn`` is an emulator trap ("HLE call"): the BIOS/DOS ROM stubs that
  the IVT points to contain ``F1 nn CF``.  Executing it calls ``self.hle(nn)``,
  which is the Python implementation of that interrupt service.
* ``hle`` may set ``self.ip`` back by 2 to "block" (e.g. INT 16h AH=0 with no key).
* Hardware IRQs are requested by the machine via ``self.pic``; they are
  delivered at instruction boundaries when IF=1.
"""

ES, CS, SS, DS = 0, 1, 2, 3
AX, CX, DX, BX, SP, BP, SI, DI = 0, 1, 2, 3, 4, 5, 6, 7

PARITY = bytes([1 if bin(i).count("1") % 2 == 0 else 0 for i in range(256)])


class CPUHalt(Exception):
    pass


class CPU:
    def __init__(self, mem):
        self.mem = mem                      # bytearray(0x100000 + 16)
        self.r = [0] * 8
        self.sr = [0] * 4
        self.ip = 0
        self.cf = self.pf = self.af = self.zf = self.sf = 0
        self.tf = self.iflag = self.df = self.of = 0
        self.ovr = -1                       # segment override index or -1
        self.rep = 0                        # 0, 0xF2, 0xF3
        self.op_cs = 0                      # CS:IP of the current instruction
        self.op_ip = 0
        self.inhibit = False                # interrupt shadow after MOV SS / POP SS
        self.halted = False
        self.icount = 0                     # emulated time (instructions, incl. idle skips)
        self.executed = 0                   # instructions actually executed
        self.ea = 0                         # linear address of last ModRM memory operand
        self.ea_off = 0                     # its offset part
        self.ea_reg = -1                    # >=0 when ModRM selected a register
        self.ea_seg = DS
        self.pic = None                     # set by machine: object with .pending() / .ack()
        self.irq_flag = False               # set by machine when an IRQ may be pending
        self.port_in = lambda port, w: 0xFF
        self.port_out = lambda port, val, w: None
        self.hle = lambda n: None
        self.on_unknown = None
        self.trace = None                   # optional list used as ring buffer of (cs, ip)
        self.trace_len = 0
        self.trace_pos = 0
        self.int_hook = None                # optional callable(n) for --trace-ints of software INTs
        self.ops = [self.op_invalid] * 256
        self._build_table()

    # ------------------------------------------------------------------ flags
    def get_flags(self):
        return (self.cf | 2 | (self.pf << 2) | (self.af << 4) | (self.zf << 6) |
                (self.sf << 7) | (self.tf << 8) | (self.iflag << 9) | (self.df << 10) |
                (self.of << 11) | 0xF000)

    def set_flags(self, v):
        self.cf = v & 1
        self.pf = (v >> 2) & 1
        self.af = (v >> 4) & 1
        self.zf = (v >> 6) & 1
        self.sf = (v >> 7) & 1
        self.tf = (v >> 8) & 1
        self.iflag = (v >> 9) & 1
        self.df = (v >> 10) & 1
        self.of = (v >> 11) & 1

    def szp8(self, v):
        self.zf = 1 if v == 0 else 0
        self.sf = v >> 7
        self.pf = PARITY[v]

    def szp16(self, v):
        self.zf = 1 if v == 0 else 0
        self.sf = v >> 15
        self.pf = PARITY[v & 0xFF]

    # ---------------------------------------------------------------- memory
    def rb(self, a):
        return self.mem[a & 0xFFFFF]

    def rw(self, a):
        a &= 0xFFFFF
        m = self.mem
        return m[a] | (m[(a + 1) & 0xFFFFF] << 8)

    def wb(self, a, v):
        a &= 0xFFFFF
        if a < 0xF0000:
            self.mem[a] = v & 0xFF

    def ww(self, a, v):
        a &= 0xFFFFF
        if a < 0xF0000:
            m = self.mem
            m[a] = v & 0xFF
            m[(a + 1) & 0xFFFFF] = (v >> 8) & 0xFF

    def fetch8(self):
        ip = self.ip
        v = self.mem[((self.sr[1] << 4) + ip) & 0xFFFFF]
        self.ip = (ip + 1) & 0xFFFF
        return v

    def fetch16(self):
        ip = self.ip
        base = self.sr[1] << 4
        m = self.mem
        v = m[(base + ip) & 0xFFFFF] | (m[(base + ((ip + 1) & 0xFFFF)) & 0xFFFFF] << 8)
        self.ip = (ip + 2) & 0xFFFF
        return v

    def fetchs8(self):
        v = self.fetch8()
        return v - 256 if v & 0x80 else v

    def push(self, v):
        r = self.r
        sp = (r[4] - 2) & 0xFFFF
        r[4] = sp
        self.ww((self.sr[2] << 4) + sp, v)

    def pop(self):
        r = self.r
        sp = r[4]
        v = self.rw((self.sr[2] << 4) + sp)
        r[4] = (sp + 2) & 0xFFFF
        return v

    # ------------------------------------------------------------- registers
    def get_r8(self, i):
        if i < 4:
            return self.r[i] & 0xFF
        return self.r[i - 4] >> 8

    def set_r8(self, i, v):
        r = self.r
        if i < 4:
            r[i] = (r[i] & 0xFF00) | (v & 0xFF)
        else:
            r[i - 4] = (r[i - 4] & 0x00FF) | ((v & 0xFF) << 8)

    # ----------------------------------------------------------------- modrm
    def modrm(self):
        """Decode a ModRM byte. Returns the reg field. Sets ea_reg / ea / ea_off."""
        m = self.fetch8()
        mod = m >> 6
        rm = m & 7
        if mod == 3:
            self.ea_reg = rm
            return (m >> 3) & 7
        self.ea_reg = -1
        r = self.r
        seg = DS
        if rm == 0:
            off = r[3] + r[6]
        elif rm == 1:
            off = r[3] + r[7]
        elif rm == 2:
            off = r[5] + r[6]
            seg = SS
        elif rm == 3:
            off = r[5] + r[7]
            seg = SS
        elif rm == 4:
            off = r[6]
        elif rm == 5:
            off = r[7]
        elif rm == 6:
            if mod == 0:
                off = self.fetch16()
            else:
                off = r[5]
                seg = SS
        else:
            off = r[3]
        if mod == 1:
            off += self.fetchs8()
        elif mod == 2:
            off += self.fetch16()
        off &= 0xFFFF
        if self.ovr >= 0:
            seg = self.ovr
        self.ea_off = off
        self.ea_seg = seg
        self.ea = ((self.sr[seg] << 4) + off) & 0xFFFFF
        return (m >> 3) & 7

    def get_rm8(self):
        if self.ea_reg >= 0:
            return self.get_r8(self.ea_reg)
        return self.mem[self.ea]

    def set_rm8(self, v):
        if self.ea_reg >= 0:
            self.set_r8(self.ea_reg, v)
        else:
            self.wb(self.ea, v)

    def get_rm16(self):
        if self.ea_reg >= 0:
            return self.r[self.ea_reg]
        a = self.ea
        if self.ea_off == 0xFFFF:
            return self.mem[a] | (self.mem[(a - 0xFFFF) & 0xFFFFF] << 8)
        return self.mem[a] | (self.mem[(a + 1) & 0xFFFFF] << 8)

    def set_rm16(self, v):
        if self.ea_reg >= 0:
            self.r[self.ea_reg] = v & 0xFFFF
        else:
            self.ww(self.ea, v)

    def ea_word2(self):
        """Second word of a memory operand (for LES/LDS/far pointers)."""
        return self.rw(((self.sr[self.ea_seg] << 4) + ((self.ea_off + 2) & 0xFFFF)))

    def seg_default(self, seg):
        return self.ovr if self.ovr >= 0 else seg

    # ------------------------------------------------------------------- ALU
    def alu8(self, op, a, b):
        if op == 0:      # ADD
            res = a + b
            self.cf = res >> 8
            res &= 0xFF
            self.of = ((a ^ res) & (b ^ res)) >> 7
            self.af = ((a ^ b ^ res) >> 4) & 1
        elif op == 1:    # OR
            res = a | b
            self.cf = self.of = self.af = 0
        elif op == 2:    # ADC
            res = a + b + self.cf
            self.cf = res >> 8
            res &= 0xFF
            self.of = ((a ^ res) & (b ^ res)) >> 7
            self.af = ((a ^ b ^ res) >> 4) & 1
        elif op == 3:    # SBB
            res = a - b - self.cf
            self.cf = 1 if res < 0 else 0
            res &= 0xFF
            self.of = ((a ^ b) & (a ^ res)) >> 7
            self.af = ((a ^ b ^ res) >> 4) & 1
        elif op == 4:    # AND
            res = a & b
            self.cf = self.of = self.af = 0
        elif op == 6:    # XOR
            res = a ^ b
            self.cf = self.of = self.af = 0
        else:            # SUB / CMP
            res = a - b
            self.cf = 1 if res < 0 else 0
            res &= 0xFF
            self.of = ((a ^ b) & (a ^ res)) >> 7
            self.af = ((a ^ b ^ res) >> 4) & 1
        self.zf = 1 if res == 0 else 0
        self.sf = res >> 7
        self.pf = PARITY[res]
        return res

    def alu16(self, op, a, b):
        if op == 0:
            res = a + b
            self.cf = res >> 16
            res &= 0xFFFF
            self.of = ((a ^ res) & (b ^ res)) >> 15
            self.af = ((a ^ b ^ res) >> 4) & 1
        elif op == 1:
            res = a | b
            self.cf = self.of = self.af = 0
        elif op == 2:
            res = a + b + self.cf
            self.cf = res >> 16
            res &= 0xFFFF
            self.of = ((a ^ res) & (b ^ res)) >> 15
            self.af = ((a ^ b ^ res) >> 4) & 1
        elif op == 3:
            res = a - b - self.cf
            self.cf = 1 if res < 0 else 0
            res &= 0xFFFF
            self.of = ((a ^ b) & (a ^ res)) >> 15
            self.af = ((a ^ b ^ res) >> 4) & 1
        elif op == 4:
            res = a & b
            self.cf = self.of = self.af = 0
        elif op == 6:
            res = a ^ b
            self.cf = self.of = self.af = 0
        else:
            res = a - b
            self.cf = 1 if res < 0 else 0
            res &= 0xFFFF
            self.of = ((a ^ b) & (a ^ res)) >> 15
            self.af = ((a ^ b ^ res) >> 4) & 1
        self.zf = 1 if res == 0 else 0
        self.sf = res >> 15
        self.pf = PARITY[res & 0xFF]
        return res

    def inc8(self, a):
        res = (a + 1) & 0xFF
        self.of = 1 if res == 0x80 else 0
        self.af = 1 if (res & 0x0F) == 0 else 0
        self.szp8(res)
        return res

    def dec8(self, a):
        res = (a - 1) & 0xFF
        self.of = 1 if res == 0x7F else 0
        self.af = 1 if (res & 0x0F) == 0x0F else 0
        self.szp8(res)
        return res

    def inc16(self, a):
        res = (a + 1) & 0xFFFF
        self.of = 1 if res == 0x8000 else 0
        self.af = 1 if (res & 0x0F) == 0 else 0
        self.szp16(res)
        return res

    def dec16(self, a):
        res = (a - 1) & 0xFFFF
        self.of = 1 if res == 0x7FFF else 0
        self.af = 1 if (res & 0x0F) == 0x0F else 0
        self.szp16(res)
        return res

    def shift(self, op, v, cnt, w):
        """Group-2 rotate/shift. op = reg field, w = 0 byte / 1 word."""
        cnt &= 0x1F                         # 80186+ masks the count
        if cnt == 0:
            return v
        bits = 16 if w else 8
        mask = 0xFFFF if w else 0xFF
        msb = bits - 1
        if op == 0:      # ROL
            c = cnt % bits
            if c:
                v = ((v << c) | (v >> (bits - c))) & mask
            self.cf = v & 1
            self.of = ((v >> msb) & 1) ^ self.cf
        elif op == 1:    # ROR
            c = cnt % bits
            if c:
                v = ((v >> c) | (v << (bits - c))) & mask
            self.cf = (v >> msb) & 1
            self.of = ((v >> msb) ^ (v >> (msb - 1))) & 1
        elif op == 2:    # RCL
            c = cnt % (bits + 1)
            cf = self.cf
            for _ in range(c):
                nc = (v >> msb) & 1
                v = ((v << 1) | cf) & mask
                cf = nc
            self.cf = cf
            self.of = ((v >> msb) & 1) ^ cf
        elif op == 3:    # RCR
            c = cnt % (bits + 1)
            cf = self.cf
            for _ in range(c):
                nc = v & 1
                v = (v >> 1) | (cf << msb)
                cf = nc
            self.cf = cf
            self.of = ((v >> msb) ^ (v >> (msb - 1))) & 1
        elif op == 4 or op == 6:   # SHL / SAL
            if cnt > bits:
                self.cf = 0
                v = 0
            else:
                self.cf = (v >> (bits - cnt)) & 1
                v = (v << cnt) & mask
            self.of = ((v >> msb) & 1) ^ self.cf
            self.af = 0
            if w:
                self.szp16(v)
            else:
                self.szp8(v)
        elif op == 5:    # SHR
            self.of = (v >> msb) & 1
            if cnt > bits:
                self.cf = 0
                v = 0
            else:
                self.cf = (v >> (cnt - 1)) & 1
                v >>= cnt
            self.af = 0
            if w:
                self.szp16(v)
            else:
                self.szp8(v)
        else:            # SAR
            sv = v - (1 << bits) if v >> msb else v
            if cnt >= bits:
                cnt = bits
            self.cf = (sv >> (cnt - 1)) & 1
            v = (sv >> cnt) & mask
            self.of = 0
            self.af = 0
            if w:
                self.szp16(v)
            else:
                self.szp8(v)
        return v

    # -------------------------------------------------------------- interrupts
    def interrupt(self, n):
        """Deliver interrupt n (software or hardware)."""
        self.push(self.get_flags())
        self.push(self.sr[1])
        self.push(self.ip)
        self.iflag = 0
        self.tf = 0
        self.ip = self.rw(n * 4)
        self.sr[1] = self.rw(n * 4 + 2)
        self.halted = False

    # ------------------------------------------------------------- main loop
    def run(self, n):
        """Execute up to n instructions. Returns number executed."""
        mem = self.mem
        ops = self.ops
        sr = self.sr
        i = 0
        trace = self.trace
        while i < n:
            if self.irq_flag and self.iflag and not self.inhibit:
                irq = self.pic.ack()
                if irq >= 0:
                    self.interrupt(irq)
            if self.halted:
                break
            self.inhibit = False
            self.ovr = -1
            self.rep = 0
            cs = sr[1]
            ip = self.ip
            self.op_cs = cs
            self.op_ip = ip
            if trace is not None:
                trace[self.trace_pos] = (cs << 16) | ip
                self.trace_pos = (self.trace_pos + 1) % self.trace_len
            op = mem[((cs << 4) + ip) & 0xFFFFF]
            self.ip = (ip + 1) & 0xFFFF
            ops[op](op)
            i += 1
        self.icount += i
        self.executed += i
        return i

    # ================================================================ opcodes
    def _build_table(self):
        t = self.ops
        for op in range(0x40):
            if (op & 7) < 6:
                t[op] = self.op_alu
        t[0x06] = t[0x0E] = t[0x16] = t[0x1E] = self.op_push_seg
        t[0x07] = t[0x17] = t[0x1F] = self.op_pop_seg
        t[0x26] = t[0x2E] = t[0x36] = t[0x3E] = self.op_segovr
        t[0x27] = self.op_daa
        t[0x2F] = self.op_das
        t[0x37] = self.op_aaa
        t[0x3F] = self.op_aas
        for op in range(0x40, 0x48):
            t[op] = self.op_inc_r
        for op in range(0x48, 0x50):
            t[op] = self.op_dec_r
        for op in range(0x50, 0x58):
            t[op] = self.op_push_r
        for op in range(0x58, 0x60):
            t[op] = self.op_pop_r
        t[0x60] = self.op_pusha
        t[0x61] = self.op_popa
        t[0x62] = self.op_bound
        t[0x68] = self.op_push_iw
        t[0x69] = self.op_imul_imm
        t[0x6A] = self.op_push_ib
        t[0x6B] = self.op_imul_imm
        t[0x6C] = t[0x6D] = self.op_ins
        t[0x6E] = t[0x6F] = self.op_outs
        for op in range(0x70, 0x80):
            t[op] = self.op_jcc
        t[0x80] = t[0x81] = t[0x82] = t[0x83] = self.op_grp1
        t[0x84] = t[0x85] = self.op_test_rm
        t[0x86] = t[0x87] = self.op_xchg_rm
        t[0x88] = self.op_mov_rm8_r8
        t[0x89] = self.op_mov_rm16_r16
        t[0x8A] = self.op_mov_r8_rm8
        t[0x8B] = self.op_mov_r16_rm16
        t[0x8C] = self.op_mov_rm_seg
        t[0x8D] = self.op_lea
        t[0x8E] = self.op_mov_seg_rm
        t[0x8F] = self.op_pop_rm
        t[0x90] = self.op_nop
        for op in range(0x91, 0x98):
            t[op] = self.op_xchg_ax
        t[0x98] = self.op_cbw
        t[0x99] = self.op_cwd
        t[0x9A] = self.op_call_far
        t[0x9B] = self.op_nop
        t[0x9C] = self.op_pushf
        t[0x9D] = self.op_popf
        t[0x9E] = self.op_sahf
        t[0x9F] = self.op_lahf
        t[0xA0] = t[0xA1] = t[0xA2] = t[0xA3] = self.op_mov_moffs
        t[0xA4] = t[0xA5] = self.op_movs
        t[0xA6] = t[0xA7] = self.op_cmps
        t[0xA8] = t[0xA9] = self.op_test_acc
        t[0xAA] = t[0xAB] = self.op_stos
        t[0xAC] = t[0xAD] = self.op_lods
        t[0xAE] = t[0xAF] = self.op_scas
        for op in range(0xB0, 0xB8):
            t[op] = self.op_mov_r8_imm
        for op in range(0xB8, 0xC0):
            t[op] = self.op_mov_r16_imm
        t[0xC0] = t[0xC1] = t[0xD0] = t[0xD1] = t[0xD2] = t[0xD3] = self.op_grp2
        t[0xC2] = t[0xC3] = self.op_ret
        t[0xC4] = t[0xC5] = self.op_lxs
        t[0xC6] = t[0xC7] = self.op_mov_rm_imm
        t[0xC8] = self.op_enter
        t[0xC9] = self.op_leave
        t[0xCA] = t[0xCB] = self.op_retf
        t[0xCC] = self.op_int3
        t[0xCD] = self.op_int
        t[0xCE] = self.op_into
        t[0xCF] = self.op_iret
        t[0xD4] = self.op_aam
        t[0xD5] = self.op_aad
        t[0xD6] = self.op_salc
        t[0xD7] = self.op_xlat
        for op in range(0xD8, 0xE0):
            t[op] = self.op_esc
        t[0xE0] = t[0xE1] = t[0xE2] = self.op_loop
        t[0xE3] = self.op_jcxz
        t[0xE4] = t[0xE5] = t[0xEC] = t[0xED] = self.op_in
        t[0xE6] = t[0xE7] = t[0xEE] = t[0xEF] = self.op_out
        t[0xE8] = self.op_call_near
        t[0xE9] = self.op_jmp_near
        t[0xEA] = self.op_jmp_far
        t[0xEB] = self.op_jmp_short
        t[0xF0] = self.op_lock
        t[0xF1] = self.op_hle
        t[0xF2] = t[0xF3] = self.op_rep
        t[0xF4] = self.op_hlt
        t[0xF5] = self.op_cmc
        t[0xF6] = t[0xF7] = self.op_grp3
        t[0xF8] = self.op_clc
        t[0xF9] = self.op_stc
        t[0xFA] = self.op_cli
        t[0xFB] = self.op_sti
        t[0xFC] = self.op_cld
        t[0xFD] = self.op_std
        t[0xFE] = self.op_grp4
        t[0xFF] = self.op_grp5

    def handled_opcodes(self):
        return [op for op in range(256) if self.ops[op] != self.op_invalid]

    def op_invalid(self, op):
        if self.on_unknown:
            self.on_unknown("opcode", op, self.op_cs, self.op_ip)
        # 80186: invalid opcode -> INT 6 with IP pointing at the opcode
        self.ip = self.op_ip
        self.interrupt(6)

    # ---- ALU block 00..3D
    def op_alu(self, op):
        alu = op >> 3
        form = op & 7
        if form == 0:
            reg = self.modrm()
            res = self.alu8(alu, self.get_rm8(), self.get_r8(reg))
            if alu != 7:
                self.set_rm8(res)
        elif form == 1:
            reg = self.modrm()
            res = self.alu16(alu, self.get_rm16(), self.r[reg])
            if alu != 7:
                self.set_rm16(res)
        elif form == 2:
            reg = self.modrm()
            res = self.alu8(alu, self.get_r8(reg), self.get_rm8())
            if alu != 7:
                self.set_r8(reg, res)
        elif form == 3:
            reg = self.modrm()
            res = self.alu16(alu, self.r[reg], self.get_rm16())
            if alu != 7:
                self.r[reg] = res
        elif form == 4:
            r = self.r
            res = self.alu8(alu, r[0] & 0xFF, self.fetch8())
            if alu != 7:
                r[0] = (r[0] & 0xFF00) | res
        else:
            res = self.alu16(alu, self.r[0], self.fetch16())
            if alu != 7:
                self.r[0] = res

    def op_push_seg(self, op):
        self.push(self.sr[op >> 3])

    def op_pop_seg(self, op):
        self.sr[op >> 3] = self.pop()
        if op == 0x17:
            self.inhibit = True

    def op_segovr(self, op):
        self.ovr = (op >> 3) & 3
        op2 = self.fetch8()
        self.ops[op2](op2)

    def op_daa(self, op):
        r = self.r
        al = r[0] & 0xFF
        old_al = al
        old_cf = self.cf
        if (al & 0x0F) > 9 or self.af:
            al += 6
            self.af = 1
        else:
            self.af = 0
        if old_al > 0x99 or old_cf:
            al += 0x60
            self.cf = 1
        else:
            self.cf = 0
        al &= 0xFF
        r[0] = (r[0] & 0xFF00) | al
        self.szp8(al)

    def op_das(self, op):
        r = self.r
        al = r[0] & 0xFF
        old_al = al
        old_cf = self.cf
        if (al & 0x0F) > 9 or self.af:
            al -= 6
            self.af = 1
        else:
            self.af = 0
        if old_al > 0x99 or old_cf:
            al -= 0x60
            self.cf = 1
        else:
            self.cf = 0
        al &= 0xFF
        r[0] = (r[0] & 0xFF00) | al
        self.szp8(al)

    def op_aaa(self, op):
        r = self.r
        ax = r[0]
        if (ax & 0x0F) > 9 or self.af:
            ax = (ax + 0x106) & 0xFFFF
            self.af = self.cf = 1
        else:
            self.af = self.cf = 0
        r[0] = ax & 0xFF0F
        self.szp8(r[0] & 0xFF)

    def op_aas(self, op):
        r = self.r
        ax = r[0]
        if (ax & 0x0F) > 9 or self.af:
            al = ((ax & 0xFF) - 6) & 0xFF
            ah = ((ax >> 8) - 1) & 0xFF
            ax = (ah << 8) | al
            self.af = self.cf = 1
        else:
            self.af = self.cf = 0
        r[0] = ax & 0xFF0F
        self.szp8(r[0] & 0xFF)

    def op_inc_r(self, op):
        i = op & 7
        self.r[i] = self.inc16(self.r[i])

    def op_dec_r(self, op):
        i = op & 7
        self.r[i] = self.dec16(self.r[i])

    def op_push_r(self, op):
        if op == 0x54:                     # 8086/186 push the decremented SP
            self.push((self.r[4] - 2) & 0xFFFF)
        else:
            self.push(self.r[op & 7])

    def op_pop_r(self, op):
        v = self.pop()
        self.r[op & 7] = v

    def op_pusha(self, op):
        r = self.r
        sp = r[4]
        for i in range(8):
            self.push(sp if i == 4 else r[i])

    def op_popa(self, op):
        r = self.r
        for i in (7, 6, 5, 4, 3, 2, 1, 0):
            v = self.pop()
            if i != 4:
                r[i] = v

    def op_bound(self, op):
        reg = self.modrm()
        lo = self.get_rm16()
        hi = self.ea_word2()
        v = self.r[reg]
        sv = v - 0x10000 if v & 0x8000 else v
        slo = lo - 0x10000 if lo & 0x8000 else lo
        shi = hi - 0x10000 if hi & 0x8000 else hi
        if sv < slo or sv > shi:
            self.ip = self.op_ip
            self.interrupt(5)

    def op_push_iw(self, op):
        self.push(self.fetch16())

    def op_push_ib(self, op):
        self.push(self.fetchs8() & 0xFFFF)

    def op_imul_imm(self, op):
        reg = self.modrm()
        a = self.get_rm16()
        if op == 0x69:
            b = self.fetch16()
            if b & 0x8000:
                b -= 0x10000
        else:
            b = self.fetchs8()
        if a & 0x8000:
            a -= 0x10000
        res = a * b
        self.r[reg] = res & 0xFFFF
        self.cf = self.of = 0 if -0x8000 <= res <= 0x7FFF else 1
        self.szp16(res & 0xFFFF)

    def op_jcc(self, op):
        d = self.fetchs8()
        c = op & 0x0F
        if c == 0:
            t = self.of
        elif c == 1:
            t = not self.of
        elif c == 2:
            t = self.cf
        elif c == 3:
            t = not self.cf
        elif c == 4:
            t = self.zf
        elif c == 5:
            t = not self.zf
        elif c == 6:
            t = self.cf or self.zf
        elif c == 7:
            t = not (self.cf or self.zf)
        elif c == 8:
            t = self.sf
        elif c == 9:
            t = not self.sf
        elif c == 10:
            t = self.pf
        elif c == 11:
            t = not self.pf
        elif c == 12:
            t = self.sf != self.of
        elif c == 13:
            t = self.sf == self.of
        elif c == 14:
            t = self.zf or (self.sf != self.of)
        else:
            t = (not self.zf) and (self.sf == self.of)
        if t:
            self.ip = (self.ip + d) & 0xFFFF

    def op_grp1(self, op):
        alu = self.modrm()
        if op & 1 == 0:
            a = self.get_rm8()
            b = self.fetch8()
            res = self.alu8(alu, a, b)
            if alu != 7:
                self.set_rm8(res)
        else:
            a = self.get_rm16()
            b = self.fetch16() if op == 0x81 else (self.fetchs8() & 0xFFFF)
            res = self.alu16(alu, a, b)
            if alu != 7:
                self.set_rm16(res)

    def op_test_rm(self, op):
        reg = self.modrm()
        if op == 0x84:
            self.alu8(4, self.get_rm8(), self.get_r8(reg))
        else:
            self.alu16(4, self.get_rm16(), self.r[reg])

    def op_xchg_rm(self, op):
        reg = self.modrm()
        if op == 0x86:
            a = self.get_rm8()
            self.set_rm8(self.get_r8(reg))
            self.set_r8(reg, a)
        else:
            a = self.get_rm16()
            self.set_rm16(self.r[reg])
            self.r[reg] = a

    def op_mov_rm8_r8(self, op):
        reg = self.modrm()
        self.set_rm8(self.get_r8(reg))

    def op_mov_rm16_r16(self, op):
        reg = self.modrm()
        self.set_rm16(self.r[reg])

    def op_mov_r8_rm8(self, op):
        reg = self.modrm()
        self.set_r8(reg, self.get_rm8())

    def op_mov_r16_rm16(self, op):
        reg = self.modrm()
        self.r[reg] = self.get_rm16()

    def op_mov_rm_seg(self, op):
        reg = self.modrm()
        self.set_rm16(self.sr[reg & 3])

    def op_lea(self, op):
        reg = self.modrm()
        self.r[reg] = self.ea_off

    def op_mov_seg_rm(self, op):
        reg = self.modrm() & 3
        self.sr[reg] = self.get_rm16()
        if reg == SS:
            self.inhibit = True

    def op_pop_rm(self, op):
        v = self.pop()
        self.modrm()
        self.set_rm16(v)

    def op_nop(self, op):
        pass

    def op_xchg_ax(self, op):
        r = self.r
        i = op & 7
        r[0], r[i] = r[i], r[0]

    def op_cbw(self, op):
        r = self.r
        al = r[0] & 0xFF
        r[0] = al | 0xFF00 if al & 0x80 else al

    def op_cwd(self, op):
        r = self.r
        r[2] = 0xFFFF if r[0] & 0x8000 else 0

    def op_call_far(self, op):
        off = self.fetch16()
        seg = self.fetch16()
        self.push(self.sr[1])
        self.push(self.ip)
        self.sr[1] = seg
        self.ip = off

    def op_pushf(self, op):
        self.push(self.get_flags())

    def op_popf(self, op):
        self.set_flags(self.pop())

    def op_sahf(self, op):
        ah = self.r[0] >> 8
        self.cf = ah & 1
        self.pf = (ah >> 2) & 1
        self.af = (ah >> 4) & 1
        self.zf = (ah >> 6) & 1
        self.sf = (ah >> 7) & 1

    def op_lahf(self, op):
        f = self.get_flags() & 0xFF
        self.r[0] = (self.r[0] & 0xFF) | (f << 8)

    def op_mov_moffs(self, op):
        off = self.fetch16()
        seg = self.ovr if self.ovr >= 0 else DS
        a = (self.sr[seg] << 4) + off
        r = self.r
        if op == 0xA0:
            r[0] = (r[0] & 0xFF00) | self.rb(a)
        elif op == 0xA1:
            r[0] = self.rw(a)
        elif op == 0xA2:
            self.wb(a, r[0])
        else:
            self.ww(a, r[0])

    # ---- string instructions
    def op_movs(self, op):
        r = self.r
        sr = self.sr
        srcseg = sr[self.ovr if self.ovr >= 0 else DS] << 4
        dstseg = sr[ES] << 4
        step = 1 if op == 0xA4 else 2
        if self.df:
            step = -step
        mem = self.mem
        if self.rep:
            cnt = r[1]
            if cnt == 0:
                return
            si = r[6]
            di = r[7]
            if op == 0xA4:
                # fast path: forward copy, no segment wrap; slice assignment has
                # memmove semantics, which equals a forward byte copy unless the
                # destination overlaps the source from above.
                if step == 1 and si + cnt <= 0x10000 and di + cnt <= 0x10000:
                    s = srcseg + si
                    d = dstseg + di
                    if s + cnt <= 0x100000 and d + cnt <= 0xF0000 and (d <= s or d >= s + cnt):
                        mem[d:d + cnt] = mem[s:s + cnt]
                        r[6] = (si + cnt) & 0xFFFF
                        r[7] = (di + cnt) & 0xFFFF
                        r[1] = 0
                        return
                for _ in range(cnt):
                    self.wb(dstseg + di, mem[(srcseg + si) & 0xFFFFF])
                    si = (si + step) & 0xFFFF
                    di = (di + step) & 0xFFFF
            else:
                for _ in range(cnt):
                    self.ww(dstseg + di, self.rw(srcseg + si))
                    si = (si + step) & 0xFFFF
                    di = (di + step) & 0xFFFF
            r[6] = si
            r[7] = di
            r[1] = 0
        else:
            if op == 0xA4:
                self.wb(dstseg + r[7], mem[(srcseg + r[6]) & 0xFFFFF])
            else:
                self.ww(dstseg + r[7], self.rw(srcseg + r[6]))
            r[6] = (r[6] + step) & 0xFFFF
            r[7] = (r[7] + step) & 0xFFFF

    def op_cmps(self, op):
        r = self.r
        sr = self.sr
        srcseg = sr[self.ovr if self.ovr >= 0 else DS] << 4
        dstseg = sr[ES] << 4
        w = op & 1
        step = 2 if w else 1
        if self.df:
            step = -step
        rep = self.rep
        if rep and r[1] == 0:
            return
        while True:
            if w:
                self.alu16(7, self.rw(srcseg + r[6]), self.rw(dstseg + r[7]))
            else:
                self.alu8(7, self.rb(srcseg + r[6]), self.rb(dstseg + r[7]))
            r[6] = (r[6] + step) & 0xFFFF
            r[7] = (r[7] + step) & 0xFFFF
            if not rep:
                return
            r[1] = (r[1] - 1) & 0xFFFF
            if r[1] == 0:
                return
            if rep == 0xF3 and not self.zf:
                return
            if rep == 0xF2 and self.zf:
                return

    def op_test_acc(self, op):
        if op == 0xA8:
            self.alu8(4, self.r[0] & 0xFF, self.fetch8())
        else:
            self.alu16(4, self.r[0], self.fetch16())

    def op_stos(self, op):
        r = self.r
        dstseg = self.sr[ES] << 4
        w = op & 1
        step = 2 if w else 1
        if self.df:
            step = -step
        if self.rep:
            cnt = r[1]
            if cnt == 0:
                return
            di = r[7]
            if w:
                v = r[0]
                for _ in range(cnt):
                    self.ww(dstseg + di, v)
                    di = (di + step) & 0xFFFF
            else:
                v = r[0] & 0xFF
                if step == 1 and di + cnt <= 0x10000 and dstseg + di + cnt <= 0xF0000:
                    d = dstseg + di
                    self.mem[d:d + cnt] = bytes([v]) * cnt
                    di = (di + cnt) & 0xFFFF
                else:
                    for _ in range(cnt):
                        self.wb(dstseg + di, v)
                        di = (di + step) & 0xFFFF
            r[7] = di
            r[1] = 0
        else:
            if w:
                self.ww(dstseg + r[7], r[0])
            else:
                self.wb(dstseg + r[7], r[0])
            r[7] = (r[7] + step) & 0xFFFF

    def op_lods(self, op):
        r = self.r
        srcseg = self.sr[self.ovr if self.ovr >= 0 else DS] << 4
        w = op & 1
        step = 2 if w else 1
        if self.df:
            step = -step
        cnt = r[1] if self.rep else 1
        if self.rep and cnt == 0:
            return
        for _ in range(cnt):
            if w:
                r[0] = self.rw(srcseg + r[6])
            else:
                r[0] = (r[0] & 0xFF00) | self.rb(srcseg + r[6])
            r[6] = (r[6] + step) & 0xFFFF
        if self.rep:
            r[1] = 0

    def op_scas(self, op):
        r = self.r
        dstseg = self.sr[ES] << 4
        w = op & 1
        step = 2 if w else 1
        if self.df:
            step = -step
        rep = self.rep
        if rep and r[1] == 0:
            return
        while True:
            if w:
                self.alu16(7, r[0], self.rw(dstseg + r[7]))
            else:
                self.alu8(7, r[0] & 0xFF, self.rb(dstseg + r[7]))
            r[7] = (r[7] + step) & 0xFFFF
            if not rep:
                return
            r[1] = (r[1] - 1) & 0xFFFF
            if r[1] == 0:
                return
            if rep == 0xF3 and not self.zf:
                return
            if rep == 0xF2 and self.zf:
                return

    def op_ins(self, op):
        r = self.r
        dstseg = self.sr[ES] << 4
        w = op & 1
        step = (2 if w else 1) * (-1 if self.df else 1)
        cnt = r[1] if self.rep else 1
        for _ in range(cnt):
            v = self.port_in(r[2], w)
            if w:
                self.ww(dstseg + r[7], v)
            else:
                self.wb(dstseg + r[7], v)
            r[7] = (r[7] + step) & 0xFFFF
        if self.rep:
            r[1] = 0

    def op_outs(self, op):
        r = self.r
        srcseg = self.sr[self.ovr if self.ovr >= 0 else DS] << 4
        w = op & 1
        step = (2 if w else 1) * (-1 if self.df else 1)
        cnt = r[1] if self.rep else 1
        for _ in range(cnt):
            v = self.rw(srcseg + r[6]) if w else self.rb(srcseg + r[6])
            self.port_out(r[2], v, w)
            r[6] = (r[6] + step) & 0xFFFF
        if self.rep:
            r[1] = 0

    def op_mov_r8_imm(self, op):
        self.set_r8(op & 7, self.fetch8())

    def op_mov_r16_imm(self, op):
        self.r[op & 7] = self.fetch16()

    def op_grp2(self, op):
        sub = self.modrm()
        if op <= 0xC1:
            # immediate follows the modrm displacement
            if op == 0xC0:
                v = self.get_rm8()
                self.set_rm8(self.shift(sub, v, self.fetch8(), 0))
            else:
                v = self.get_rm16()
                self.set_rm16(self.shift(sub, v, self.fetch8(), 1))
            return
        cnt = 1 if op <= 0xD1 else (self.r[1] & 0xFF)
        if op & 1:
            self.set_rm16(self.shift(sub, self.get_rm16(), cnt, 1))
        else:
            self.set_rm8(self.shift(sub, self.get_rm8(), cnt, 0))

    def op_ret(self, op):
        if op == 0xC2:
            n = self.fetch16()
            self.ip = self.pop()
            self.r[4] = (self.r[4] + n) & 0xFFFF
        else:
            self.ip = self.pop()

    def op_lxs(self, op):
        reg = self.modrm()
        self.r[reg] = self.get_rm16()
        self.sr[ES if op == 0xC4 else DS] = self.ea_word2()

    def op_mov_rm_imm(self, op):
        self.modrm()
        if op == 0xC6:
            self.set_rm8(self.fetch8())
        else:
            self.set_rm16(self.fetch16())

    def op_enter(self, op):
        size = self.fetch16()
        level = self.fetch8() & 0x1F
        r = self.r
        self.push(r[5])
        frame = r[4]
        if level > 0:
            bp = r[5]
            for _ in range(level - 1):
                bp = (bp - 2) & 0xFFFF
                self.push(self.rw((self.sr[SS] << 4) + bp))
            self.push(frame)
        r[5] = frame
        r[4] = (r[4] - size) & 0xFFFF

    def op_leave(self, op):
        r = self.r
        r[4] = r[5]
        r[5] = self.pop()

    def op_retf(self, op):
        n = self.fetch16() if op == 0xCA else 0
        self.ip = self.pop()
        self.sr[1] = self.pop()
        self.r[4] = (self.r[4] + n) & 0xFFFF

    def op_int3(self, op):
        self.interrupt(3)

    def op_int(self, op):
        n = self.fetch8()
        if self.int_hook is not None:
            self.int_hook(n)
        self.interrupt(n)

    def op_into(self, op):
        if self.of:
            self.interrupt(4)

    def op_iret(self, op):
        self.ip = self.pop()
        self.sr[1] = self.pop()
        self.set_flags(self.pop())

    def op_aam(self, op):
        base = self.fetch8()
        r = self.r
        al = r[0] & 0xFF
        if base == 0:
            self.interrupt(0)
            return
        ah = al // base
        al = al % base
        r[0] = (ah << 8) | al
        self.szp8(al)

    def op_aad(self, op):
        base = self.fetch8()
        r = self.r
        al = ((r[0] >> 8) * base + (r[0] & 0xFF)) & 0xFF
        r[0] = al
        self.szp8(al)

    def op_salc(self, op):
        self.set_r8(0, 0xFF if self.cf else 0)

    def op_xlat(self, op):
        r = self.r
        seg = self.ovr if self.ovr >= 0 else DS
        a = (self.sr[seg] << 4) + ((r[3] + (r[0] & 0xFF)) & 0xFFFF)
        r[0] = (r[0] & 0xFF00) | self.rb(a)

    def op_esc(self, op):
        # No 8087: decode and ignore the operand.  FNSTSW/FNSTCW leave memory
        # untouched, so "is a coprocessor present" probes answer "no".
        self.modrm()
        if self.on_unknown:
            self.on_unknown("fpu", op, self.op_cs, self.op_ip)

    def op_loop(self, op):
        d = self.fetchs8()
        r = self.r
        cx = (r[1] - 1) & 0xFFFF
        r[1] = cx
        if op == 0xE2:
            t = cx != 0
        elif op == 0xE1:
            t = cx != 0 and self.zf
        else:
            t = cx != 0 and not self.zf
        if t:
            self.ip = (self.ip + d) & 0xFFFF

    def op_jcxz(self, op):
        d = self.fetchs8()
        if self.r[1] == 0:
            self.ip = (self.ip + d) & 0xFFFF

    def op_in(self, op):
        port = self.fetch8() if op <= 0xE5 else self.r[2]
        r = self.r
        if op & 1:
            r[0] = self.port_in(port, 1) & 0xFFFF
        else:
            r[0] = (r[0] & 0xFF00) | (self.port_in(port, 0) & 0xFF)

    def op_out(self, op):
        port = self.fetch8() if op <= 0xE7 else self.r[2]
        if op & 1:
            self.port_out(port, self.r[0], 1)
        else:
            self.port_out(port, self.r[0] & 0xFF, 0)

    def op_call_near(self, op):
        d = self.fetch16()
        self.push(self.ip)
        self.ip = (self.ip + d) & 0xFFFF

    def op_jmp_near(self, op):
        d = self.fetch16()
        self.ip = (self.ip + d) & 0xFFFF

    def op_jmp_far(self, op):
        off = self.fetch16()
        seg = self.fetch16()
        self.ip = off
        self.sr[1] = seg

    def op_jmp_short(self, op):
        d = self.fetchs8()
        self.ip = (self.ip + d) & 0xFFFF

    def op_lock(self, op):
        op2 = self.fetch8()
        self.ops[op2](op2)

    def op_hle(self, op):
        n = self.fetch8()
        self.hle(n)

    def op_rep(self, op):
        self.rep = op
        op2 = self.fetch8()
        while op2 in (0x26, 0x2E, 0x36, 0x3E, 0xF0):
            if op2 != 0xF0:
                self.ovr = (op2 >> 3) & 3
            op2 = self.fetch8()
        self.ops[op2](op2)

    def op_hlt(self, op):
        self.halted = True

    def op_cmc(self, op):
        self.cf ^= 1

    def op_grp3(self, op):
        sub = self.modrm()
        r = self.r
        if op == 0xF6:
            v = self.get_rm8()
            if sub == 0 or sub == 1:
                self.alu8(4, v, self.fetch8())
            elif sub == 2:
                self.set_rm8(v ^ 0xFF)
            elif sub == 3:
                self.set_rm8(self.alu8(5, 0, v))
            elif sub == 4:
                res = (r[0] & 0xFF) * v
                r[0] = res
                self.cf = self.of = 1 if res & 0xFF00 else 0
                self.szp8(res & 0xFF)
            elif sub == 5:
                a = r[0] & 0xFF
                a = a - 256 if a & 0x80 else a
                b = v - 256 if v & 0x80 else v
                res = a * b
                r[0] = res & 0xFFFF
                self.cf = self.of = 0 if -128 <= res <= 127 else 1
                self.szp8(res & 0xFF)
            elif sub == 6:
                if v == 0:
                    self.interrupt(0)
                    return
                ax = r[0]
                q = ax // v
                if q > 0xFF:
                    self.interrupt(0)
                    return
                r[0] = ((ax % v) << 8) | q
            else:
                if v == 0:
                    self.interrupt(0)
                    return
                ax = r[0]
                a = ax - 0x10000 if ax & 0x8000 else ax
                b = v - 256 if v & 0x80 else v
                q = abs(a) // abs(b)
                if (a < 0) != (b < 0):
                    q = -q
                rem = a - q * b
                if q > 127 or q < -128:
                    self.interrupt(0)
                    return
                r[0] = ((rem & 0xFF) << 8) | (q & 0xFF)
        else:
            v = self.get_rm16()
            if sub == 0 or sub == 1:
                self.alu16(4, v, self.fetch16())
            elif sub == 2:
                self.set_rm16(v ^ 0xFFFF)
            elif sub == 3:
                self.set_rm16(self.alu16(5, 0, v))
            elif sub == 4:
                res = r[0] * v
                r[0] = res & 0xFFFF
                r[2] = res >> 16
                self.cf = self.of = 1 if r[2] else 0
                self.szp16(res & 0xFFFF)
            elif sub == 5:
                a = r[0] - 0x10000 if r[0] & 0x8000 else r[0]
                b = v - 0x10000 if v & 0x8000 else v
                res = a * b
                r[0] = res & 0xFFFF
                r[2] = (res >> 16) & 0xFFFF
                self.cf = self.of = 0 if -0x8000 <= res <= 0x7FFF else 1
                self.szp16(res & 0xFFFF)
            elif sub == 6:
                if v == 0:
                    self.interrupt(0)
                    return
                num = (r[2] << 16) | r[0]
                q = num // v
                if q > 0xFFFF:
                    self.interrupt(0)
                    return
                r[0] = q
                r[2] = num % v
            else:
                if v == 0:
                    self.interrupt(0)
                    return
                num = (r[2] << 16) | r[0]
                if num & 0x80000000:
                    num -= 0x100000000
                b = v - 0x10000 if v & 0x8000 else v
                q = abs(num) // abs(b)
                if (num < 0) != (b < 0):
                    q = -q
                rem = num - q * b
                if q > 0x7FFF or q < -0x8000:
                    self.interrupt(0)
                    return
                r[0] = q & 0xFFFF
                r[2] = rem & 0xFFFF

    def op_clc(self, op):
        self.cf = 0

    def op_stc(self, op):
        self.cf = 1

    def op_cli(self, op):
        self.iflag = 0

    def op_sti(self, op):
        self.iflag = 1
        self.inhibit = True

    def op_cld(self, op):
        self.df = 0

    def op_std(self, op):
        self.df = 1

    def op_grp4(self, op):
        sub = self.modrm()
        v = self.get_rm8()
        if sub == 0:
            self.set_rm8(self.inc8(v))
        elif sub == 1:
            self.set_rm8(self.dec8(v))
        else:
            self.op_invalid(op)

    def op_grp5(self, op):
        sub = self.modrm()
        if sub == 0:
            self.set_rm16(self.inc16(self.get_rm16()))
        elif sub == 1:
            self.set_rm16(self.dec16(self.get_rm16()))
        elif sub == 2:
            t = self.get_rm16()
            self.push(self.ip)
            self.ip = t
        elif sub == 3:
            off = self.get_rm16()
            seg = self.ea_word2()
            self.push(self.sr[1])
            self.push(self.ip)
            self.ip = off
            self.sr[1] = seg
        elif sub == 4:
            self.ip = self.get_rm16()
        elif sub == 5:
            off = self.get_rm16()
            seg = self.ea_word2()
            self.ip = off
            self.sr[1] = seg
        elif sub == 6:
            v = self.get_rm16()
            self.push(v)
        else:
            self.op_invalid(op)
