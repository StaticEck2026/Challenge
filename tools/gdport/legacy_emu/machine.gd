## PC/XT machine model: memory map, ROM stubs, IVT, PIC, PIT, ports, keyboard.
## One-to-one port of tools/refemu/machine.py.
##
## The BIOS ROM is a set of tiny stubs at F000:E000 + 4*n, each `F1 nn CF`
## (emulator trap, vector number, IRET).  The trap calls hle(nn), which
## dispatches to the GDScript BIOS (bios.gd) / DOS (dos.gd) services.
class_name Machine
extends RefCounted

const ROM_SEG := 0xF000
const STUB_BASE := 0xE000
const INT08_OFF := 0xE400
const FONT_OFF := 0xFA6E
const FONT_HI_OFF := 0xF600
## Service return value meaning "retry later" (waiting for input).
const BLOCK := 1
const DONE := 0


## Minimal 8259A: IRR/ISR/IMR, fixed priority, non-specific EOI.
class PIC:
	var irr: int = 0
	var isr: int = 0
	var imr: int = 0
	var cpu: CPU = null
	var icw_step: int = 0

	func _update() -> void:
		var pend := irr & ~imr & 0xFF
		var ok := false
		if pend:
			for n in range(8):
				var bit := 1 << n
				if isr & bit:
					break
				if pend & bit:
					ok = true
					break
		cpu.irq_flag = ok

	func raise_irq(n: int) -> void:
		irr |= 1 << n
		_update()

	func ack() -> int:
		var pend := irr & ~imr & 0xFF
		for n in range(8):
			var bit := 1 << n
			if isr & bit:
				break
			if pend & bit:
				irr &= ~bit
				isr |= bit
				_update()
				return 8 + n
		_update()
		return -1

	func eoi() -> void:
		for n in range(8):
			if isr & (1 << n):
				isr &= ~(1 << n)
				break
		_update()

	func write(port: int, v: int) -> void:
		if port == 0x20:
			if v & 0x10:
				icw_step = 1
				imr = 0
			elif (v & 0x18) == 0x00 and (v & 0x20):
				if v & 0x40:
					isr &= ~(1 << (v & 7))
					_update()
				else:
					eoi()
		else:
			if icw_step:
				icw_step = icw_step + 1 if icw_step < 3 else 0
			else:
				imr = v & 0xFF
				_update()

	func read(port: int) -> int:
		if port == 0x21:
			return imr
		return irr


var cpu: CPU
var pic: PIC
var handlers: Dictionary = {}
var seen: Dictionary = {}
var trace_ints: bool = false
var int_counts: Dictionary = {}
var port_counts: Dictionary = {}
var exited: bool = false
var exit_code: int = 0
var ipt: int = 20000
var pit_div: PackedInt32Array = PackedInt32Array([65536, 65536, 65536])
var pit_mode: PackedInt32Array = PackedInt32Array([3, 3, 3])
var pit_rl: PackedInt32Array = PackedInt32Array([3, 3, 3])
var pit_wlow: Array = [null, null, null]
var pit_latch: Array = [null, null, null]
var tick_len: int = 20000
var next_tick: int = 20000
var last_tick: int = 0
var ticks: int = 0
var speaker: int = 0
var kbd_data: int = 0
var kbd_queue: PackedInt32Array = PackedInt32Array()
var kbd_busy: bool = false
var cga_toggle: int = 0
var cga_mode_reg: int = 0x29
var cga_color_reg: int = 0x30
var crtc_index: int = 0
var crtc: PackedInt32Array = PackedInt32Array()
var idle: bool = false
var skip_to_tick: bool = false
## When true the front-end calls tick() from wall-clock time; run() then never
## generates timer ticks from the instruction count.
var realtime: bool = false
var log_lines: PackedStringArray = PackedStringArray()


func _init(p_ipt: int = 20000) -> void:
	cpu = CPU.new()
	cpu.machine = self
	pic = PIC.new()
	pic.cpu = cpu
	ipt = p_ipt
	tick_len = p_ipt
	next_tick = p_ipt
	crtc.resize(32)
	_init_memory()


# ------------------------------------------------------------------ setup
func _init_memory() -> void:
	var rom := ROM_SEG << 4
	for n in range(256):
		var off := STUB_BASE + n * 4
		var a := rom + off
		cpu.mem[a] = 0xF1
		cpu.mem[a + 1] = n
		cpu.mem[a + 2] = 0xCF
		cpu.mem[a + 3] = 0x90
		ww(n * 4, off)
		ww(n * 4 + 2, ROM_SEG)
	# timer: F1 08 ; INT 1C ; PUSH AX ; MOV AL,20 ; OUT 20,AL ; POP AX ; IRET
	var t08 := [0xF1, 0x08, 0xCD, 0x1C, 0x50, 0xB0, 0x20, 0xE6, 0x20, 0x58, 0xCF, 0x90]
	for i in range(t08.size()):
		cpu.mem[rom + INT08_OFF + i] = t08[i]
	set_vec(0x08, ROM_SEG, INT08_OFF)
	var reset := [0xEA, 0x00, 0xE0, 0x00, 0xF0]
	for i in range(reset.size()):
		cpu.mem[rom + 0xFFF0 + i] = reset[i]
	var date := "01/10/86".to_ascii_buffer()
	for i in range(date.size()):
		cpu.mem[rom + 0xFFF5 + i] = date[i]
	cpu.mem[rom + 0xFFFE] = 0xFE
	set_vec(0x1D, ROM_SEG, 0xF0A4)
	set_vec(0x1E, ROM_SEG, 0xEFC7)
	set_vec(0x1F, ROM_SEG, FONT_HI_OFF)
	var dpt := [0xDF, 0x02, 0x25, 0x02, 0x09, 0x2A, 0xFF, 0x50, 0xF6, 0x19, 0x04]
	for i in range(dpt.size()):
		cpu.mem[rom + 0xEFC7 + i] = dpt[i]
	ww(0x410, 0x4061)
	ww(0x413, 640)
	ww(0x41A, 0x1E)
	ww(0x41C, 0x1E)
	ww(0x480, 0x1E)
	ww(0x482, 0x3E)
	cpu.mem[0x417] = 0x00
	ww(0x408, 0x378)
	ww(0x400, 0x3F8)


func load_font(font8: PackedByteArray) -> void:
	var rom := ROM_SEG << 4
	for i in range(1024):
		cpu.mem[rom + FONT_OFF + i] = font8[i]
		cpu.mem[rom + FONT_HI_OFF + i] = font8[1024 + i]


func set_vec(n: int, seg: int, off: int) -> void:
	ww(n * 4, off)
	ww(n * 4 + 2, seg)


## Returns [segment, offset].
func get_vec(n: int) -> Array:
	return [rw(n * 4 + 2), rw(n * 4)]


func rb(a: int) -> int:
	return cpu.mem[a & 0xFFFFF]


func rw(a: int) -> int:
	return cpu.mem[a & 0xFFFFF] | (cpu.mem[(a + 1) & 0xFFFFF] << 8)


func wb(a: int, v: int) -> void:
	cpu.mem[a & 0xFFFFF] = v & 0xFF


func ww(a: int, v: int) -> void:
	cpu.mem[a & 0xFFFFF] = v & 0xFF
	cpu.mem[(a + 1) & 0xFFFFF] = (v >> 8) & 0xFF


# ---------------------------------------------------------------- logging
func log_msg(s: String) -> void:
	print(s)
	log_lines.append(s)
	if log_lines.size() > 200:
		log_lines.remove_at(0)


func once(kind: String, a, b = null, extra: String = "") -> void:
	var key := "%s/%s/%s" % [kind, str(a), str(b)]
	if seen.has(key):
		return
	seen[key] = true
	var astr: String = ("%02X" % a) if typeof(a) == TYPE_INT else str(a)
	var bstr: String = ("/%02X" % b) if typeof(b) == TYPE_INT else ""
	log_msg("[unimpl] %s %s%s at %04X:%04X %s" % [kind, astr, bstr, cpu.op_cs, cpu.op_ip, extra])


func on_unknown(kind: String, op: int, _cs: int, _ip: int) -> void:
	if kind == "fpu":
		once("fpu-opcode", op)
	else:
		once("opcode", op)


func count_int(n: int, ah: int) -> void:
	var key := "INT %02Xh AH=%02Xh" % [n, ah]
	int_counts[key] = int(int_counts.get(key, 0)) + 1


func is_hw_vector(n: int) -> bool:
	return (n >= 0x08 and n < 0x10) or (n >= 0x70 and n < 0x78)


# -------------------------------------------------------------------- HLE
func hle(n: int) -> void:
	if trace_ints:
		var key := "%02Xh/%02Xh" % [n, cpu.r[0] >> 8]
		int_counts[key] = int(int_counts.get(key, 0)) + 1
	if not handlers.has(n):
		if is_hw_vector(n):
			if n >= 0x0A:
				pic.eoi()
			return
		once("int", n, cpu.r[0] >> 8)
		return
	var h: Callable = handlers[n]
	var res = h.call()
	if typeof(res) == TYPE_INT and res == BLOCK:
		cpu.ip = (cpu.ip - 2) & 0xFFFF
		cpu.iflag = 1
		cpu.halted = true
		idle = true
		return
	if not is_hw_vector(n):
		# copy the arithmetic flags into the FLAGS image IRET will pop
		var a: int = (cpu.sr[2] << 4) + ((cpu.r[4] + 4) & 0xFFFF)
		var f := rw(a)
		f = (f & ~0x08D5) | (cpu.get_flags() & 0x08D5)
		ww(a, f)


# ------------------------------------------------------------------ ports
func count_port(kind: String, port: int) -> void:
	var key := "%s %03Xh" % [kind, port]
	port_counts[key] = int(port_counts.get(key, 0)) + 1


func port_in(port: int, w: int) -> int:
	if w:
		return port_in(port, 0) | (port_in(port + 1, 0) << 8)
	port &= 0xFFFF
	if trace_ints:
		count_port("in", port)
	if port == 0x3DA or port == 0x3BA:
		cga_toggle = (cga_toggle + 1) & 3
		return 0x09 if cga_toggle >= 2 else 0x00
	if port == 0x3D5 or port == 0x3B5:
		return crtc[crtc_index & 31]
	if port == 0x60:
		kbd_busy = false
		return kbd_data
	if port == 0x61:
		return speaker
	if port == 0x64:
		return 0x1C | (1 if kbd_busy else 0)
	if port == 0x20 or port == 0x21:
		return pic.read(port)
	if port >= 0x40 and port <= 0x42:
		return pit_read(port - 0x40)
	if port == 0x62:
		return 0x00
	if port == 0x201:
		return 0xF0
	if port == 0x379 or port == 0x279 or port == 0x3BD:
		return 0x00
	if port == 0x378 or port == 0x3BC or port == 0x278 or port == 0x37A:
		return 0xFF
	if (port >= 0x3F8 and port <= 0x3FF) or (port >= 0x2F8 and port <= 0x2FF):
		return 0xFF
	once("port-in", "%04X" % port)
	return 0xFF


func port_out(port: int, v: int, w: int) -> void:
	if w:
		port_out(port, v & 0xFF, 0)
		port_out(port + 1, (v >> 8) & 0xFF, 0)
		return
	port &= 0xFFFF
	v &= 0xFF
	if trace_ints:
		count_port("out", port)
	if port == 0x20 or port == 0x21:
		pic.write(port, v)
	elif port >= 0x40 and port <= 0x43:
		pit_write(port - 0x40, v)
	elif port == 0x61:
		speaker = v
	elif port == 0x3D4 or port == 0x3B4:
		crtc_index = v
	elif port == 0x3D5 or port == 0x3B5:
		crtc[crtc_index & 31] = v
	elif port == 0x3D8:
		cga_mode_reg = v
	elif port == 0x3D9:
		cga_color_reg = v
	elif port == 0x3B8 or port == 0x3BF:
		pass
	elif port in [0x378, 0x37A, 0x3BC, 0x3BE, 0x278, 0x27A]:
		pass
	elif (port >= 0x3F8 and port <= 0x3FF) or (port >= 0x2F8 and port <= 0x2FF):
		pass
	elif port == 0x201 or port == 0xA0 or port == 0x80:
		pass
	else:
		once("port-out", "%04X" % port)


# -------------------------------------------------------------------- PIT
func pit_write(ch: int, v: int) -> void:
	if ch == 3:
		var c := v >> 6
		if c == 3:
			return
		var rlv := (v >> 4) & 3
		if rlv == 0:
			pit_latch[c] = pit_count(c)
			return
		pit_rl[c] = rlv
		pit_mode[c] = (v >> 1) & 7
		pit_wlow[c] = null
		return
	var rl: int = pit_rl[ch]
	var val := 0
	if rl == 3:
		if pit_wlow[ch] == null:
			pit_wlow[ch] = v
			return
		val = int(pit_wlow[ch]) | (v << 8)
		pit_wlow[ch] = null
	elif rl == 1:
		val = v
	else:
		val = v << 8
	if val == 0:
		val = 65536
	pit_div[ch] = val
	if ch == 0:
		tick_len = maxi(50, ipt * val / 65536)
		next_tick = cpu.icount + tick_len


func pit_count(ch: int) -> int:
	var div: int = pit_div[ch]
	var c := 0
	if ch == 0:
		var elapsed := cpu.icount - last_tick
		c = div - (elapsed * div / maxi(1, tick_len))
	else:
		c = div - (cpu.icount * 4) % div
	return clampi(c, 0, 0xFFFF)


func pit_read(ch: int) -> int:
	if pit_latch[ch] != null:
		var v: int = pit_latch[ch]
		if pit_wlow[ch] == null:
			pit_wlow[ch] = "hi"
			return v & 0xFF
		pit_wlow[ch] = null
		pit_latch[ch] = null
		return v >> 8
	var c := pit_count(ch)
	if pit_rl[ch] == 3:
		if pit_wlow[ch] == null:
			pit_wlow[ch] = "hi"
			return c & 0xFF
		pit_wlow[ch] = null
		return c >> 8
	return c & 0xFF


func speaker_freq() -> int:
	if (speaker & 3) == 3:
		return 1193182 / pit_div[2]
	return 0


# --------------------------------------------------------------- keyboard
func key_scancodes(codes: PackedInt32Array) -> void:
	kbd_queue.append_array(codes)


func bios_kbd_empty() -> bool:
	return rw(0x41A) == rw(0x41C)


## Deliver the next queued scancode through IRQ1 if the controller is free.
func pump_keyboard() -> bool:
	if kbd_busy or kbd_queue.is_empty():
		return false
	var sc: int = kbd_queue[0]
	var modifier := sc == 0x1D or sc == 0x2A or sc == 0x36 or sc == 0x38
	if not (sc & 0x80) and not modifier and not bios_kbd_empty():
		return false
	kbd_queue.remove_at(0)
	kbd_data = sc
	kbd_busy = true
	pic.raise_irq(1)
	return true


# -------------------------------------------------------------- main loop
func tick() -> void:
	if skip_to_tick:
		cpu.halted = false
		skip_to_tick = false
	ticks += 1
	last_tick = cpu.icount
	pic.raise_irq(0)
	pump_keyboard()


## Run until max_instr more instructions (emulated time) have elapsed or exit.
## In realtime mode, return early when the CPU idles (the front-end ticks).
func run(max_instr: int) -> void:
	var end := cpu.icount + max_instr
	while not exited and cpu.icount < end:
		if realtime:
			# idle (HLT, blocked service or key-poll heuristic): wait for tick()
			if cpu.halted and (skip_to_tick or not cpu.irq_flag):
				return
			cpu.run(end - cpu.icount)
			continue
		if cpu.halted and skip_to_tick:
			cpu.halted = false
			skip_to_tick = false
			cpu.icount = maxi(cpu.icount, next_tick)
		elif cpu.halted:
			if not cpu.iflag and not idle:
				log_msg("HLT with interrupts disabled at %04X:%04X" % [cpu.op_cs, cpu.op_ip])
				exited = true
				break
			cpu.icount = maxi(cpu.icount, next_tick)
		var k := next_tick - cpu.icount
		if k > end - cpu.icount:
			k = end - cpu.icount
		if k > 0:
			cpu.run(k)
		if cpu.icount >= next_tick:
			next_tick += tick_len
			if next_tick <= cpu.icount:
				next_tick = cpu.icount + tick_len
			idle = false
			tick()
