## Native PC model for the recompiled program: memory map and ROM stubs, I/O
## ports, keyboard queue, the 18.2 Hz clock and the service dispatcher.
## (The instruction interpreter that used to live here is gone: the program
## runs as GDScript produced by tools/recomp/recompile.py.)
##
## Time.  Two clocks are supported:
## * realtime (the game in Godot): BIOS ticks follow the wall clock (x speed),
##   checked whenever the program calls a service, reads a port, or has taken
##   EDGE_BUDGET loop back-edges;
## * deterministic (tests): one tick every `polls_per_tick` *polls*: service
##   calls, port reads and loop back-edges (backward conditional/direct jumps),
##   so a test run and the Python oracle see the same time at the same logical
##   points.
##
## Back-edges are counted by the generated code itself: the host hands out a
## budget (cpu.eb) of back-edges it may take before it must call
## RecompHost.edge_poll(); account_edges() books what was used whenever the
## host gets control.  In deterministic mode the budget ends exactly at the
## next tick.
##
## Timer interrupt.  If the program hooks INT 08h (Turbo Pascal does, to
## calibrate Delay), a tick does not update the BIOS clock directly: it makes
## IRQ 0 pending, and the next back-edge (with IF=1) delivers it to the
## program's handler, which chains to the ROM timer stub.
##
## Threads.  In Godot the program runs on its own Thread (see main.gd).  The
## main thread only calls key_scancodes(), request_abort(), set_paused(),
## wake() and reads memory / speaker state for output; everything else runs on
## the program thread.  The key queue is guarded by `mutex`.
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
const TICK_HZ := 18.2065
## Back-edges between edge polls in realtime mode.
const EDGE_BUDGET := 4096

var cpu: CpuState
var handlers: Dictionary = {}
var seen: Dictionary = {}
var trace_ints: bool = false
var int_counts: Dictionary = {}
var port_counts: Dictionary = {}
var exited: bool = false
var exit_code: int = 0
var pit_div: PackedInt32Array = PackedInt32Array([65536, 65536, 65536])
var pit_rl: PackedInt32Array = PackedInt32Array([3, 3, 3])
var pit_wlow: Array = [null, null, null]
var ticks: int = 0
var speaker: int = 0
var kbd_queue: PackedInt32Array = PackedInt32Array()
var cga_toggle: int = 0
var cga_mode_reg: int = 0x29
var cga_color_reg: int = 0x30
var crtc_index: int = 0
var crtc: PackedInt32Array = PackedInt32Array()
var log_lines: PackedStringArray = PackedStringArray()
var bios = null   # BIOS (untyped: BIOS refers back to Machine)

# ---- clock
var deterministic: bool = false
var polls_per_tick: int = 64
var poll_count: int = 0
var speed: float = 1.0
var last_usec: int = 0
var acc_usec: float = 0.0
var idle_polls: int = 0
## Callables run after every tick (tests: key scripts).
var tick_hooks: Array = []
## Callable(n) run before every service call (tests: lockstep checkpoints).
var service_hook: Callable = Callable()
var port_hook: Callable = Callable()
var max_idle_ticks: int = 200000
var eb_set: int = 1
var irq0_pending: bool = false

# ---- shared with the main thread
var mutex: Mutex = null
var sem: Semaphore = null
var in_queue: PackedInt32Array = PackedInt32Array()
var waiting: bool = false
var abort: bool = false
var paused: bool = false


func _init() -> void:
	cpu = CpuState.new()
	crtc.resize(32)
	mutex = Mutex.new()
	sem = Semaphore.new()
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
	# timer: F1 08 ; INT 1C ; PUSH AX ; MOV AL,20 ; OUT 20,AL ; POP AX ; IRET (data only)
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


func is_hw_vector(n: int) -> bool:
	return (n >= 0x08 and n < 0x10) or (n >= 0x70 and n < 0x78)


# ------------------------------------------------------------- services
## Runs BIOS/DOS service `n` on the register state in `cpu` (a ROM stub was
## reached: the IRET frame is at SS:SP).  Blocks (waits for input) when the
## service has to.  Returns DONE, or BLOCK if the program must stop (exit or
## abort requested).
func service(n: int) -> int:
	account_edges()
	var r := _service(n)
	new_budget()
	return r


func _service(n: int) -> int:
	cpu.icount += 1
	if trace_ints:
		var key := "%02Xh/%02Xh" % [n, cpu.r[0] >> 8]
		int_counts[key] = int(int_counts.get(key, 0)) + 1
	if service_hook.is_valid():
		service_hook.call(n)
	sync_time()
	if not handlers.has(n):
		if not is_hw_vector(n):
			once("int", n, cpu.r[0] >> 8)
		return DONE
	var h: Callable = handlers[n]
	var res = h.call()
	var idle := 0
	while typeof(res) == TYPE_INT and res == BLOCK:
		if exited or abort:
			return BLOCK
		if not wait_input():
			return BLOCK
		idle += 1
		if deterministic and idle > max_idle_ticks:
			log_msg("[host] waited %d ticks for input; stopping" % idle)
			abort = true
			return BLOCK
		res = h.call()
	if not is_hw_vector(n):
		# copy the arithmetic flags into the FLAGS image IRET will pop
		var a: int = (cpu.sr[2] << 4) + ((cpu.r[4] + 4) & 0xFFFF)
		var f := rw(a)
		f = (f & ~0x08D5) | (cpu.get_flags() & 0x08D5)
		ww(a, f)
	if exited or abort:
		return BLOCK
	return DONE


## kbhit()/AH=06h found no key: in realtime mode, let the CPU breathe a little
## during the game's busy-wait loops (the game's timing comes from the clock).
func idle_poll() -> void:
	if deterministic:
		return
	idle_polls += 1
	if idle_polls >= 64:
		idle_polls = 0
		OS.delay_usec(500)


# ------------------------------------------------------------------ ports
func count_port(kind: String, port: int) -> void:
	var key := "%s %03Xh" % [kind, port]
	port_counts[key] = int(port_counts.get(key, 0)) + 1


func port_in(port: int, w: int) -> int:
	account_edges()
	var v := _port_in(port, w)
	new_budget()
	return v


func _port_in(port: int, w: int) -> int:
	if w:
		return port_in(port, 0) | (port_in(port + 1, 0) << 8)
	port &= 0xFFFF
	if trace_ints:
		count_port("in", port)
	if port_hook.is_valid():
		port_hook.call(port)
	sync_time()
	if port == 0x3DA or port == 0x3BA:
		cga_toggle = (cga_toggle + 1) & 3
		return 0x09 if cga_toggle >= 2 else 0x00
	if port == 0x3D5 or port == 0x3B5:
		return crtc[crtc_index & 31]
	if port == 0x61:
		return speaker
	if port == 0x62:
		return 0x00
	if port == 0x201:
		return 0xF0
	if port == 0x379 or port == 0x279 or port == 0x3BD:
		return 0x00
	if port == 0x378 or port == 0x3BC or port == 0x278 or port == 0x37A:
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
	if port >= 0x40 and port <= 0x43:
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
	elif port == 0x20 or port == 0x21 or port == 0x3B8 or port == 0x3BF:
		pass
	else:
		once("port-out", "%04X" % port)


## PIT: only the divisor matters (channel 2 drives the speaker).
func pit_write(ch: int, v: int) -> void:
	if ch == 3:
		var c := v >> 6
		if c == 3:
			return
		var rlv := (v >> 4) & 3
		if rlv == 0:
			return
		pit_rl[c] = rlv
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


func speaker_freq() -> int:
	if (speaker & 3) == 3:
		return 1193182 / pit_div[2]
	return 0


# --------------------------------------------------------------- keyboard
## Main thread: queue make/break scancodes (set 1).
func key_scancodes(codes: PackedInt32Array) -> void:
	mutex.lock()
	in_queue.append_array(codes)
	mutex.unlock()
	wake()


func bios_kbd_empty() -> bool:
	return rw(0x41A) == rw(0x41C)


## Program thread: run the BIOS keyboard handler for queued scancodes.  A make
## code waits until the BIOS buffer is empty, so typing never overruns it.
func pump_keyboard() -> void:
	if not in_queue.is_empty():
		mutex.lock()
		kbd_queue.append_array(in_queue)
		in_queue.clear()
		mutex.unlock()
	while not kbd_queue.is_empty():
		var sc: int = kbd_queue[0]
		var modifier := sc == 0x1D or sc == 0x2A or sc == 0x36 or sc == 0x38
		if not (sc & 0x80) and not modifier and not bios_kbd_empty():
			return
		kbd_queue.remove_at(0)
		bios.key_event(sc)


# ------------------------------------------------------------------ clock
## True if the program replaced the INT 08h (timer) vector.
func timer_hooked() -> bool:
	return rw(0x20) != INT08_OFF or rw(0x22) != ROM_SEG


## Book the back-edges the generated code took since the last budget.
func account_edges() -> void:
	var used := eb_set - cpu.eb
	eb_set = cpu.eb
	if used <= 0 or not deterministic:
		return
	poll_count += used
	if poll_count >= polls_per_tick:
		poll_count = 0
		do_tick()


## Hand out the next back-edge budget (1 while a timer interrupt is pending).
func new_budget() -> void:
	var b := EDGE_BUDGET
	if deterministic:
		b = maxi(1, polls_per_tick - poll_count)
	if irq0_pending:
		b = 1
	eb_set = b
	cpu.eb = b


func do_tick() -> void:
	if timer_hooked():
		irq0_pending = true
	else:
		bios.int08()
	ticks += 1
	for hk in tick_hooks:
		var c: Callable = hk
		c.call()
	pump_keyboard()


## Advance the BIOS clock to "now" (realtime) or by one poll (deterministic).
func sync_time() -> void:
	if deterministic:
		poll_count += 1
		if poll_count >= polls_per_tick:
			poll_count = 0
			do_tick()
		return
	while paused and not abort:
		waiting = true
		sem.wait()
		waiting = false
		last_usec = Time.get_ticks_usec()
	var now := Time.get_ticks_usec()
	if last_usec == 0:
		last_usec = now
	acc_usec += float(now - last_usec) * speed
	last_usec = now
	var tick_us := 1000000.0 / TICK_HZ
	var n := 0
	while acc_usec >= tick_us and n < 8:
		do_tick()
		acc_usec -= tick_us
		n += 1
	if acc_usec > tick_us * 8.0:
		acc_usec = 0.0
	if not in_queue.is_empty():
		pump_keyboard()


## A service is blocked (no key yet): wait for the next event.
## Returns false if the program must stop.
func wait_input() -> bool:
	if deterministic:
		poll_count = 0
		do_tick()
		return not abort
	waiting = true
	sem.wait()
	waiting = false
	sync_time()
	return not abort


## Main thread: wake the program thread if it is waiting (called every frame).
func wake() -> void:
	if waiting:
		sem.post()


func request_abort() -> void:
	abort = true
	sem.post()


func set_paused(p: bool) -> void:
	paused = p
	if not p:
		wake()
