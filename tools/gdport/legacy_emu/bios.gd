## PC BIOS services (HLE): INT 05 08 09 10 11 12 13 14 15 16 17 18 19 1A 1B 1C.
## One-to-one port of tools/refemu/bios.py.  All services operate on emulated
## memory (BIOS data area and video buffer).
class_name BIOS
extends RefCounted

const BDA_EQUIP := 0x410
const BDA_KBFLAGS := 0x417
const BDA_KBHEAD := 0x41A
const BDA_KBTAIL := 0x41C
const BDA_MODE := 0x449
const BDA_COLS := 0x44A
const BDA_PAGESIZE := 0x44C
const BDA_PAGEOFF := 0x44E
const BDA_CURPOS := 0x450
const BDA_CURSHAPE := 0x460
const BDA_PAGE := 0x462
const BDA_CRTC := 0x463
const BDA_MODEREG := 0x465
const BDA_PALETTE := 0x466
const BDA_TICKS := 0x46C
const BDA_MIDNIGHT := 0x470
const TICKS_PER_DAY := 0x1800B0
## 1989-08-08 00:00:00 (the deterministic default start date, same as Python).
const DEFAULT_EPOCH := 618537600

## RGB 0xRRGGBB of the 16 CGA colours.
const CGA_PALETTE := [
	0x000000, 0x0000AA, 0x00AA00, 0x00AAAA, 0xAA0000, 0xAA00AA, 0xAA5500, 0xAAAAAA,
	0x555555, 0x5555FF, 0x55FF55, 0x55FFFF, 0xFF5555, 0xFF55FF, 0xFFFF55, 0xFFFFFF]

## mode -> [cols, rows, graphics bpp (0 = text), page size, mode register]
const MODE_INFO := {
	0: [40, 25, 0, 0x0800, 0x2C], 1: [40, 25, 0, 0x0800, 0x28],
	2: [80, 25, 0, 0x1000, 0x2D], 3: [80, 25, 0, 0x1000, 0x29],
	4: [40, 25, 2, 0x4000, 0x2A], 5: [40, 25, 2, 0x4000, 0x2E],
	6: [80, 25, 1, 0x4000, 0x1E], 7: [80, 25, 0, 0x1000, 0x29],
}

var m: Machine
var cpu: CPU
var font8: PackedByteArray
var printer_path: String = ""
var printer: FileAccess = null
var start_epoch: int = DEFAULT_EPOCH
var start_secs: int = 12 * 3600
var days: int = 0
var poll_ip: int = -1
var poll_count: int = 0
var poll_icount: int = 0
## scancode -> [normal, shift, ctrl, alt] BIOS key words (-1 = no key)
var keymap: Dictionary = {}


func _init(p_machine: Machine, p_font8: PackedByteArray, p_printer_path: String = "",
		p_epoch: int = DEFAULT_EPOCH, p_start_secs: int = 12 * 3600) -> void:
	m = p_machine
	cpu = m.cpu
	font8 = p_font8
	printer_path = p_printer_path
	start_epoch = p_epoch
	start_secs = p_start_secs
	_build_keymap()
	m.handlers[0x05] = int05
	m.handlers[0x08] = int08
	m.handlers[0x09] = int09
	m.handlers[0x10] = int10
	m.handlers[0x11] = int11
	m.handlers[0x12] = int12
	m.handlers[0x13] = int13
	m.handlers[0x14] = int14
	m.handlers[0x15] = int15
	m.handlers[0x16] = int16
	m.handlers[0x17] = int17
	m.handlers[0x18] = int19
	m.handlers[0x19] = int19
	m.handlers[0x1A] = int1a
	m.handlers[0x1B] = nop
	m.handlers[0x1C] = nop
	m.load_font(font8)
	m.ww(BDA_TICKS, 0)
	set_ticks(int(start_secs * 18.2065))
	set_mode(3)


func nop() -> int:
	return Machine.DONE


func _put(sc: int, n: int, s: int, c: int, a: int) -> void:
	keymap[sc] = [n, s, c, a]


func _build_keymap() -> void:
	_put(0x01, 0x011B, 0x011B, 0x011B, 0x0100)
	var digits := "1234567890-="
	var shifted := "!@#$%^&*()_+"
	for i in range(digits.length()):
		var sc := 0x02 + i
		var ch := digits[i]
		var ctrl := -1
		if ch == "2":
			ctrl = 0x0300
		elif ch == "6":
			ctrl = 0x071E
		elif ch == "-":
			ctrl = 0x0C1F
		var alt := (0x78 + i) << 8
		_put(sc, (sc << 8) | ch.unicode_at(0), (sc << 8) | shifted.unicode_at(i), ctrl, alt)
	_put(0x0E, 0x0E08, 0x0E08, 0x0E7F, 0x0E00)
	_put(0x0F, 0x0F09, 0x0F00, 0x9400, 0xA500)
	var rows := [[0x10, "qwertyuiop[]"], [0x1E, "asdfghjkl;'`"], [0x2B, "\\zxcvbnm,./"]]
	var shifted_map := {"[": "{", "]": "}", ";": ":", "'": "\"", "`": "~", "\\": "|",
		",": "<", ".": ">", "/": "?"}
	var ctrl_map := {"[": 0x1A1B, "]": 0x1B1D, "\\": 0x2B1C}
	for row in rows:
		var base: int = row[0]
		var letters: String = row[1]
		for i in range(letters.length()):
			var sc := base + i
			var ch := letters[i]
			var code := ch.unicode_at(0)
			if code >= 0x61 and code <= 0x7A:
				_put(sc, (sc << 8) | code, (sc << 8) | (code - 0x20), (sc << 8) | (code - 0x60), sc << 8)
			else:
				var sh: String = shifted_map[ch]
				_put(sc, (sc << 8) | code, (sc << 8) | sh.unicode_at(0), int(ctrl_map.get(ch, -1)), -1)
	_put(0x1C, 0x1C0D, 0x1C0D, 0x1C0A, 0xA600)
	_put(0x39, 0x3920, 0x3920, 0x3920, 0x3920)
	_put(0x37, 0x372A, 0x372A, -1, -1)
	_put(0x4A, 0x4A2D, 0x4A2D, -1, -1)
	_put(0x4E, 0x4E2B, 0x4E2B, -1, -1)
	for i in range(10):
		var sc := 0x3B + i
		_put(sc, sc << 8, (0x54 + i) << 8, (0x5E + i) << 8, (0x68 + i) << 8)
	_put(0x57, 0x8500, 0x8700, 0x8900, 0x8B00)
	_put(0x58, 0x8600, 0x8800, 0x8A00, 0x8C00)
	var pad := {0x47: [0x4700, 0x4737, 0x7700], 0x48: [0x4800, 0x4838, 0x8D00],
		0x49: [0x4900, 0x4939, 0x8400], 0x4B: [0x4B00, 0x4B34, 0x7300],
		0x4C: [0x4C00, 0x4C35, 0x8F00], 0x4D: [0x4D00, 0x4D36, 0x7400],
		0x4F: [0x4F00, 0x4F31, 0x7500], 0x50: [0x5000, 0x5032, 0x9100],
		0x51: [0x5100, 0x5133, 0x7600], 0x52: [0x5200, 0x5230, 0x9200],
		0x53: [0x5300, 0x532E, 0x9300]}
	for sc in pad:
		var e: Array = pad[sc]
		_put(sc, e[0], e[1], e[2], -1)


# --------------------------------------------------------------- helpers
func set_ticks(v: int) -> void:
	m.ww(BDA_TICKS, v & 0xFFFF)
	m.ww(BDA_TICKS + 2, (v >> 16) & 0xFFFF)


func get_ticks() -> int:
	return m.rw(BDA_TICKS) | (m.rw(BDA_TICKS + 2) << 16)


## Emulated wall-clock time as a datetime dictionary (derived from the tick count).
func now() -> Dictionary:
	var secs := int(get_ticks() / 18.2065)
	var unix := start_epoch + secs + 86400 * days
	return Time.get_datetime_dict_from_unix_time(unix)


## 1/100 s part of the emulated time of day (for INT 21h AH=2Ch).
func hundredths() -> int:
	var secs_f := get_ticks() / 18.2065
	return int((secs_f - int(secs_f)) * 100)


func int05() -> int:
	return Machine.DONE


func int08() -> int:
	var t := get_ticks() + 1
	if t >= TICKS_PER_DAY:
		t = 0
		m.wb(BDA_MIDNIGHT, 1)
		days += 1
	set_ticks(t)
	var mc := m.rb(0x440)
	if mc:
		m.wb(0x440, mc - 1)
	return Machine.DONE


func int09() -> int:
	var sc := m.port_in(0x60, 0)
	var flags := m.rb(BDA_KBFLAGS)
	var make := not (sc & 0x80)
	var code := sc & 0x7F
	var shift_bits := {0x36: 0x01, 0x2A: 0x02, 0x1D: 0x04, 0x38: 0x08}
	var toggles := {0x46: 0x10, 0x45: 0x20, 0x3A: 0x40, 0x52: 0x80}
	if shift_bits.has(code):
		var bit: int = shift_bits[code]
		flags = (flags | bit) if make else (flags & ~bit)
		m.wb(BDA_KBFLAGS, flags)
	elif make:
		if toggles.has(code) and code != 0x52:
			m.wb(BDA_KBFLAGS, flags ^ int(toggles[code]))
		else:
			if toggles.has(code):
				m.wb(BDA_KBFLAGS, flags ^ int(toggles[code]))
			if keymap.has(code):
				var ent: Array = keymap[code]
				var kw: int = -1
				if flags & 0x08:
					kw = ent[3]
				elif flags & 0x04:
					kw = ent[2]
				elif flags & 0x03:
					kw = ent[1]
				else:
					kw = ent[0]
				if kw >= 0 and (flags & 0x40) and not (flags & 0x0C):
					var lo := kw & 0xFF
					if (lo >= 0x41 and lo <= 0x5A) or (lo >= 0x61 and lo <= 0x7A):
						kw ^= 0x20
				if kw >= 0:
					kbd_put(kw)
	m.pic.eoi()
	return Machine.DONE


func kbd_put(kw: int) -> bool:
	var head := m.rw(BDA_KBHEAD)
	var tail := m.rw(BDA_KBTAIL)
	var nt := tail + 2
	if nt >= 0x3E:
		nt = 0x1E
	if nt == head:
		return false
	m.ww(0x400 + tail, kw)
	m.ww(BDA_KBTAIL, nt)
	return true


## Returns the next key word without removing it, or -1.
func kbd_peek() -> int:
	var head := m.rw(BDA_KBHEAD)
	if head == m.rw(BDA_KBTAIL):
		return -1
	return m.rw(0x400 + head)


## Removes and returns the next key word, or -1.
func kbd_get() -> int:
	var head := m.rw(BDA_KBHEAD)
	if head == m.rw(BDA_KBTAIL):
		return -1
	var kw := m.rw(0x400 + head)
	head += 2
	if head >= 0x3E:
		head = 0x1E
	m.ww(BDA_KBHEAD, head)
	return kw


func int16() -> int:
	var ah: int = cpu.r[0] >> 8
	if ah == 0x00 or ah == 0x10:
		var kw := kbd_get()
		if kw < 0:
			return Machine.BLOCK
		if ah == 0 and (kw & 0xFF) == 0xE0:
			kw &= 0xFF00
		cpu.r[0] = kw
	elif ah == 0x01 or ah == 0x11:
		var kw := kbd_peek()
		if kw < 0:
			cpu.zf = 1
			idle_poll()
		else:
			cpu.zf = 0
			cpu.r[0] = kw
	elif ah == 0x02 or ah == 0x12:
		cpu.r[0] = (cpu.r[0] & 0xFF00) | m.rb(BDA_KBFLAGS)
	elif ah == 0x05:
		var ok := kbd_put(cpu.r[1])
		cpu.r[0] = (cpu.r[0] & 0xFF00) | (0 if ok else 1)
	elif ah == 0x03:
		pass
	else:
		m.once("int", 0x16, ah)
	return Machine.DONE


## Heuristic: a tight loop polling for a key that is not there -> skip to next tick.
func idle_poll() -> void:
	var ss_base: int = cpu.sr[2] << 4
	var here := m.rw(ss_base + cpu.r[4]) | (m.rw(ss_base + ((cpu.r[4] + 2) & 0xFFFF)) << 16)
	if here == poll_ip and cpu.icount - poll_icount < 2000:
		poll_count += 1
	else:
		poll_count = 0
	poll_ip = here
	poll_icount = cpu.icount
	if poll_count > 8:
		poll_count = 0
		m.skip_to_tick = true
		cpu.halted = true


func int11() -> int:
	cpu.r[0] = m.rw(BDA_EQUIP)
	return Machine.DONE


func int12() -> int:
	cpu.r[0] = m.rw(0x413)
	return Machine.DONE


func int13() -> int:
	var ah: int = cpu.r[0] >> 8
	if ah == 0x00:
		cpu.r[0] &= 0x00FF
		cpu.cf = 0
	elif ah == 0x08:
		cpu.r[0] = 0x0100
		cpu.cf = 1
	else:
		m.once("int", 0x13, ah)
		cpu.r[0] = (cpu.r[0] & 0x00FF) | 0x8000
		cpu.cf = 1
	return Machine.DONE


func int14() -> int:
	cpu.r[0] = 0x8000 | (cpu.r[0] & 0xFF)
	return Machine.DONE


func int15() -> int:
	var ah: int = cpu.r[0] >> 8
	cpu.r[0] = (cpu.r[0] & 0x00FF) | 0x8600
	cpu.cf = 1
	if not ah in [0x88, 0xC0, 0x41, 0x4F, 0x90, 0x91, 0x86]:
		m.once("int", 0x15, ah)
	return Machine.DONE


func int17() -> int:
	var ah: int = cpu.r[0] >> 8
	if ah == 0x00:
		if printer_path != "":
			if printer == null:
				if FileAccess.file_exists(printer_path):
					printer = FileAccess.open(printer_path, FileAccess.READ_WRITE)
					if printer:
						printer.seek_end()
				else:
					printer = FileAccess.open(printer_path, FileAccess.WRITE)
			if printer:
				printer.store_8(cpu.r[0] & 0xFF)
				printer.flush()
			cpu.r[0] = (cpu.r[0] & 0xFF) | 0x9000
		else:
			cpu.r[0] = (cpu.r[0] & 0xFF) | 0x2900
	elif ah == 0x01 or ah == 0x02:
		var st := 0x90 if printer_path != "" else 0x29
		cpu.r[0] = (cpu.r[0] & 0xFF) | (st << 8)
	return Machine.DONE


func int19() -> int:
	m.log_msg("INT 19h/18h (reboot / ROM BASIC) -> stop")
	m.exited = true
	cpu.halted = true
	return Machine.DONE


func bcd(v: int) -> int:
	return ((v / 10) << 4) | (v % 10)


func int1a() -> int:
	var ah: int = cpu.r[0] >> 8
	if ah == 0x00:
		var t := get_ticks()
		cpu.r[1] = (t >> 16) & 0xFFFF
		cpu.r[2] = t & 0xFFFF
		cpu.r[0] = (cpu.r[0] & 0xFF00) | m.rb(BDA_MIDNIGHT)
		m.wb(BDA_MIDNIGHT, 0)
	elif ah == 0x01:
		set_ticks((cpu.r[1] << 16) | cpu.r[2])
	elif ah == 0x02:
		var n := now()
		cpu.r[1] = (bcd(n["hour"]) << 8) | bcd(n["minute"])
		cpu.r[2] = bcd(n["second"]) << 8
		cpu.cf = 0
	elif ah == 0x04:
		var n := now()
		cpu.r[1] = (bcd(int(n["year"]) / 100) << 8) | bcd(int(n["year"]) % 100)
		cpu.r[2] = (bcd(n["month"]) << 8) | bcd(n["day"])
		cpu.cf = 0
	else:
		m.once("int", 0x1A, ah)
		cpu.cf = 1
	return Machine.DONE


# ================================================================== video
func mode() -> int:
	return m.rb(BDA_MODE)


func cols() -> int:
	var c := m.rw(BDA_COLS)
	return c if c else 80


func vbase() -> int:
	return 0xB0000 if mode() == 7 else 0xB8000


func set_mode(al: int) -> void:
	var clear := not (al & 0x80)
	var md := al & 0x7F
	if not MODE_INFO.has(md):
		m.once("video-mode", md)
		md = 3
	var info: Array = MODE_INFO[md]
	var bpp: int = info[2]
	m.wb(BDA_MODE, md)
	m.ww(BDA_COLS, info[0])
	m.ww(BDA_PAGESIZE, info[3])
	m.ww(BDA_PAGEOFF, 0)
	for p in range(8):
		m.ww(BDA_CURPOS + p * 2, 0)
	m.ww(BDA_CURSHAPE, 0x0607 if md != 7 else 0x0B0C)
	m.wb(BDA_PAGE, 0)
	m.ww(BDA_CRTC, 0x3B4 if md == 7 else 0x3D4)
	m.wb(BDA_MODEREG, info[4])
	var pal := 0x30
	if bpp != 0 and md == 6:
		pal = 0x3F
	m.wb(BDA_PALETTE, pal)
	m.cga_mode_reg = info[4]
	m.cga_color_reg = pal
	if clear:
		var base := 0xB0000 if md == 7 else 0xB8000
		for i in range(0x2000):
			if bpp:
				cpu.mem[base + 2 * i] = 0
				cpu.mem[base + 2 * i + 1] = 0
			else:
				cpu.mem[base + 2 * i] = 0x20
				cpu.mem[base + 2 * i + 1] = 0x07


## Returns [row, col].
func cursor(page: int = -1) -> Array:
	if page < 0:
		page = m.rb(BDA_PAGE)
	var v := m.rw(BDA_CURPOS + (page & 7) * 2)
	return [v >> 8, v & 0xFF]


func set_cursor(row: int, col: int, page: int = -1) -> void:
	if page < 0:
		page = m.rb(BDA_PAGE)
	m.ww(BDA_CURPOS + (page & 7) * 2, (row << 8) | col)


func text_addr(page: int, row: int, col: int) -> int:
	return vbase() + page * m.rw(BDA_PAGESIZE) + (row * cols() + col) * 2


func is_graphics() -> bool:
	if not MODE_INFO.has(mode()):
		return false
	return int(MODE_INFO[mode()][2]) != 0


func put_pixel(x: int, y: int, color: int) -> void:
	var md := mode()
	if md == 4 or md == 5:
		if x < 0 or x >= 320 or y < 0 or y >= 200:
			return
		var a := 0xB8000 + (y & 1) * 0x2000 + (y >> 1) * 80 + (x >> 2)
		var sh := (3 - (x & 3)) * 2
		var c := color & 3
		if color & 0x80:
			cpu.mem[a] = cpu.mem[a] ^ (c << sh)
		else:
			cpu.mem[a] = (cpu.mem[a] & ~(3 << sh) & 0xFF) | (c << sh)
	elif md == 6:
		if x < 0 or x >= 640 or y < 0 or y >= 200:
			return
		var a := 0xB8000 + (y & 1) * 0x2000 + (y >> 1) * 80 + (x >> 3)
		var sh := 7 - (x & 7)
		var c := color & 1
		if color & 0x80:
			cpu.mem[a] = cpu.mem[a] ^ (c << sh)
		else:
			cpu.mem[a] = (cpu.mem[a] & ~(1 << sh) & 0xFF) | (c << sh)


func get_pixel(x: int, y: int) -> int:
	var md := mode()
	if md == 4 or md == 5:
		var a := 0xB8000 + (y & 1) * 0x2000 + (y >> 1) * 80 + (x >> 2)
		return (cpu.mem[a] >> ((3 - (x & 3)) * 2)) & 3
	if md == 6:
		var a := 0xB8000 + (y & 1) * 0x2000 + (y >> 1) * 80 + (x >> 3)
		return (cpu.mem[a] >> (7 - (x & 7))) & 1
	return 0


func gfx_char(row: int, col: int, ch: int, color: int) -> void:
	if mode() == 6:
		for y in range(8):
			var py := row * 8 + y
			var a := 0xB8000 + (py & 1) * 0x2000 + (py >> 1) * 80 + col
			var bits: int = font8[ch * 8 + y]
			if color & 0x80:
				cpu.mem[a] = cpu.mem[a] ^ bits
			else:
				cpu.mem[a] = bits if color & 1 else 0
	else:
		var c := color & 3
		for y in range(8):
			var py := row * 8 + y
			var a := 0xB8000 + (py & 1) * 0x2000 + (py >> 1) * 80 + col * 2
			var bits: int = font8[ch * 8 + y]
			var w := 0
			for x in range(8):
				if bits & (0x80 >> x):
					w |= c << ((7 - x) * 2)
			if color & 0x80:
				cpu.mem[a] = cpu.mem[a] ^ (w >> 8)
				cpu.mem[a + 1] = cpu.mem[a + 1] ^ (w & 0xFF)
			else:
				cpu.mem[a] = w >> 8
				cpu.mem[a + 1] = w & 0xFF


func write_char(ch: int, attr: int, count: int, use_attr: bool, page: int = -1) -> void:
	if page < 0:
		page = m.rb(BDA_PAGE)
	var rc := cursor(page)
	var row: int = rc[0]
	var col: int = rc[1]
	var ncols := cols()
	if is_graphics():
		for _i in range(count):
			if col >= ncols:
				break
			gfx_char(row, col, ch, attr)
			col += 1
		return
	var a := text_addr(page, row, col)
	var end := text_addr(page, 25, 0)
	for _i in range(count):
		if a >= end:
			break
		cpu.mem[a] = ch
		if use_attr:
			cpu.mem[a + 1] = attr
		a += 2


func _gfx_line_addr(py: int) -> int:
	return 0xB8000 + (py & 1) * 0x2000 + (py >> 1) * 80


func scroll(up: bool, lines: int, attr: int, top: int, left: int, bottom: int, right: int) -> void:
	var ncols := cols()
	right = mini(right, ncols - 1)
	bottom = mini(bottom, 24)
	if top > bottom or left > right:
		return
	var height := bottom - top + 1
	if lines == 0 or lines > height:
		lines = height
	var rows: Array = []
	if up:
		for rr in range(top, bottom + 1):
			rows.append(rr)
	else:
		for rr in range(bottom, top - 1, -1):
			rows.append(rr)
	if is_graphics():
		var bpb := 2 if mode() == 4 or mode() == 5 else 1
		var fill := 0
		if mode() == 4 or mode() == 5:
			var c := attr & 3
			fill = c | (c << 2) | (c << 4) | (c << 6)
		elif attr & 1:
			fill = 0xFF
		var x0 := left * bpb
		var x1 := (right + 1) * bpb
		for rr in rows:
			var src: int = rr + lines if up else rr - lines
			for y in range(8):
				var d := _gfx_line_addr(rr * 8 + y)
				if src >= top and src <= bottom:
					var s := _gfx_line_addr(src * 8 + y)
					for x in range(x0, x1):
						cpu.mem[d + x] = cpu.mem[s + x]
				else:
					for x in range(x0, x1):
						cpu.mem[d + x] = fill
		return
	var page := m.rb(BDA_PAGE)
	var width := (right - left + 1) * 2
	for rr in rows:
		var d := text_addr(page, rr, left)
		var src: int = rr + lines if up else rr - lines
		if src >= top and src <= bottom:
			var s := text_addr(page, src, left)
			for i in range(width):
				cpu.mem[d + i] = cpu.mem[s + i]
		else:
			for i in range(0, width, 2):
				cpu.mem[d + i] = 0x20
				cpu.mem[d + i + 1] = attr


## Teletype output (INT 10h AH=0Eh semantics), used by DOS console output.
func tty(ch: int, color: int = -1) -> void:
	var page := m.rb(BDA_PAGE)
	var rc := cursor(page)
	var row: int = rc[0]
	var col: int = rc[1]
	var ncols := cols()
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
		if is_graphics():
			gfx_char(row, col, ch, color if color >= 0 else 3)
		else:
			cpu.mem[text_addr(page, row, col)] = ch
		col += 1
		if col >= ncols:
			col = 0
			row += 1
	if row >= 25:
		if is_graphics():
			scroll(true, 1, 0, 0, 0, 24, ncols - 1)
		else:
			var attr: int = cpu.mem[text_addr(page, 24, ncols - 1) + 1]
			scroll(true, 1, attr, 0, 0, 24, ncols - 1)
		row = 24
	set_cursor(row, col, page)


func int10() -> int:
	var ax: int = cpu.r[0]
	var ah := ax >> 8
	var al := ax & 0xFF
	var bh: int = cpu.r[3] >> 8
	var bl: int = cpu.r[3] & 0xFF
	if ah == 0x00:
		set_mode(al)
	elif ah == 0x01:
		m.ww(BDA_CURSHAPE, cpu.r[1])
	elif ah == 0x02:
		set_cursor(cpu.r[2] >> 8, cpu.r[2] & 0xFF, bh)
	elif ah == 0x03:
		var rc := cursor(bh)
		cpu.r[2] = (int(rc[0]) << 8) | int(rc[1])
		cpu.r[1] = m.rw(BDA_CURSHAPE)
	elif ah == 0x04:
		cpu.r[0] = ax & 0x00FF
	elif ah == 0x05:
		m.wb(BDA_PAGE, al & 7)
		m.ww(BDA_PAGEOFF, (al & 7) * m.rw(BDA_PAGESIZE))
	elif ah == 0x06 or ah == 0x07:
		var cx: int = cpu.r[1]
		var dx: int = cpu.r[2]
		scroll(ah == 6, al, bh, cx >> 8, cx & 0xFF, dx >> 8, dx & 0xFF)
	elif ah == 0x08:
		if is_graphics():
			cpu.r[0] = (ax & 0xFF00) | gfx_read_char(bh)
		else:
			var rc := cursor(bh)
			var a := text_addr(bh, rc[0], rc[1])
			cpu.r[0] = cpu.mem[a] | (cpu.mem[a + 1] << 8)
	elif ah == 0x09:
		write_char(al, bl, cpu.r[1], true, bh)
	elif ah == 0x0A:
		write_char(al, bl, cpu.r[1], false, bh)
	elif ah == 0x0B:
		var pal := 0
		if bh == 0:
			pal = (m.rb(BDA_PALETTE) & 0xE0) | (bl & 0x1F)
		else:
			pal = (m.rb(BDA_PALETTE) & 0xDF) | (0x20 if bl & 1 else 0)
		m.wb(BDA_PALETTE, pal)
		m.cga_color_reg = pal
	elif ah == 0x0C:
		put_pixel(cpu.r[1], cpu.r[2], al)
	elif ah == 0x0D:
		cpu.r[0] = (ax & 0xFF00) | get_pixel(cpu.r[1], cpu.r[2])
	elif ah == 0x0E:
		tty(al, bl)
	elif ah == 0x0F:
		cpu.r[0] = (cols() << 8) | mode()
		cpu.r[3] = (m.rb(BDA_PAGE) << 8) | bl
	elif ah == 0x10 or ah == 0x11:
		pass
	elif ah == 0x12:
		pass
	elif ah == 0x13:
		write_string()
	elif ah == 0x1A:
		pass
	elif ah == 0xFE or ah == 0xFF:
		pass
	else:
		m.once("int", 0x10, ah)
	return Machine.DONE


func gfx_read_char(page: int) -> int:
	var rc := cursor(page)
	var row: int = rc[0]
	var col: int = rc[1]
	var pat := PackedByteArray()
	for y in range(8):
		var bits := 0
		for x in range(8):
			if get_pixel(col * 8 + x, row * 8 + y):
				bits |= 0x80 >> x
		pat.append(bits)
	for c in range(256):
		if font8.slice(c * 8, c * 8 + 8) == pat:
			return c
	return 0


func write_string() -> void:
	var al: int = cpu.r[0] & 0xFF
	var page: int = cpu.r[3] >> 8
	var attr: int = cpu.r[3] & 0xFF
	var old := cursor(page)
	set_cursor(cpu.r[2] >> 8, cpu.r[2] & 0xFF, page)
	var a: int = (cpu.sr[0] << 4) + cpu.r[5]
	for _i in range(cpu.r[1]):
		var ch := m.rb(a)
		a += 1
		if al & 2:
			attr = m.rb(a)
			a += 1
		if ch == 7 or ch == 8 or ch == 10 or ch == 13:
			tty(ch)
		else:
			write_char(ch, attr, 1, true, page)
			var rc := cursor(page)
			var rr: int = rc[0]
			var cc: int = rc[1] + 1
			if cc >= cols():
				cc = 0
				rr += 1
				if rr >= 25:
					scroll(true, 1, 7, 0, 0, 24, cols() - 1)
					rr = 24
			set_cursor(rr, cc, page)
	if not (al & 1):
		set_cursor(old[0], old[1], page)
