## 8086 + 80186 real-mode CPU core.  One-to-one port of tools/refemu/cpu.py:
## same register layout, same function names, same flag rules.
##
## * Memory is one PackedByteArray of 1 MB (+16 guard bytes), owned by this
##   object.  Other modules access it as `cpu.mem` (never through a local copy).
## * r[] = AX CX DX BX SP BP SI DI, sr[] = ES CS SS DS (16-bit values).
## * Flags are separate 0/1 ints, computed eagerly.
## * Opcode F1 nn is the emulator trap: the ROM stubs contain F1 nn CF, and
##   executing it calls machine.hle(nn) (BIOS/DOS services written in GDScript).
class_name CPU
extends RefCounted

const ES := 0
const CS := 1
const SS := 2
const DS := 3

var mem: PackedByteArray = PackedByteArray()
var r: PackedInt32Array = PackedInt32Array([0, 0, 0, 0, 0, 0, 0, 0])
var sr: PackedInt32Array = PackedInt32Array([0, 0, 0, 0])
var ip: int = 0
var cf: int = 0
var pf: int = 0
var af: int = 0
var zf: int = 0
var sf: int = 0
var tf: int = 0
var iflag: int = 0
var df: int = 0
var of: int = 0
var ovr: int = -1
var rep: int = 0
var op_cs: int = 0
var op_ip: int = 0
var inhibit: bool = false
var halted: bool = false
var icount: int = 0
var executed: int = 0
var ea: int = 0
var ea_off: int = 0
var ea_seg: int = DS
var ea_reg: int = -1
var irq_flag: bool = false
var machine = null  # Machine (untyped to avoid a cyclic class reference)
var parity: PackedByteArray = PackedByteArray()
var trace: PackedInt64Array = PackedInt64Array()
var trace_len: int = 0
var trace_pos: int = 0


func _init() -> void:
	mem.resize(0x100000 + 16)
	parity.resize(256)
	for i in range(256):
		var bits := 0
		var v := i
		while v:
			bits += v & 1
			v >>= 1
		parity[i] = 1 if bits % 2 == 0 else 0


# ------------------------------------------------------------------ flags
func get_flags() -> int:
	return (cf | 2 | (pf << 2) | (af << 4) | (zf << 6) | (sf << 7) | (tf << 8)
		| (iflag << 9) | (df << 10) | (of << 11) | 0xF000)


func set_flags(v: int) -> void:
	cf = v & 1
	pf = (v >> 2) & 1
	af = (v >> 4) & 1
	zf = (v >> 6) & 1
	sf = (v >> 7) & 1
	tf = (v >> 8) & 1
	iflag = (v >> 9) & 1
	df = (v >> 10) & 1
	of = (v >> 11) & 1


func szp8(v: int) -> void:
	zf = 1 if v == 0 else 0
	sf = v >> 7
	pf = parity[v]


func szp16(v: int) -> void:
	zf = 1 if v == 0 else 0
	sf = v >> 15
	pf = parity[v & 0xFF]


# ----------------------------------------------------------------- memory
func rb(a: int) -> int:
	return mem[a & 0xFFFFF]


func rw(a: int) -> int:
	a &= 0xFFFFF
	return mem[a] | (mem[(a + 1) & 0xFFFFF] << 8)


func wb(a: int, v: int) -> void:
	a &= 0xFFFFF
	if a < 0xF0000:
		mem[a] = v & 0xFF


func ww(a: int, v: int) -> void:
	a &= 0xFFFFF
	if a < 0xF0000:
		mem[a] = v & 0xFF
		mem[(a + 1) & 0xFFFFF] = (v >> 8) & 0xFF


func fetch8() -> int:
	var v: int = mem[((sr[1] << 4) + ip) & 0xFFFFF]
	ip = (ip + 1) & 0xFFFF
	return v


func fetch16() -> int:
	var base: int = sr[1] << 4
	var v: int = mem[(base + ip) & 0xFFFFF] | (mem[(base + ((ip + 1) & 0xFFFF)) & 0xFFFFF] << 8)
	ip = (ip + 2) & 0xFFFF
	return v


func fetchs8() -> int:
	var v := fetch8()
	return v - 256 if v & 0x80 else v


func push(v: int) -> void:
	var sp: int = (r[4] - 2) & 0xFFFF
	r[4] = sp
	ww((sr[2] << 4) + sp, v)


func pop() -> int:
	var sp: int = r[4]
	var v := rw((sr[2] << 4) + sp)
	r[4] = (sp + 2) & 0xFFFF
	return v


# -------------------------------------------------------------- registers
func get_r8(i: int) -> int:
	if i < 4:
		return r[i] & 0xFF
	return r[i - 4] >> 8


func set_r8(i: int, v: int) -> void:
	if i < 4:
		r[i] = (r[i] & 0xFF00) | (v & 0xFF)
	else:
		r[i - 4] = (r[i - 4] & 0x00FF) | ((v & 0xFF) << 8)


# ------------------------------------------------------------------ modrm
## Decode a ModRM byte. Returns the reg field. Sets ea_reg / ea / ea_off.
func modrm() -> int:
	var m := fetch8()
	var mod := m >> 6
	var rmv := m & 7
	if mod == 3:
		ea_reg = rmv
		return (m >> 3) & 7
	ea_reg = -1
	var seg := DS
	var off := 0
	if rmv == 0:
		off = r[3] + r[6]
	elif rmv == 1:
		off = r[3] + r[7]
	elif rmv == 2:
		off = r[5] + r[6]
		seg = SS
	elif rmv == 3:
		off = r[5] + r[7]
		seg = SS
	elif rmv == 4:
		off = r[6]
	elif rmv == 5:
		off = r[7]
	elif rmv == 6:
		if mod == 0:
			off = fetch16()
		else:
			off = r[5]
			seg = SS
	else:
		off = r[3]
	if mod == 1:
		off += fetchs8()
	elif mod == 2:
		off += fetch16()
	off &= 0xFFFF
	if ovr >= 0:
		seg = ovr
	ea_off = off
	ea_seg = seg
	ea = ((sr[seg] << 4) + off) & 0xFFFFF
	return (m >> 3) & 7


func get_rm8() -> int:
	if ea_reg >= 0:
		return get_r8(ea_reg)
	return mem[ea]


func set_rm8(v: int) -> void:
	if ea_reg >= 0:
		set_r8(ea_reg, v)
	else:
		wb(ea, v)


func get_rm16() -> int:
	if ea_reg >= 0:
		return r[ea_reg]
	if ea_off == 0xFFFF:
		return mem[ea] | (mem[(ea - 0xFFFF) & 0xFFFFF] << 8)
	return mem[ea] | (mem[(ea + 1) & 0xFFFFF] << 8)


func set_rm16(v: int) -> void:
	if ea_reg >= 0:
		r[ea_reg] = v & 0xFFFF
	else:
		ww(ea, v)


## Second word of a memory operand (for LES/LDS/far pointers).
func ea_word2() -> int:
	return rw((sr[ea_seg] << 4) + ((ea_off + 2) & 0xFFFF))


# -------------------------------------------------------------------- ALU
func alu8(op: int, a: int, b: int) -> int:
	var res := 0
	if op == 0:
		res = a + b
		cf = res >> 8
		res &= 0xFF
		of = ((a ^ res) & (b ^ res)) >> 7
		af = ((a ^ b ^ res) >> 4) & 1
	elif op == 1:
		res = a | b
		cf = 0
		of = 0
		af = 0
	elif op == 2:
		res = a + b + cf
		cf = res >> 8
		res &= 0xFF
		of = ((a ^ res) & (b ^ res)) >> 7
		af = ((a ^ b ^ res) >> 4) & 1
	elif op == 3:
		res = a - b - cf
		cf = 1 if res < 0 else 0
		res &= 0xFF
		of = ((a ^ b) & (a ^ res)) >> 7
		af = ((a ^ b ^ res) >> 4) & 1
	elif op == 4:
		res = a & b
		cf = 0
		of = 0
		af = 0
	elif op == 6:
		res = a ^ b
		cf = 0
		of = 0
		af = 0
	else:
		res = a - b
		cf = 1 if res < 0 else 0
		res &= 0xFF
		of = ((a ^ b) & (a ^ res)) >> 7
		af = ((a ^ b ^ res) >> 4) & 1
	zf = 1 if res == 0 else 0
	sf = res >> 7
	pf = parity[res]
	return res


func alu16(op: int, a: int, b: int) -> int:
	var res := 0
	if op == 0:
		res = a + b
		cf = res >> 16
		res &= 0xFFFF
		of = ((a ^ res) & (b ^ res)) >> 15
		af = ((a ^ b ^ res) >> 4) & 1
	elif op == 1:
		res = a | b
		cf = 0
		of = 0
		af = 0
	elif op == 2:
		res = a + b + cf
		cf = res >> 16
		res &= 0xFFFF
		of = ((a ^ res) & (b ^ res)) >> 15
		af = ((a ^ b ^ res) >> 4) & 1
	elif op == 3:
		res = a - b - cf
		cf = 1 if res < 0 else 0
		res &= 0xFFFF
		of = ((a ^ b) & (a ^ res)) >> 15
		af = ((a ^ b ^ res) >> 4) & 1
	elif op == 4:
		res = a & b
		cf = 0
		of = 0
		af = 0
	elif op == 6:
		res = a ^ b
		cf = 0
		of = 0
		af = 0
	else:
		res = a - b
		cf = 1 if res < 0 else 0
		res &= 0xFFFF
		of = ((a ^ b) & (a ^ res)) >> 15
		af = ((a ^ b ^ res) >> 4) & 1
	zf = 1 if res == 0 else 0
	sf = res >> 15
	pf = parity[res & 0xFF]
	return res


func inc8(a: int) -> int:
	var res := (a + 1) & 0xFF
	of = 1 if res == 0x80 else 0
	af = 1 if (res & 0x0F) == 0 else 0
	szp8(res)
	return res


func dec8(a: int) -> int:
	var res := (a - 1) & 0xFF
	of = 1 if res == 0x7F else 0
	af = 1 if (res & 0x0F) == 0x0F else 0
	szp8(res)
	return res


func inc16(a: int) -> int:
	var res := (a + 1) & 0xFFFF
	of = 1 if res == 0x8000 else 0
	af = 1 if (res & 0x0F) == 0 else 0
	szp16(res)
	return res


func dec16(a: int) -> int:
	var res := (a - 1) & 0xFFFF
	of = 1 if res == 0x7FFF else 0
	af = 1 if (res & 0x0F) == 0x0F else 0
	szp16(res)
	return res


## Group-2 rotate/shift. op = reg field, w = 0 byte / 1 word.
func shift(op: int, v: int, cnt: int, w: int) -> int:
	cnt &= 0x1F
	if cnt == 0:
		return v
	var bits := 16 if w else 8
	var mask := 0xFFFF if w else 0xFF
	var msb := bits - 1
	if op == 0:
		var c := cnt % bits
		if c:
			v = ((v << c) | (v >> (bits - c))) & mask
		cf = v & 1
		of = ((v >> msb) & 1) ^ cf
	elif op == 1:
		var c := cnt % bits
		if c:
			v = ((v >> c) | (v << (bits - c))) & mask
		cf = (v >> msb) & 1
		of = ((v >> msb) ^ (v >> (msb - 1))) & 1
	elif op == 2:
		var c := cnt % (bits + 1)
		var cc := cf
		for _i in range(c):
			var nc := (v >> msb) & 1
			v = ((v << 1) | cc) & mask
			cc = nc
		cf = cc
		of = ((v >> msb) & 1) ^ cc
	elif op == 3:
		var c := cnt % (bits + 1)
		var cc := cf
		for _i in range(c):
			var nc := v & 1
			v = (v >> 1) | (cc << msb)
			cc = nc
		cf = cc
		of = ((v >> msb) ^ (v >> (msb - 1))) & 1
	elif op == 4 or op == 6:
		if cnt > bits:
			cf = 0
			v = 0
		else:
			cf = (v >> (bits - cnt)) & 1
			v = (v << cnt) & mask
		of = ((v >> msb) & 1) ^ cf
		af = 0
		if w:
			szp16(v)
		else:
			szp8(v)
	elif op == 5:
		of = (v >> msb) & 1
		if cnt > bits:
			cf = 0
			v = 0
		else:
			cf = (v >> (cnt - 1)) & 1
			v >>= cnt
		af = 0
		if w:
			szp16(v)
		else:
			szp8(v)
	else:
		var sv := v - (1 << bits) if (v >> msb) else v
		if cnt >= bits:
			cnt = bits
		cf = (sv >> (cnt - 1)) & 1
		v = (sv >> cnt) & mask
		of = 0
		af = 0
		if w:
			szp16(v)
		else:
			szp8(v)
	return v


# ------------------------------------------------------------- interrupts
## Deliver interrupt n (software or hardware).
func interrupt(n: int) -> void:
	push(get_flags())
	push(sr[1])
	push(ip)
	iflag = 0
	tf = 0
	ip = rw(n * 4)
	sr[1] = rw(n * 4 + 2)
	halted = false


# -------------------------------------------------------------- main loop
## Execute up to n instructions. Returns number executed.
func run(n: int) -> int:
	var i := 0
	while i < n:
		if irq_flag and iflag and not inhibit:
			var irq: int = machine.pic.ack()
			if irq >= 0:
				interrupt(irq)
		if halted:
			break
		inhibit = false
		ovr = -1
		rep = 0
		op_cs = sr[1]
		op_ip = ip
		if trace_len > 0:
			trace[trace_pos] = (op_cs << 16) | op_ip
			trace_pos = (trace_pos + 1) % trace_len
		var op: int = mem[((op_cs << 4) + ip) & 0xFFFFF]
		ip = (ip + 1) & 0xFFFF
		exec_op(op)
		i += 1
	icount += i
	executed += i
	return i


## Opcode dispatch.  Two-level match (high nibble, then opcode) keeps the number
## of pattern tests per instruction small.  tools/check_port.py parses the hex
## literals of the inner match patterns to cross-check opcode coverage.
func exec_op(op: int) -> void:
	match op >> 4:
		0x0, 0x1, 0x2, 0x3:
			match op:
				0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D:
					op_alu(op)
				0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D:
					op_alu(op)
				0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x2D:
					op_alu(op)
				0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D:
					op_alu(op)
				0x06, 0x0E, 0x16, 0x1E:
					op_push_seg(op)
				0x07, 0x17, 0x1F:
					op_pop_seg(op)
				0x26, 0x2E, 0x36, 0x3E:
					op_segovr(op)
				0x27:
					op_daa(op)
				0x2F:
					op_das(op)
				0x37:
					op_aaa(op)
				0x3F:
					op_aas(op)
				_:
					op_invalid(op)
		0x4:
			match op:
				0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47:
					r[op & 7] = inc16(r[op & 7])
				0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E, 0x4F:
					r[op & 7] = dec16(r[op & 7])
		0x5:
			match op:
				0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57:
					op_push_r(op)
				0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F:
					op_pop_r(op)
		0x6:
			match op:
				0x60:
					op_pusha(op)
				0x61:
					op_popa(op)
				0x62:
					op_bound(op)
				0x68:
					push(fetch16())
				0x69, 0x6B:
					op_imul_imm(op)
				0x6A:
					push(fetchs8() & 0xFFFF)
				0x6C, 0x6D:
					op_ins(op)
				0x6E, 0x6F:
					op_outs(op)
				_:
					op_invalid(op)
		0x7:
			match op:
				0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77:
					op_jcc(op)
				0x78, 0x79, 0x7A, 0x7B, 0x7C, 0x7D, 0x7E, 0x7F:
					op_jcc(op)
		0x8:
			match op:
				0x80, 0x81, 0x82, 0x83:
					op_grp1(op)
				0x84, 0x85:
					op_test_rm(op)
				0x86, 0x87:
					op_xchg_rm(op)
				0x88:
					var reg := modrm()
					set_rm8(get_r8(reg))
				0x89:
					var reg := modrm()
					set_rm16(r[reg])
				0x8A:
					var reg := modrm()
					set_r8(reg, get_rm8())
				0x8B:
					var reg := modrm()
					r[reg] = get_rm16()
				0x8C:
					var reg := modrm()
					set_rm16(sr[reg & 3])
				0x8D:
					var reg := modrm()
					r[reg] = ea_off
				0x8E:
					op_mov_seg_rm(op)
				0x8F:
					var v := pop()
					modrm()
					set_rm16(v)
		0x9:
			match op:
				0x90, 0x9B:
					pass
				0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97:
					var t: int = r[0]
					r[0] = r[op & 7]
					r[op & 7] = t
				0x98:
					var al: int = r[0] & 0xFF
					r[0] = (al | 0xFF00) if al & 0x80 else al
				0x99:
					r[2] = 0xFFFF if r[0] & 0x8000 else 0
				0x9A:
					op_call_far(op)
				0x9C:
					push(get_flags())
				0x9D:
					set_flags(pop())
				0x9E:
					op_sahf(op)
				0x9F:
					r[0] = (r[0] & 0xFF) | ((get_flags() & 0xFF) << 8)
		0xA:
			match op:
				0xA0, 0xA1, 0xA2, 0xA3:
					op_mov_moffs(op)
				0xA4, 0xA5:
					op_movs(op)
				0xA6, 0xA7:
					op_cmps(op)
				0xA8:
					alu8(4, r[0] & 0xFF, fetch8())
				0xA9:
					alu16(4, r[0], fetch16())
				0xAA, 0xAB:
					op_stos(op)
				0xAC, 0xAD:
					op_lods(op)
				0xAE, 0xAF:
					op_scas(op)
		0xB:
			match op:
				0xB0, 0xB1, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7:
					set_r8(op & 7, fetch8())
				0xB8, 0xB9, 0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF:
					r[op & 7] = fetch16()
		0xC:
			match op:
				0xC0, 0xC1:
					op_grp2(op)
				0xC2, 0xC3:
					op_ret(op)
				0xC4, 0xC5:
					op_lxs(op)
				0xC6, 0xC7:
					op_mov_rm_imm(op)
				0xC8:
					op_enter(op)
				0xC9:
					r[4] = r[5]
					r[5] = pop()
				0xCA, 0xCB:
					op_retf(op)
				0xCC:
					interrupt(3)
				0xCD:
					var n := fetch8()
					if machine.trace_ints and op_cs != 0xF000:
						machine.count_int(n, r[0] >> 8)
					interrupt(n)
				0xCE:
					if of:
						interrupt(4)
				0xCF:
					ip = pop()
					sr[1] = pop()
					set_flags(pop())
		0xD:
			match op:
				0xD0, 0xD1, 0xD2, 0xD3:
					op_grp2(op)
				0xD4:
					op_aam(op)
				0xD5:
					op_aad(op)
				0xD6:
					set_r8(0, 0xFF if cf else 0)
				0xD7:
					var seg := ovr if ovr >= 0 else DS
					var a: int = (sr[seg] << 4) + ((r[3] + (r[0] & 0xFF)) & 0xFFFF)
					r[0] = (r[0] & 0xFF00) | rb(a)
				0xD8, 0xD9, 0xDA, 0xDB, 0xDC, 0xDD, 0xDE, 0xDF:
					op_esc(op)
		0xE:
			match op:
				0xE0, 0xE1, 0xE2:
					op_loop(op)
				0xE3:
					var d := fetchs8()
					if r[1] == 0:
						ip = (ip + d) & 0xFFFF
				0xE4, 0xE5, 0xEC, 0xED:
					op_in(op)
				0xE6, 0xE7, 0xEE, 0xEF:
					op_out(op)
				0xE8:
					var d := fetch16()
					push(ip)
					ip = (ip + d) & 0xFFFF
				0xE9:
					var d := fetch16()
					ip = (ip + d) & 0xFFFF
				0xEA:
					var off := fetch16()
					var seg := fetch16()
					ip = off
					sr[1] = seg
				0xEB:
					var d := fetchs8()
					ip = (ip + d) & 0xFFFF
		_:
			match op:
				0xF0:
					exec_op(fetch8())
				0xF1:
					machine.hle(fetch8())
				0xF2, 0xF3:
					op_rep(op)
				0xF4:
					halted = true
				0xF5:
					cf ^= 1
				0xF6, 0xF7:
					op_grp3(op)
				0xF8:
					cf = 0
				0xF9:
					cf = 1
				0xFA:
					iflag = 0
				0xFB:
					iflag = 1
					inhibit = true
				0xFC:
					df = 0
				0xFD:
					df = 1
				0xFE:
					op_grp4(op)
				0xFF:
					op_grp5(op)


func op_invalid(op: int) -> void:
	machine.on_unknown("opcode", op, op_cs, op_ip)
	ip = op_ip
	interrupt(6)


func op_alu(op: int) -> void:
	var alu := op >> 3
	var form := op & 7
	if form == 0:
		var reg := modrm()
		var res := alu8(alu, get_rm8(), get_r8(reg))
		if alu != 7:
			set_rm8(res)
	elif form == 1:
		var reg := modrm()
		var res := alu16(alu, get_rm16(), r[reg])
		if alu != 7:
			set_rm16(res)
	elif form == 2:
		var reg := modrm()
		var res := alu8(alu, get_r8(reg), get_rm8())
		if alu != 7:
			set_r8(reg, res)
	elif form == 3:
		var reg := modrm()
		var res := alu16(alu, r[reg], get_rm16())
		if alu != 7:
			r[reg] = res
	elif form == 4:
		var res := alu8(alu, r[0] & 0xFF, fetch8())
		if alu != 7:
			r[0] = (r[0] & 0xFF00) | res
	else:
		var res := alu16(alu, r[0], fetch16())
		if alu != 7:
			r[0] = res


func op_push_seg(op: int) -> void:
	push(sr[op >> 3])


func op_pop_seg(op: int) -> void:
	sr[op >> 3] = pop()
	if op == 0x17:
		inhibit = true


func op_segovr(op: int) -> void:
	ovr = (op >> 3) & 3
	exec_op(fetch8())


func op_daa(_op: int) -> void:
	var al: int = r[0] & 0xFF
	var old_al := al
	var old_cf := cf
	if (al & 0x0F) > 9 or af:
		al += 6
		af = 1
	else:
		af = 0
	if old_al > 0x99 or old_cf:
		al += 0x60
		cf = 1
	else:
		cf = 0
	al &= 0xFF
	r[0] = (r[0] & 0xFF00) | al
	szp8(al)


func op_das(_op: int) -> void:
	var al: int = r[0] & 0xFF
	var old_al := al
	var old_cf := cf
	if (al & 0x0F) > 9 or af:
		al -= 6
		af = 1
	else:
		af = 0
	if old_al > 0x99 or old_cf:
		al -= 0x60
		cf = 1
	else:
		cf = 0
	al &= 0xFF
	r[0] = (r[0] & 0xFF00) | al
	szp8(al)


func op_aaa(_op: int) -> void:
	var ax: int = r[0]
	if (ax & 0x0F) > 9 or af:
		ax = (ax + 0x106) & 0xFFFF
		af = 1
		cf = 1
	else:
		af = 0
		cf = 0
	r[0] = ax & 0xFF0F
	szp8(r[0] & 0xFF)


func op_aas(_op: int) -> void:
	var ax: int = r[0]
	if (ax & 0x0F) > 9 or af:
		var al := ((ax & 0xFF) - 6) & 0xFF
		var ah := ((ax >> 8) - 1) & 0xFF
		ax = (ah << 8) | al
		af = 1
		cf = 1
	else:
		af = 0
		cf = 0
	r[0] = ax & 0xFF0F
	szp8(r[0] & 0xFF)


func op_push_r(op: int) -> void:
	if op == 0x54:
		push((r[4] - 2) & 0xFFFF)
	else:
		push(r[op & 7])


func op_pop_r(op: int) -> void:
	var v := pop()
	r[op & 7] = v


func op_pusha(_op: int) -> void:
	var sp: int = r[4]
	for i in range(8):
		push(sp if i == 4 else r[i])


func op_popa(_op: int) -> void:
	for i in [7, 6, 5, 4, 3, 2, 1, 0]:
		var v := pop()
		if i != 4:
			r[i] = v


func op_bound(_op: int) -> void:
	var reg := modrm()
	var lo := get_rm16()
	var hi := ea_word2()
	var v: int = r[reg]
	var sv := v - 0x10000 if v & 0x8000 else v
	var slo := lo - 0x10000 if lo & 0x8000 else lo
	var shi := hi - 0x10000 if hi & 0x8000 else hi
	if sv < slo or sv > shi:
		ip = op_ip
		interrupt(5)


func op_imul_imm(op: int) -> void:
	var reg := modrm()
	var a := get_rm16()
	var b := 0
	if op == 0x69:
		b = fetch16()
		if b & 0x8000:
			b -= 0x10000
	else:
		b = fetchs8()
	if a & 0x8000:
		a -= 0x10000
	var res := a * b
	r[reg] = res & 0xFFFF
	cf = 0 if (res >= -0x8000 and res <= 0x7FFF) else 1
	of = cf
	szp16(res & 0xFFFF)


func op_jcc(op: int) -> void:
	var d := fetchs8()
	var c := op & 0x0F
	var t := false
	match c:
		0:
			t = of != 0
		1:
			t = of == 0
		2:
			t = cf != 0
		3:
			t = cf == 0
		4:
			t = zf != 0
		5:
			t = zf == 0
		6:
			t = cf != 0 or zf != 0
		7:
			t = cf == 0 and zf == 0
		8:
			t = sf != 0
		9:
			t = sf == 0
		10:
			t = pf != 0
		11:
			t = pf == 0
		12:
			t = sf != of
		13:
			t = sf == of
		14:
			t = zf != 0 or sf != of
		_:
			t = zf == 0 and sf == of
	if t:
		ip = (ip + d) & 0xFFFF


func op_grp1(op: int) -> void:
	var alu := modrm()
	if (op & 1) == 0:
		var a := get_rm8()
		var b := fetch8()
		var res := alu8(alu, a, b)
		if alu != 7:
			set_rm8(res)
	else:
		var a := get_rm16()
		var b := fetch16() if op == 0x81 else (fetchs8() & 0xFFFF)
		var res := alu16(alu, a, b)
		if alu != 7:
			set_rm16(res)


func op_test_rm(op: int) -> void:
	var reg := modrm()
	if op == 0x84:
		alu8(4, get_rm8(), get_r8(reg))
	else:
		alu16(4, get_rm16(), r[reg])


func op_xchg_rm(op: int) -> void:
	var reg := modrm()
	if op == 0x86:
		var a := get_rm8()
		set_rm8(get_r8(reg))
		set_r8(reg, a)
	else:
		var a := get_rm16()
		set_rm16(r[reg])
		r[reg] = a


func op_mov_seg_rm(_op: int) -> void:
	var reg := modrm() & 3
	sr[reg] = get_rm16()
	if reg == SS:
		inhibit = true


func op_call_far(_op: int) -> void:
	var off := fetch16()
	var seg := fetch16()
	push(sr[1])
	push(ip)
	sr[1] = seg
	ip = off


func op_sahf(_op: int) -> void:
	var ah: int = r[0] >> 8
	cf = ah & 1
	pf = (ah >> 2) & 1
	af = (ah >> 4) & 1
	zf = (ah >> 6) & 1
	sf = (ah >> 7) & 1


func op_mov_moffs(op: int) -> void:
	var off := fetch16()
	var seg := ovr if ovr >= 0 else DS
	var a: int = (sr[seg] << 4) + off
	if op == 0xA0:
		r[0] = (r[0] & 0xFF00) | rb(a)
	elif op == 0xA1:
		r[0] = rw(a)
	elif op == 0xA2:
		wb(a, r[0])
	else:
		ww(a, r[0])


# ---- string instructions
func op_movs(op: int) -> void:
	var srcseg: int = sr[ovr if ovr >= 0 else DS] << 4
	var dstseg: int = sr[ES] << 4
	var step := 1 if op == 0xA4 else 2
	if df:
		step = -step
	if rep:
		var cnt: int = r[1]
		if cnt == 0:
			return
		var si: int = r[6]
		var di: int = r[7]
		if op == 0xA4:
			for _i in range(cnt):
				wb(dstseg + di, mem[(srcseg + si) & 0xFFFFF])
				si = (si + step) & 0xFFFF
				di = (di + step) & 0xFFFF
		else:
			for _i in range(cnt):
				ww(dstseg + di, rw(srcseg + si))
				si = (si + step) & 0xFFFF
				di = (di + step) & 0xFFFF
		r[6] = si
		r[7] = di
		r[1] = 0
	else:
		if op == 0xA4:
			wb(dstseg + r[7], mem[(srcseg + r[6]) & 0xFFFFF])
		else:
			ww(dstseg + r[7], rw(srcseg + r[6]))
		r[6] = (r[6] + step) & 0xFFFF
		r[7] = (r[7] + step) & 0xFFFF


func op_cmps(op: int) -> void:
	var srcseg: int = sr[ovr if ovr >= 0 else DS] << 4
	var dstseg: int = sr[ES] << 4
	var w := op & 1
	var step := 2 if w else 1
	if df:
		step = -step
	if rep and r[1] == 0:
		return
	while true:
		if w:
			alu16(7, rw(srcseg + r[6]), rw(dstseg + r[7]))
		else:
			alu8(7, rb(srcseg + r[6]), rb(dstseg + r[7]))
		r[6] = (r[6] + step) & 0xFFFF
		r[7] = (r[7] + step) & 0xFFFF
		if not rep:
			return
		r[1] = (r[1] - 1) & 0xFFFF
		if r[1] == 0:
			return
		if rep == 0xF3 and not zf:
			return
		if rep == 0xF2 and zf:
			return


func op_stos(op: int) -> void:
	var dstseg: int = sr[ES] << 4
	var w := op & 1
	var step := 2 if w else 1
	if df:
		step = -step
	if rep:
		var cnt: int = r[1]
		if cnt == 0:
			return
		var di: int = r[7]
		if w:
			var v: int = r[0]
			for _i in range(cnt):
				ww(dstseg + di, v)
				di = (di + step) & 0xFFFF
		else:
			var v: int = r[0] & 0xFF
			for _i in range(cnt):
				wb(dstseg + di, v)
				di = (di + step) & 0xFFFF
		r[7] = di
		r[1] = 0
	else:
		if w:
			ww(dstseg + r[7], r[0])
		else:
			wb(dstseg + r[7], r[0])
		r[7] = (r[7] + step) & 0xFFFF


func op_lods(op: int) -> void:
	var srcseg: int = sr[ovr if ovr >= 0 else DS] << 4
	var w := op & 1
	var step := 2 if w else 1
	if df:
		step = -step
	var cnt: int = r[1] if rep else 1
	if rep and cnt == 0:
		return
	for _i in range(cnt):
		if w:
			r[0] = rw(srcseg + r[6])
		else:
			r[0] = (r[0] & 0xFF00) | rb(srcseg + r[6])
		r[6] = (r[6] + step) & 0xFFFF
	if rep:
		r[1] = 0


func op_scas(op: int) -> void:
	var dstseg: int = sr[ES] << 4
	var w := op & 1
	var step := 2 if w else 1
	if df:
		step = -step
	if rep and r[1] == 0:
		return
	while true:
		if w:
			alu16(7, r[0], rw(dstseg + r[7]))
		else:
			alu8(7, r[0] & 0xFF, rb(dstseg + r[7]))
		r[7] = (r[7] + step) & 0xFFFF
		if not rep:
			return
		r[1] = (r[1] - 1) & 0xFFFF
		if r[1] == 0:
			return
		if rep == 0xF3 and not zf:
			return
		if rep == 0xF2 and zf:
			return


func op_ins(op: int) -> void:
	var dstseg: int = sr[ES] << 4
	var w := op & 1
	var step := (2 if w else 1) * (-1 if df else 1)
	var cnt: int = r[1] if rep else 1
	for _i in range(cnt):
		var v: int = machine.port_in(r[2], w)
		if w:
			ww(dstseg + r[7], v)
		else:
			wb(dstseg + r[7], v)
		r[7] = (r[7] + step) & 0xFFFF
	if rep:
		r[1] = 0


func op_outs(op: int) -> void:
	var srcseg: int = sr[ovr if ovr >= 0 else DS] << 4
	var w := op & 1
	var step := (2 if w else 1) * (-1 if df else 1)
	var cnt: int = r[1] if rep else 1
	for _i in range(cnt):
		var v := rw(srcseg + r[6]) if w else rb(srcseg + r[6])
		machine.port_out(r[2], v, w)
		r[6] = (r[6] + step) & 0xFFFF
	if rep:
		r[1] = 0


func op_grp2(op: int) -> void:
	var sub := modrm()
	if op <= 0xC1:
		if op == 0xC0:
			var v := get_rm8()
			set_rm8(shift(sub, v, fetch8(), 0))
		else:
			var v := get_rm16()
			set_rm16(shift(sub, v, fetch8(), 1))
		return
	var cnt: int = 1 if op <= 0xD1 else (r[1] & 0xFF)
	if op & 1:
		set_rm16(shift(sub, get_rm16(), cnt, 1))
	else:
		set_rm8(shift(sub, get_rm8(), cnt, 0))


func op_ret(op: int) -> void:
	if op == 0xC2:
		var n := fetch16()
		ip = pop()
		r[4] = (r[4] + n) & 0xFFFF
	else:
		ip = pop()


func op_lxs(op: int) -> void:
	var reg := modrm()
	r[reg] = get_rm16()
	sr[ES if op == 0xC4 else DS] = ea_word2()


func op_mov_rm_imm(op: int) -> void:
	modrm()
	if op == 0xC6:
		set_rm8(fetch8())
	else:
		set_rm16(fetch16())


func op_enter(_op: int) -> void:
	var size := fetch16()
	var level := fetch8() & 0x1F
	push(r[5])
	var frame: int = r[4]
	if level > 0:
		var bp: int = r[5]
		for _i in range(level - 1):
			bp = (bp - 2) & 0xFFFF
			push(rw((sr[SS] << 4) + bp))
		push(frame)
	r[5] = frame
	r[4] = (r[4] - size) & 0xFFFF


func op_retf(op: int) -> void:
	var n := fetch16() if op == 0xCA else 0
	ip = pop()
	sr[1] = pop()
	r[4] = (r[4] + n) & 0xFFFF


func op_aam(_op: int) -> void:
	var base := fetch8()
	var al: int = r[0] & 0xFF
	if base == 0:
		interrupt(0)
		return
	var ah := al / base
	al = al % base
	r[0] = (ah << 8) | al
	szp8(al)


func op_aad(_op: int) -> void:
	var base := fetch8()
	var al: int = ((r[0] >> 8) * base + (r[0] & 0xFF)) & 0xFF
	r[0] = al
	szp8(al)


func op_esc(op: int) -> void:
	# No 8087: decode and ignore the operand (FNSTSW/FNSTCW leave memory
	# untouched, so "is a coprocessor present" probes answer "no").
	modrm()
	machine.on_unknown("fpu", op, op_cs, op_ip)


func op_loop(op: int) -> void:
	var d := fetchs8()
	var cx: int = (r[1] - 1) & 0xFFFF
	r[1] = cx
	var t := false
	if op == 0xE2:
		t = cx != 0
	elif op == 0xE1:
		t = cx != 0 and zf != 0
	else:
		t = cx != 0 and zf == 0
	if t:
		ip = (ip + d) & 0xFFFF


func op_in(op: int) -> void:
	var port: int = fetch8() if op <= 0xE5 else r[2]
	if op & 1:
		r[0] = int(machine.port_in(port, 1)) & 0xFFFF
	else:
		r[0] = (r[0] & 0xFF00) | (int(machine.port_in(port, 0)) & 0xFF)


func op_out(op: int) -> void:
	var port: int = fetch8() if op <= 0xE7 else r[2]
	if op & 1:
		machine.port_out(port, r[0], 1)
	else:
		machine.port_out(port, r[0] & 0xFF, 0)


func op_rep(op: int) -> void:
	rep = op
	var op2 := fetch8()
	while op2 == 0x26 or op2 == 0x2E or op2 == 0x36 or op2 == 0x3E or op2 == 0xF0:
		if op2 != 0xF0:
			ovr = (op2 >> 3) & 3
		op2 = fetch8()
	exec_op(op2)


func op_grp3(op: int) -> void:
	var sub := modrm()
	if op == 0xF6:
		var v := get_rm8()
		if sub == 0 or sub == 1:
			alu8(4, v, fetch8())
		elif sub == 2:
			set_rm8(v ^ 0xFF)
		elif sub == 3:
			set_rm8(alu8(5, 0, v))
		elif sub == 4:
			var res: int = (r[0] & 0xFF) * v
			r[0] = res
			cf = 1 if res & 0xFF00 else 0
			of = cf
			szp8(res & 0xFF)
		elif sub == 5:
			var a: int = r[0] & 0xFF
			a = a - 256 if a & 0x80 else a
			var b := v - 256 if v & 0x80 else v
			var res := a * b
			r[0] = res & 0xFFFF
			cf = 0 if (res >= -128 and res <= 127) else 1
			of = cf
			szp8(res & 0xFF)
		elif sub == 6:
			if v == 0:
				interrupt(0)
				return
			var ax: int = r[0]
			var q := ax / v
			if q > 0xFF:
				interrupt(0)
				return
			r[0] = ((ax % v) << 8) | q
		else:
			if v == 0:
				interrupt(0)
				return
			var ax: int = r[0]
			var a := ax - 0x10000 if ax & 0x8000 else ax
			var b := v - 256 if v & 0x80 else v
			var q := absi(a) / absi(b)
			if (a < 0) != (b < 0):
				q = -q
			var rem := a - q * b
			if q > 127 or q < -128:
				interrupt(0)
				return
			r[0] = ((rem & 0xFF) << 8) | (q & 0xFF)
	else:
		var v := get_rm16()
		if sub == 0 or sub == 1:
			alu16(4, v, fetch16())
		elif sub == 2:
			set_rm16(v ^ 0xFFFF)
		elif sub == 3:
			set_rm16(alu16(5, 0, v))
		elif sub == 4:
			var res: int = r[0] * v
			r[0] = res & 0xFFFF
			r[2] = res >> 16
			cf = 1 if r[2] else 0
			of = cf
			szp16(res & 0xFFFF)
		elif sub == 5:
			var a: int = r[0] - 0x10000 if r[0] & 0x8000 else r[0]
			var b := v - 0x10000 if v & 0x8000 else v
			var res := a * b
			r[0] = res & 0xFFFF
			r[2] = (res >> 16) & 0xFFFF
			cf = 0 if (res >= -0x8000 and res <= 0x7FFF) else 1
			of = cf
			szp16(res & 0xFFFF)
		elif sub == 6:
			if v == 0:
				interrupt(0)
				return
			var num: int = (r[2] << 16) | r[0]
			var q := num / v
			if q > 0xFFFF:
				interrupt(0)
				return
			r[0] = q
			r[2] = num % v
		else:
			if v == 0:
				interrupt(0)
				return
			var num: int = (r[2] << 16) | r[0]
			if num & 0x80000000:
				num -= 0x100000000
			var b := v - 0x10000 if v & 0x8000 else v
			var q := absi(num) / absi(b)
			if (num < 0) != (b < 0):
				q = -q
			var rem := num - q * b
			if q > 0x7FFF or q < -0x8000:
				interrupt(0)
				return
			r[0] = q & 0xFFFF
			r[2] = rem & 0xFFFF


func op_grp4(op: int) -> void:
	var sub := modrm()
	var v := get_rm8()
	if sub == 0:
		set_rm8(inc8(v))
	elif sub == 1:
		set_rm8(dec8(v))
	else:
		op_invalid(op)


func op_grp5(op: int) -> void:
	var sub := modrm()
	if sub == 0:
		set_rm16(inc16(get_rm16()))
	elif sub == 1:
		set_rm16(dec16(get_rm16()))
	elif sub == 2:
		var t := get_rm16()
		push(ip)
		ip = t
	elif sub == 3:
		var off := get_rm16()
		var seg := ea_word2()
		push(sr[1])
		push(ip)
		ip = off
		sr[1] = seg
	elif sub == 4:
		ip = get_rm16()
	elif sub == 5:
		var off := get_rm16()
		var seg := ea_word2()
		ip = off
		sr[1] = seg
	elif sub == 6:
		var v := get_rm16()
		push(v)
	else:
		op_invalid(op)
