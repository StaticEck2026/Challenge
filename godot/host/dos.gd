## DOS 3.30 emulation (HLE): MZ/COM loader, PSP, MCB chain, INT 20h/21h/2Fh/33h.
## One-to-one port of tools/refemu/dos.py.
##
## Drives map to host directories.  Each drive has a read directory and an
## optional write directory (e.g. res://dos_c read-only + user://dos_c
## writable): lookups try the write directory first, files are created there,
## and a file that only exists in the read directory is copied over before it
## is opened for writing.
class_name DOS
extends RefCounted

const ENV_SEG := 0x0101
const FIRST_MCB := 0x0100
const MEM_TOP := 0xA000
const ERR_FUNC := 1
const ERR_NOFILE := 2
const ERR_NOPATH := 3
const ERR_TOOMANY := 4
const ERR_ACCESS := 5
const ERR_HANDLE := 6
const ERR_MCB := 7
const ERR_NOMEM := 8
const ERR_BADBLOCK := 9
const ERR_DRIVE := 15
const ERR_NOMORE := 18
const DEVICES := ["CON", "NUL", "PRN", "AUX", "CLOCK$", "LPT1", "COM1"]


class DOSFile:
	var fp: FileAccess = null
	var path: String = ""
	var dev: String = ""
	var drive: int = 2
	var refs: int = 1

	func _init(p_fp: FileAccess, p_path: String, p_dev: String = "", p_drive: int = 2) -> void:
		fp = p_fp
		path = p_path
		dev = p_dev
		drive = p_drive


var m: Machine
var cpu: CpuState
var bios: BIOS
## drive letter -> [read_dir, write_dir]  (write_dir "" = same as read_dir)
var drives: Dictionary = {}
var cur_drive: String = "C"
var cwd: Dictionary = {}
var files: Array = []
var printer_path: String = ""
var psp: int = 0
var dta: Array = [0, 0x80]
var last_error: int = 0
var line_buf: PackedByteArray = PackedByteArray()
var line_active: bool = false
var read_queue: PackedByteArray = PackedByteArray()
var pending_scan: int = -1
var ctrl_break: int = 0
var verify: int = 0
var exit_code: int = 0
var env: Dictionary = {"COMSPEC": "C:\\COMMAND.COM", "PATH": "C:\\"}
var file_log: Array = []
var program_name: String = ""
var load_info: Dictionary = {}
var find_list: Array = []
var find_pos: int = 0
var fcbs: Dictionary = {}


func _init(p_machine: Machine, p_bios: BIOS, p_drives: Dictionary, p_cur_drive: String = "C",
		p_printer_path: String = "") -> void:
	m = p_machine
	cpu = m.cpu
	bios = p_bios
	for k in p_drives:
		var v = p_drives[k]
		drives[String(k).to_upper()] = v if typeof(v) == TYPE_ARRAY else [v, ""]
		cwd[String(k).to_upper()] = ""
	cur_drive = p_cur_drive.to_upper()
	printer_path = p_printer_path
	files.resize(20)
	files[0] = DOSFile.new(null, "CON", "CON")
	files[1] = DOSFile.new(null, "CON", "CON")
	files[2] = DOSFile.new(null, "CON", "CON")
	files[3] = DOSFile.new(null, "AUX", "AUX")
	files[4] = DOSFile.new(null, "PRN", "PRN")
	m.handlers[0x20] = int20
	m.handlers[0x21] = int21
	m.handlers[0x22] = nop
	m.handlers[0x23] = nop
	m.handlers[0x24] = int24
	m.handlers[0x27] = int27
	m.handlers[0x28] = nop
	m.handlers[0x29] = int29
	m.handlers[0x2A] = nop
	m.handlers[0x2F] = int2f
	m.handlers[0x33] = int33
	m.handlers[0x00] = int00
	m.handlers[0x67] = int67


func nop() -> int:
	return Machine.DONE


# ------------------------------------------------------------ memory/MCB
func mcb_write(seg: int, kind: int, owner: int, size: int) -> void:
	var a := seg << 4
	m.wb(a, kind)
	m.ww(a + 1, owner)
	m.ww(a + 3, size)
	for i in range(5, 16):
		m.wb(a + i, 0)


## Returns [kind, owner, size].
func mcb_read(seg: int) -> Array:
	var a := seg << 4
	return [m.rb(a), m.rw(a + 1), m.rw(a + 3)]


## Returns an Array of [seg, kind, owner, size], or [] if the chain is broken.
func mcb_chain() -> Array:
	var seg := FIRST_MCB
	var out: Array = []
	for _i in range(1000):
		var e := mcb_read(seg)
		if e[0] != 0x4D and e[0] != 0x5A:
			return []
		out.append([seg, e[0], e[1], e[2]])
		if e[0] == 0x5A:
			return out
		seg = seg + 1 + int(e[2])
	return []


func mcb_merge() -> void:
	var chain := mcb_chain()
	if chain.is_empty():
		return
	var i := 0
	while i < chain.size() - 1:
		var cur: Array = chain[i]
		var nxt: Array = chain[i + 1]
		if cur[2] == 0 and nxt[2] == 0:
			var size: int = int(cur[3]) + 1 + int(nxt[3])
			mcb_write(cur[0], nxt[1], 0, size)
			chain[i] = [cur[0], nxt[1], 0, size]
			chain.remove_at(i + 1)
		else:
			i += 1


## First fit. Returns [segment or -1, largest, error].
func alloc(paras: int, owner: int) -> Array:
	mcb_merge()
	var chain := mcb_chain()
	if chain.is_empty():
		return [-1, 0, ERR_MCB]
	var largest := 0
	for e in chain:
		var seg: int = e[0]
		var kind: int = e[1]
		var size: int = e[3]
		if e[2] != 0:
			continue
		if size >= paras:
			if size > paras:
				mcb_write(seg + 1 + paras, kind, 0, size - paras - 1)
				mcb_write(seg, 0x4D, owner, paras)
			else:
				mcb_write(seg, kind, owner, paras)
			return [seg + 1, 0, 0]
		largest = maxi(largest, size)
	return [-1, largest, ERR_NOMEM]


func free_block(block: int) -> int:
	var seg := block - 1
	var e := mcb_read(seg)
	if e[0] != 0x4D and e[0] != 0x5A:
		return ERR_BADBLOCK
	mcb_write(seg, e[0], 0, e[2])
	return 0


## Returns [error, available paragraphs].
func resize(block: int, paras: int) -> Array:
	var seg := block - 1
	var e := mcb_read(seg)
	var kind: int = e[0]
	var owner: int = e[1]
	var size: int = e[2]
	if kind != 0x4D and kind != 0x5A:
		return [ERR_BADBLOCK, 0]
	if paras <= size:
		if paras < size:
			mcb_write(seg + 1 + paras, kind, 0, size - paras - 1)
			mcb_write(seg, 0x4D, owner, paras)
			mcb_merge()
		return [0, paras]
	mcb_merge()
	e = mcb_read(seg)
	kind = e[0]
	owner = e[1]
	size = e[2]
	var avail := size
	if kind == 0x4D:
		var n := mcb_read(seg + 1 + size)
		if n[1] == 0:
			avail = size + 1 + int(n[2])
			if avail >= paras:
				if avail > paras:
					mcb_write(seg + 1 + paras, n[0], 0, avail - paras - 1)
					mcb_write(seg, 0x4D, owner, paras)
				else:
					mcb_write(seg, n[0], owner, paras)
				return [0, paras]
	return [ERR_NOMEM, avail]


# ----------------------------------------------------------------- loader
func build_env(seg: int, program_path: String) -> int:
	var data := PackedByteArray()
	for k in env:
		data.append_array(("%s=%s" % [k, env[k]]).to_ascii_buffer())
		data.append(0)
	data.append(0)
	data.append(1)
	data.append(0)
	data.append_array(program_path.to_ascii_buffer())
	data.append(0)
	var a := seg << 4
	for i in range(data.size()):
		cpu.mem[a + i] = data[i]
	return (data.size() + 15) / 16


func build_psp(p: int, env_seg: int, tail: String, mem_end: int) -> void:
	var a := p << 4
	for i in range(256):
		cpu.mem[a + i] = 0
	m.wb(a, 0xCD)
	m.wb(a + 1, 0x20)
	m.ww(a + 2, mem_end)
	m.wb(a + 5, 0x9A)
	m.ww(a + 6, 0xE000 + 0x21 * 4)
	m.ww(a + 8, 0xF000)
	var vecs := [0x22, 0x23, 0x24]
	for i in range(3):
		var sv := m.get_vec(vecs[i])
		m.ww(a + 0x0A + i * 4, sv[1])
		m.ww(a + 0x0C + i * 4, sv[0])
	m.ww(a + 0x16, p)
	for i in range(20):
		m.wb(a + 0x18 + i, i if i < 5 else 0xFF)
	m.ww(a + 0x2C, env_seg)
	m.ww(a + 0x32, 20)
	m.ww(a + 0x34, 0x18)
	m.ww(a + 0x36, p)
	m.wb(a + 0x50, 0xCD)
	m.wb(a + 0x51, 0x21)
	m.wb(a + 0x52, 0xCB)
	var args := tail.split(" ", false)
	var bases := [0x5C, 0x6C]
	for i in range(2):
		var base: int = bases[i]
		m.wb(a + base, 0)
		for j in range(11):
			m.wb(a + base + 1 + j, 0x20)
		if i < args.size() and not args[i].begins_with("/"):
			parse_fcb_name(a + base, args[i])
	var t := tail.to_ascii_buffer()
	if t.size() > 0 and t[0] != 0x20:
		var t2 := PackedByteArray([0x20])
		t2.append_array(t)
		t = t2
	if t.size() > 126:
		t = t.slice(0, 126)
	m.wb(a + 0x80, t.size())
	for i in range(t.size()):
		m.wb(a + 0x81 + i, t[i])
	m.wb(a + 0x81 + t.size(), 0x0D)


func parse_fcb_name(a: int, name: String) -> void:
	var drive := 0
	if name.length() >= 2 and name[1] == ":":
		drive = name.to_upper().unicode_at(0) - 64
		name = name.substr(2)
	var up := name.to_upper()
	var dot := up.find(".")
	var base := up if dot < 0 else up.substr(0, dot)
	var ext := "" if dot < 0 else up.substr(dot + 1)
	m.wb(a, drive)
	var b := base.substr(0, 8).rpad(8).to_ascii_buffer()
	var e := ext.substr(0, 3).rpad(3).to_ascii_buffer()
	for i in range(8):
		m.wb(a + 1 + i, b[i])
	for i in range(3):
		m.wb(a + 9 + i, e[i])


## Loads an MZ .EXE or a .COM program. Returns load_info (empty on failure).
func load_program(dos_path: String, tail: String = "") -> Dictionary:
	var host := resolve(dos_path)
	if host == "" or host.begins_with("DEV:"):
		return {}
	var data := FileAccess.get_file_as_bytes(host)
	if data.is_empty():
		return {}
	var full := full_dos_path(dos_path)
	program_name = full
	var env_paras := 0x10
	mcb_write(FIRST_MCB, 0x4D, 0x0000, env_paras)
	build_env(ENV_SEG, full)
	var prog_mcb := ENV_SEG + env_paras
	var p := prog_mcb + 1
	mcb_write(prog_mcb, 0x5A, p, MEM_TOP - p)
	mcb_write(FIRST_MCB, 0x4D, p, env_paras)
	psp = p
	var sig := data.decode_u16(0)
	if sig == 0x5A4D or sig == 0x4D5A:
		var cblp := data.decode_u16(2)
		var cp := data.decode_u16(4)
		var crlc := data.decode_u16(6)
		var cparhdr := data.decode_u16(8)
		var minalloc := data.decode_u16(10)
		var maxalloc := data.decode_u16(12)
		var ss := data.decode_u16(14)
		var sp := data.decode_u16(16)
		var ipv := data.decode_u16(20)
		var cs := data.decode_u16(22)
		var lfarlc := data.decode_u16(24)
		var img_end := cp * 512 - ((512 - cblp) if cblp else 0)
		var hdr := cparhdr * 16
		var image_size := img_end - hdr
		var load_seg := p + 0x10
		var need := (image_size + 15) / 16 + minalloc + 0x10
		var avail := MEM_TOP - p
		var max_need := (image_size + 15) / 16 + maxalloc + 0x10 if maxalloc else need
		var want := mini(avail, maxi(need, max_need))
		mcb_write(prog_mcb, 0x5A, p, avail)
		if want < avail:
			resize(p, want)
		var a := load_seg << 4
		for i in range(image_size):
			cpu.mem[a + i] = data[hdr + i]
		for i in range(crlc):
			var off := data.decode_u16(lfarlc + i * 4)
			var seg := data.decode_u16(lfarlc + i * 4 + 2)
			var la := ((load_seg + seg) << 4) + off
			var v := (cpu.mem[la] | (cpu.mem[la + 1] << 8)) + load_seg
			cpu.mem[la] = v & 0xFF
			cpu.mem[la + 1] = (v >> 8) & 0xFF
		build_psp(p, ENV_SEG, tail, MEM_TOP)
		cpu.sr[0] = p
		cpu.sr[3] = p
		cpu.sr[2] = (ss + load_seg) & 0xFFFF
		cpu.r[4] = sp
		cpu.sr[1] = (cs + load_seg) & 0xFFFF
		cpu.ip = ipv
		load_info = {"psp": p, "load_seg": load_seg, "image_size": image_size, "relocs": crlc,
			"cs": cs, "ip": ipv, "ss": ss, "sp": sp, "minalloc": minalloc, "maxalloc": maxalloc,
			"overlay_bytes": data.size() - img_end}
	else:
		mcb_write(prog_mcb, 0x5A, p, MEM_TOP - p)
		build_psp(p, ENV_SEG, tail, MEM_TOP)
		var a := (p << 4) + 0x100
		for i in range(data.size()):
			cpu.mem[a + i] = data[i]
		for i in range(4):
			cpu.sr[i] = p
		cpu.ip = 0x100
		cpu.r[4] = 0xFFFE
		m.ww((p << 4) + 0xFFFE, 0)
		load_info = {"psp": p, "com_size": data.size()}
	dta = [p, 0x80]
	cpu.r[0] = 0
	cpu.r[3] = 0
	cpu.r[1] = 0xFF
	cpu.r[2] = p
	cpu.r[6] = cpu.ip
	cpu.r[7] = cpu.r[4]
	cpu.r[5] = 0x91C
	cpu.iflag = 1
	return load_info


# ------------------------------------------------------------- file names
func read_asciiz(seg: int, off: int, maxlen: int = 128) -> String:
	var a := (seg << 4) + off
	var out := PackedByteArray()
	for i in range(maxlen):
		var c := m.rb(a + i)
		if c == 0:
			break
		out.append(c)
	return out.get_string_from_ascii()


## Returns [drive letter, Array of upper-case path components].
func split_path(name: String) -> Array:
	name = name.strip_edges().replace("/", "\\")
	var drive := cur_drive
	if name.length() >= 2 and name[1] == ":":
		drive = name[0].to_upper()
		name = name.substr(2)
	var parts: Array = []
	if name.begins_with("\\"):
		parts = Array(name.substr(1).split("\\"))
	else:
		var c: String = cwd.get(drive, "")
		if c != "":
			parts = Array(c.split("\\"))
		parts.append_array(Array(name.split("\\")))
	var out: Array = []
	for p in parts:
		if p == "" or p == ".":
			continue
		if p == "..":
			if not out.is_empty():
				out.pop_back()
			continue
		out.append(String(p).to_upper())
	return [drive, out]


func full_dos_path(name: String) -> String:
	var sp := split_path(name)
	return "%s:\\%s" % [sp[0], "\\".join(PackedStringArray(sp[1]))]


func canon_component(p: String) -> String:
	var dot := p.find(".")
	if dot < 0:
		return p.substr(0, 8)
	var ext := p.substr(dot + 1)
	return p.substr(0, mini(dot, 8)) + ("." + ext.substr(0, 3) if ext != "" else "")


static func join_path(dir: String, name: String) -> String:
	if dir.ends_with("/"):
		return dir + name
	return dir + "/" + name


## Case-insensitive lookup of one component in a host directory ("" = missing).
func lookup(hostdir: String, comp: String) -> String:
	comp = canon_component(comp).to_upper()
	if not DirAccess.dir_exists_absolute(hostdir):
		return ""
	for n in DirAccess.get_files_at(hostdir):
		if n.to_upper() == comp:
			return join_path(hostdir, n)
	for n in DirAccess.get_directories_at(hostdir):
		if n.to_upper() == comp:
			return join_path(hostdir, n)
	return ""


func _resolve_in(root: String, parts: Array, create: bool, want_dir: bool) -> String:
	if root == "":
		return ""
	var cur := root
	for i in range(parts.size()):
		var nxt := lookup(cur, parts[i])
		if nxt == "":
			if create and i == parts.size() - 1:
				return join_path(cur, canon_component(parts[i]).to_upper())
			return ""
		cur = nxt
	if want_dir and not DirAccess.dir_exists_absolute(cur):
		return ""
	return cur


## DOS name -> host path ("" if not found).  With create, the final component
## may be missing and is returned in upper case (in the drive's write dir).
func resolve(name: String, create: bool = false, want_dir: bool = false) -> String:
	var sp := split_path(name)
	var drive: String = sp[0]
	var parts: Array = sp[1]
	if not drives.has(drive):
		return ""
	if not parts.is_empty():
		var last: String = parts[-1]
		var base := last.split(".")[0]
		if last in DEVICES:
			return "DEV:" + last
		if base in ["CON", "NUL", "PRN", "AUX", "LPT1", "COM1"]:
			return "DEV:" + base
	var dirs: Array = drives[drive]
	var rdir: String = dirs[0]
	var wdir: String = dirs[1]
	if wdir != "":
		var found := _resolve_in(wdir, parts, false, want_dir)
		if found != "":
			return found
		found = _resolve_in(rdir, parts, false, want_dir)
		if found != "":
			return found
		if create:
			return _resolve_in(wdir, parts, true, want_dir)
		return ""
	return _resolve_in(rdir, parts, create, want_dir)


## Make sure a file that lives only in the read-only directory is copied to
## the write directory before it is opened for writing.  Returns the path to use.
func writable_path(name: String, host: String) -> String:
	var sp := split_path(name)
	var dirs: Array = drives.get(sp[0], ["", ""])
	var wdir: String = dirs[1]
	if wdir == "" or host.begins_with(wdir):
		return host
	var parts: Array = sp[1]
	var cur := wdir
	for i in range(parts.size() - 1):
		var nxt := lookup(cur, parts[i])
		if nxt == "":
			nxt = join_path(cur, String(parts[i]))
			DirAccess.make_dir_recursive_absolute(nxt)
		cur = nxt
	var dst := join_path(cur, host.get_file())
	DirAccess.copy_absolute(host, dst)
	return dst


func drive_num(name: String) -> int:
	var sp := split_path(name)
	return String(sp[0]).unicode_at(0) - 65


# ---------------------------------------------------------------- results
func ok(ax: int = -1) -> int:
	if ax >= 0:
		cpu.r[0] = ax & 0xFFFF
	cpu.cf = 0
	return Machine.DONE


func err(code: int) -> int:
	cpu.r[0] = code
	cpu.cf = 1
	last_error = code
	return Machine.DONE


func alloc_handle(f: DOSFile) -> int:
	for i in range(5, 20):
		if files[i] == null:
			files[i] = f
			return i
	return -1


# ---------------------------------------------------------------- console
func con_out(ch: int) -> void:
	bios.tty(ch)


func con_in_available() -> bool:
	return bios.kbd_peek() >= 0


## Return ASCII (0 for extended keys, scan code returned next call) or -1.
func con_getkey() -> int:
	if pending_scan >= 0:
		var v := pending_scan
		pending_scan = -1
		return v
	var kw := bios.kbd_get()
	if kw < 0:
		return -1
	var al := kw & 0xFF
	if al == 0 or al == 0xE0:
		pending_scan = kw >> 8
		return 0
	return al


## Collect a line with echo. Returns true when Enter was pressed (line in line_buf).
func cooked_line(maxlen: int) -> bool:
	if not line_active:
		line_buf = PackedByteArray()
		line_active = true
	while true:
		var c := con_getkey()
		if c < 0:
			return false
		if c == 0:
			con_getkey()
			continue
		if c == 13:
			line_active = false
			return true
		if c == 8:
			if not line_buf.is_empty():
				line_buf.remove_at(line_buf.size() - 1)
				con_out(8)
				con_out(32)
				con_out(8)
			continue
		if c == 27:
			while not line_buf.is_empty():
				line_buf.remove_at(line_buf.size() - 1)
				con_out(8)
				con_out(32)
				con_out(8)
			continue
		if line_buf.size() < maxlen:
			line_buf.append(c)
			con_out(c)
	return false


# ================================================================ INT 21h
func int20() -> int:
	terminate(0)
	return Machine.DONE


func int27() -> int:
	m.log_msg("INT 27h TSR -> treated as exit")
	terminate(0)
	return Machine.DONE


func int00() -> int:
	for ch in "\r\nDivide overflow\r\n".to_ascii_buffer():
		con_out(ch)
	m.log_msg("Divide overflow at %04X:%04X" % [cpu.op_cs, cpu.op_ip])
	terminate(0xFF)
	return Machine.DONE


func int24() -> int:
	cpu.r[0] = (cpu.r[0] & 0xFF00) | 3
	return Machine.DONE


func int29() -> int:
	con_out(cpu.r[0] & 0xFF)
	return Machine.DONE


func int2f() -> int:
	var ax: int = cpu.r[0]
	if not (ax >> 8) in [0x16, 0x43, 0x11, 0x12, 0x15, 0x1A, 0xB7, 0xAE]:
		m.once("int", 0x2F, ax >> 8)
	cpu.r[0] = ax & 0xFF00
	return Machine.DONE


func int33() -> int:
	cpu.r[0] = 0
	return Machine.DONE


func int67() -> int:
	cpu.r[0] = 0x8400
	return Machine.DONE


func terminate(code: int) -> void:
	exit_code = code
	m.exit_code = code
	m.exited = true
	cpu.halted = true
	for i in range(5, 20):
		var f: DOSFile = files[i]
		if f != null and f.fp != null:
			f.fp.close()
		files[i] = null


func int21() -> int:
	var ax: int = cpu.r[0]
	var ah := ax >> 8
	var al := ax & 0xFF
	if ah == 0x00:
		terminate(0)
	elif ah == 0x01 or ah == 0x07 or ah == 0x08:
		var c := con_getkey()
		if c < 0:
			return Machine.BLOCK
		if ah == 0x01:
			con_out(c)
		cpu.r[0] = (ax & 0xFF00) | c
	elif ah == 0x02:
		con_out(cpu.r[2] & 0xFF)
		cpu.r[0] = (ax & 0xFF00) | (cpu.r[2] & 0xFF)
	elif ah == 0x05:
		printer_out(PackedByteArray([cpu.r[2] & 0xFF]))
	elif ah == 0x06:
		var dl: int = cpu.r[2] & 0xFF
		if dl == 0xFF:
			if not con_in_available() and pending_scan < 0:
				cpu.zf = 1
				cpu.r[0] = ax & 0xFF00
				bios.idle_poll()
			else:
				var c := con_getkey()
				cpu.zf = 0
				cpu.r[0] = (ax & 0xFF00) | c
		else:
			con_out(dl)
			cpu.r[0] = (ax & 0xFF00) | dl
	elif ah == 0x09:
		var a: int = (cpu.sr[3] << 4) + cpu.r[2]
		for i in range(65536):
			var c := m.rb(a + i)
			if c == 0x24:
				break
			con_out(c)
		cpu.r[0] = (ax & 0xFF00) | 0x24
	elif ah == 0x0A:
		var a: int = (cpu.sr[3] << 4) + cpu.r[2]
		var maxlen := m.rb(a)
		if maxlen == 0:
			return Machine.DONE
		if not cooked_line(maxlen - 1):
			return Machine.BLOCK
		m.wb(a + 1, line_buf.size())
		for i in range(line_buf.size()):
			m.wb(a + 2 + i, line_buf[i])
		m.wb(a + 2 + line_buf.size(), 13)
		con_out(13)
	elif ah == 0x0B:
		var avail := con_in_available() or pending_scan >= 0
		cpu.r[0] = (ax & 0xFF00) | (0xFF if avail else 0)
		if not avail:
			bios.idle_poll()
	elif ah == 0x0C:
		while bios.kbd_get() >= 0:
			pass
		pending_scan = -1
		if al in [0x01, 0x06, 0x07, 0x08, 0x0A]:
			cpu.r[0] = (al << 8) | al
			return int21()
		cpu.r[0] = ax & 0xFF00
	elif ah == 0x0D:
		pass
	elif ah == 0x0F or ah == 0x10 or ah == 0x27:
		return fcb_io(ah)
	elif ah == 0x29:
		return fcb_parse(al)
	elif ah == 0x0E:
		var d: int = cpu.r[2] & 0xFF
		if d < 26 and drives.has(char(65 + d)):
			cur_drive = char(65 + d)
		cpu.r[0] = (ax & 0xFF00) | 5
	elif ah == 0x19:
		cpu.r[0] = (ax & 0xFF00) | (cur_drive.unicode_at(0) - 65)
	elif ah == 0x1A:
		dta = [cpu.sr[3], cpu.r[2]]
	elif ah == 0x2F:
		cpu.sr[0] = dta[0]
		cpu.r[3] = dta[1]
	elif ah == 0x25:
		m.set_vec(al, cpu.sr[3], cpu.r[2])
	elif ah == 0x35:
		var sv := m.get_vec(al)
		cpu.sr[0] = sv[0]
		cpu.r[3] = sv[1]
	elif ah == 0x2A:
		var n := bios.now()
		cpu.r[1] = n["year"]
		cpu.r[2] = (int(n["month"]) << 8) | int(n["day"])
		cpu.r[0] = (ax & 0xFF00) | int(n["weekday"])
	elif ah == 0x2B:
		cpu.r[0] = ax & 0xFF00
	elif ah == 0x2C:
		var n := bios.now()
		cpu.r[1] = (int(n["hour"]) << 8) | int(n["minute"])
		cpu.r[2] = (int(n["second"]) << 8) | bios.hundredths()
	elif ah == 0x2D:
		cpu.r[0] = ax & 0xFF00
	elif ah == 0x2E:
		verify = al & 1
	elif ah == 0x30:
		cpu.r[0] = 0x1E03
		cpu.r[3] = 0
		cpu.r[1] = 0
	elif ah == 0x33:
		if al == 0:
			cpu.r[2] = (cpu.r[2] & 0xFF00) | ctrl_break
		elif al == 1:
			ctrl_break = cpu.r[2] & 1
		elif al == 5:
			cpu.r[2] = (cpu.r[2] & 0xFF00) | 3
	elif ah == 0x36:
		var d: int = cpu.r[2] & 0xFF
		var drive := cur_drive if d == 0 else char(64 + d)
		if not drives.has(drive):
			cpu.r[0] = 0xFFFF
		else:
			cpu.r[0] = 4
			cpu.r[3] = 2000
			cpu.r[1] = 512
			cpu.r[2] = 2400
	elif ah == 0x37:
		if al == 0:
			cpu.r[2] = (cpu.r[2] & 0xFF00) | 0x2F
		cpu.r[0] = ax & 0xFF00
	elif ah == 0x38:
		if al == 0:
			var a: int = (cpu.sr[3] << 4) + cpu.r[2]
			var info := [0, 0, 0x24, 0, 0, 0, 0, 0, 0x2C, 0, 0x2E, 0, 0x2F, 0, 0x3A]
			for i in range(34):
				m.wb(a + i, info[i] if i < info.size() else 0)
			cpu.r[3] = 1
		ok()
	elif ah == 0x39 or ah == 0x3A:
		var name := read_asciiz(cpu.sr[3], cpu.r[2])
		var host := resolve(name, true)
		var e := ERR_NOPATH
		if host != "":
			if ah == 0x39:
				e = DirAccess.make_dir_absolute(host)
			else:
				e = DirAccess.remove_absolute(host)
		if host != "" and e == OK:
			ok()
		else:
			err(ERR_NOPATH if ah == 0x39 else ERR_ACCESS)
	elif ah == 0x3B:
		var name := read_asciiz(cpu.sr[3], cpu.r[2])
		var host := resolve(name, false, true)
		var sp := split_path(name)
		if host == "" and not (sp[1] as Array).is_empty():
			err(ERR_NOPATH)
		else:
			cwd[sp[0]] = "\\".join(PackedStringArray(sp[1]))
			ok()
	elif ah == 0x3C or ah == 0x5B:
		return create_file(ah)
	elif ah == 0x3D:
		return open_file(al)
	elif ah == 0x3E:
		var h: int = cpu.r[3]
		var f: DOSFile = files[h] if h < 20 else null
		if f == null:
			return err(ERR_HANDLE)
		f.refs -= 1
		if f.refs <= 0 and f.fp != null:
			f.fp.close()
		files[h] = null
		ok()
	elif ah == 0x3F:
		return read_handle()
	elif ah == 0x40:
		return write_handle()
	elif ah == 0x41:
		var name := read_asciiz(cpu.sr[3], cpu.r[2])
		var host := resolve(name)
		if host == "" or host.begins_with("DEV:"):
			return err(ERR_NOFILE)
		if DirAccess.remove_absolute(host) == OK:
			file_log.append(["delete", name, host])
			ok()
		else:
			err(ERR_ACCESS)
	elif ah == 0x42:
		return seek_handle(al)
	elif ah == 0x43:
		var name := read_asciiz(cpu.sr[3], cpu.r[2])
		var host := resolve(name)
		if host == "":
			return err(ERR_NOFILE)
		if al == 0:
			cpu.r[1] = 0x10 if DirAccess.dir_exists_absolute(host) else 0x20
		ok()
	elif ah == 0x44:
		return ioctl()
	elif ah == 0x45:
		var h: int = cpu.r[3]
		var f: DOSFile = files[h] if h < 20 else null
		if f == null:
			return err(ERR_HANDLE)
		var nh := alloc_handle(f)
		if nh < 0:
			return err(ERR_TOOMANY)
		f.refs += 1
		ok(nh)
	elif ah == 0x46:
		var h: int = cpu.r[3]
		var nh: int = cpu.r[1]
		var f: DOSFile = files[h] if h < 20 else null
		if f == null or nh >= 20:
			return err(ERR_HANDLE)
		if files[nh] != null:
			var old: DOSFile = files[nh]
			old.refs -= 1
			if old.refs <= 0 and old.fp != null:
				old.fp.close()
		files[nh] = f
		f.refs += 1
		ok()
	elif ah == 0x47:
		var d: int = cpu.r[2] & 0xFF
		var drive := cur_drive if d == 0 else char(64 + d)
		if not drives.has(drive):
			return err(ERR_DRIVE)
		var p := String(cwd.get(drive, "")).to_ascii_buffer()
		p.append(0)
		var a: int = (cpu.sr[3] << 4) + cpu.r[6]
		for i in range(p.size()):
			m.wb(a + i, p[i])
		ok(0x0100)
	elif ah == 0x48:
		var res := alloc(cpu.r[3], psp)
		if res[0] < 0:
			cpu.r[3] = res[1]
			return err(res[2])
		ok(res[0])
	elif ah == 0x49:
		var e := free_block(cpu.sr[0])
		if e:
			return err(e)
		ok()
	elif ah == 0x4A:
		var res := resize(cpu.sr[0], cpu.r[3])
		if res[0]:
			cpu.r[3] = res[1]
			return err(res[0])
		ok()
	elif ah == 0x4B:
		var name := read_asciiz(cpu.sr[3], cpu.r[2])
		m.once("int", 0x21, 0x4B, "exec %s" % name)
		err(ERR_NOFILE)
	elif ah == 0x4C:
		terminate(al)
	elif ah == 0x4D:
		cpu.r[0] = exit_code
		ok()
	elif ah == 0x4E:
		return find_first()
	elif ah == 0x4F:
		return find_next()
	elif ah == 0x50:
		psp = cpu.r[3]
	elif ah == 0x51 or ah == 0x62:
		cpu.r[3] = psp
	elif ah == 0x54:
		cpu.r[0] = (ax & 0xFF00) | verify
	elif ah == 0x56:
		var oldn := read_asciiz(cpu.sr[3], cpu.r[2])
		var newn := read_asciiz(cpu.sr[0], cpu.r[7])
		var ho := resolve(oldn)
		var hn := resolve(newn, true)
		if ho == "" or hn == "":
			return err(ERR_NOFILE)
		if DirAccess.rename_absolute(ho, hn) == OK:
			ok()
		else:
			err(ERR_ACCESS)
	elif ah == 0x57:
		var h: int = cpu.r[3]
		var f: DOSFile = files[h] if h < 20 else null
		if f == null:
			return err(ERR_HANDLE)
		if al == 0:
			cpu.r[1] = 0x6000
			cpu.r[2] = ((1989 - 1980) << 9) | (8 << 5) | 8
		ok()
	elif ah == 0x58:
		if al == 0:
			ok(0)
		else:
			ok()
	elif ah == 0x59:
		cpu.r[0] = last_error
		cpu.r[3] = 0x0101
		cpu.r[1] = cpu.r[1] & 0xFF
	elif ah == 0x5A:
		err(ERR_ACCESS)
	elif ah == 0x63 or ah == 0x65 or ah == 0x66:
		err(ERR_FUNC)
	elif ah == 0x67:
		ok()
	elif ah == 0x68:
		var h: int = cpu.r[3]
		var f: DOSFile = files[h] if h < 20 else null
		if f != null and f.fp != null:
			f.fp.flush()
		ok()
	else:
		m.once("int", 0x21, ah)
		cpu.r[0] = ax & 0xFF00
		cpu.cf = 1
	return Machine.DONE


func create_file(ah: int) -> int:
	var name := read_asciiz(cpu.sr[3], cpu.r[2])
	var host := resolve(name, true)
	if host == "":
		return err(ERR_NOPATH)
	if host.begins_with("DEV:"):
		var hd := alloc_handle(DOSFile.new(null, name, host.substr(4)))
		return ok(hd) if hd >= 0 else err(ERR_TOOMANY)
	if ah == 0x5B and FileAccess.file_exists(host):
		return err(80)
	host = writable_path(name, host)
	var fp := FileAccess.open(host, FileAccess.WRITE_READ)
	if fp == null:
		return err(ERR_ACCESS)
	file_log.append(["create", name, host])
	var h := alloc_handle(DOSFile.new(fp, host, "", drive_num(name)))
	if h < 0:
		fp.close()
		return err(ERR_TOOMANY)
	return ok(h)


func open_file(al: int) -> int:
	var name := read_asciiz(cpu.sr[3], cpu.r[2])
	var host := resolve(name)
	if host == "":
		file_log.append(["open-fail", name, ""])
		return err(ERR_NOFILE)
	if host.begins_with("DEV:"):
		var hd := alloc_handle(DOSFile.new(null, name, host.substr(4)))
		return ok(hd) if hd >= 0 else err(ERR_TOOMANY)
	if DirAccess.dir_exists_absolute(host):
		return err(ERR_ACCESS)
	var mode := al & 3
	var fp: FileAccess = null
	if mode == 0:
		fp = FileAccess.open(host, FileAccess.READ)
	else:
		host = writable_path(name, host)
		fp = FileAccess.open(host, FileAccess.READ_WRITE)
	if fp == null:
		return err(ERR_ACCESS)
	file_log.append(["open", name, host])
	var h := alloc_handle(DOSFile.new(fp, host, "", drive_num(name)))
	if h < 0:
		fp.close()
		return err(ERR_TOOMANY)
	return ok(h)


func seek_handle(al: int) -> int:
	var h: int = cpu.r[3]
	var f: DOSFile = files[h] if h < 20 else null
	if f == null:
		return err(ERR_HANDLE)
	var off: int = (cpu.r[1] << 16) | cpu.r[2]
	if f.fp == null:
		cpu.r[0] = 0
		cpu.r[2] = 0
		return ok()
	if al == 1 or al == 2:
		if off & 0x80000000:
			off -= 0x100000000
	var pos := off
	if al == 1:
		pos = f.fp.get_position() + off
	elif al == 2:
		pos = f.fp.get_length() + off
	if pos < 0:
		return err(25)
	f.fp.seek(pos)
	cpu.r[2] = (pos >> 16) & 0xFFFF
	return ok(pos & 0xFFFF)


func ioctl() -> int:
	var al: int = cpu.r[0] & 0xFF
	var h: int = cpu.r[3]
	if al == 0 or al == 1 or al == 6 or al == 7:
		var f: DOSFile = files[h] if h < 20 else null
		if f == null:
			return err(ERR_HANDLE)
		if al == 0:
			if f.dev == "CON":
				cpu.r[2] = 0x80D3
			elif f.dev != "":
				cpu.r[2] = 0x80C0 | (0x04 if f.dev == "NUL" else 0)
			else:
				cpu.r[2] = f.drive & 0x3F
			return ok(cpu.r[2])
		if al == 1:
			return ok()
		if al == 6:
			var st := 0
			if f.dev == "CON":
				st = 0xFF if con_in_available() else 0x00
			elif f.dev != "":
				st = 0xFF
			else:
				st = 0xFF if f.fp.get_position() < f.fp.get_length() else 0x00
			return ok((cpu.r[0] & 0xFF00) | st)
		return ok((cpu.r[0] & 0xFF00) | 0xFF)
	if al == 8:
		var d: int = cpu.r[3] & 0xFF
		var drive := cur_drive if d == 0 else char(64 + d)
		return ok(0 if drive == "A" or drive == "B" else 1)
	if al == 9:
		cpu.r[2] = 0
		return ok()
	if al == 0x0E:
		return ok(cpu.r[0] & 0xFF00)
	m.once("int", 0x21, 0x44, "ioctl al=%02X" % al)
	return err(ERR_FUNC)


func printer_out(data: PackedByteArray) -> void:
	if printer_path == "":
		return
	var fp: FileAccess = null
	if FileAccess.file_exists(printer_path):
		fp = FileAccess.open(printer_path, FileAccess.READ_WRITE)
		if fp:
			fp.seek_end()
	else:
		fp = FileAccess.open(printer_path, FileAccess.WRITE)
	if fp:
		fp.store_buffer(data)
		fp.close()


func read_handle() -> int:
	var h: int = cpu.r[3]
	var n: int = cpu.r[1]
	var f: DOSFile = files[h] if h < 20 else null
	if f == null:
		return err(ERR_HANDLE)
	var a: int = (cpu.sr[3] << 4) + cpu.r[2]
	var data := PackedByteArray()
	if f.dev == "CON":
		if read_queue.is_empty():
			if not cooked_line(126):
				return Machine.BLOCK
			con_out(13)
			con_out(10)
			read_queue = line_buf.duplicate()
			read_queue.append(13)
			read_queue.append(10)
		data = read_queue.slice(0, n)
		read_queue = read_queue.slice(data.size())
	elif f.dev != "":
		data = PackedByteArray()
	else:
		data = f.fp.get_buffer(n)
	for i in range(data.size()):
		cpu.mem[(a + i) & 0xFFFFF] = data[i]
	return ok(data.size())


func write_handle() -> int:
	var h: int = cpu.r[3]
	var n: int = cpu.r[1]
	var f: DOSFile = files[h] if h < 20 else null
	if f == null:
		return err(ERR_HANDLE)
	var a: int = (cpu.sr[3] << 4) + cpu.r[2]
	var data := PackedByteArray()
	data.resize(n)
	for i in range(n):
		data[i] = cpu.mem[(a + i) & 0xFFFFF]
	if f.dev == "CON":
		for c in data:
			con_out(c)
	elif f.dev == "PRN":
		printer_out(data)
	elif f.dev != "":
		pass
	else:
		if n == 0:
			m.once("dos-truncate", 0x40)
		else:
			f.fp.store_buffer(data)
			f.fp.flush()
	return ok(n)


# ------------------------------------------------------------------- FCBs
func fcb_addr() -> int:
	var a: int = (cpu.sr[3] << 4) + cpu.r[2]
	if m.rb(a) == 0xFF:
		a += 7
	return a


func fcb_name(a: int) -> String:
	var drv := m.rb(a)
	var raw := PackedByteArray()
	for i in range(11):
		raw.append(m.rb(a + 1 + i))
	var text := raw.get_string_from_ascii()
	var base := text.substr(0, 8).strip_edges(false, true)
	var ext := text.substr(8).strip_edges(false, true)
	var name := base + ("." + ext if ext != "" else "")
	if drv:
		name = char(64 + drv) + ":" + name
	return name


## AH=0Fh open, 10h close, 27h random block read (DOS 1.x style file I/O).
func fcb_io(ah: int) -> int:
	var a := fcb_addr()
	if ah == 0x0F:
		var host := resolve(fcb_name(a))
		if host == "" or host.begins_with("DEV:") or DirAccess.dir_exists_absolute(host):
			cpu.r[0] = (cpu.r[0] & 0xFF00) | 0xFF
			return Machine.DONE
		var fp := FileAccess.open(host, FileAccess.READ)
		if fp == null:
			cpu.r[0] = (cpu.r[0] & 0xFF00) | 0xFF
			return Machine.DONE
		fcbs[a] = fp
		file_log.append(["fcb-open", fcb_name(a), host])
		if m.rb(a) == 0:
			m.wb(a, cur_drive.unicode_at(0) - 64)
		m.ww(a + 0x0C, 0)
		m.ww(a + 0x0E, 128)
		var size := fp.get_length()
		m.ww(a + 0x10, size & 0xFFFF)
		m.ww(a + 0x12, size >> 16)
		m.ww(a + 0x14, ((1989 - 1980) << 9) | (8 << 5) | 8)
		m.ww(a + 0x16, 0x6000)
		m.wb(a + 0x20, 0)
		cpu.r[0] &= 0xFF00
	elif ah == 0x10:
		if fcbs.has(a):
			var fp: FileAccess = fcbs[a]
			fp.close()
			fcbs.erase(a)
		cpu.r[0] &= 0xFF00
	else:
		if not fcbs.has(a):
			cpu.r[0] = (cpu.r[0] & 0xFF00) | 1
			cpu.r[1] = 0
			return Machine.DONE
		var fp: FileAccess = fcbs[a]
		var recsize := m.rw(a + 0x0E)
		if recsize == 0:
			recsize = 128
		var rec := m.rw(a + 0x21)
		if recsize < 64:
			rec |= m.rw(a + 0x23) << 16
		else:
			rec |= m.rb(a + 0x23) << 16
		var n: int = cpu.r[1]
		fp.seek(rec * recsize)
		var data := fp.get_buffer(n * recsize)
		var full := data.size() / recsize
		var part := data.size() % recsize
		var base: int = (int(dta[0]) << 4) + int(dta[1])
		for i in range(data.size()):
			m.wb(base + i, data[i])
		if part:
			for i in range(part, recsize):
				m.wb(base + full * recsize + i, 0)
		var got := full + (1 if part else 0)
		rec += got
		m.ww(a + 0x21, rec & 0xFFFF)
		m.wb(a + 0x23, (rec >> 16) & 0xFF)
		m.ww(a + 0x0C, rec / 128)
		m.wb(a + 0x20, rec % 128)
		cpu.r[1] = got
		var res := 0 if got == n and not part else (3 if part else 1)
		cpu.r[0] = (cpu.r[0] & 0xFF00) | res
	return Machine.DONE


func _fcb_ch(src: int, si: int, i: int) -> String:
	return char(m.rb(src + ((si + i) & 0xFFFF)))


## Reads a name field; returns [new index, padded text or "" when empty+keep].
func _fcb_field(src: int, si: int, i: int, width: int, keep: bool) -> Array:
	var term := " \t\r\n.;,=+:\"/[]<>|" + char(0)
	var out := ""
	while term.find(_fcb_ch(src, si, i)) < 0 and out.length() < 64:
		out += _fcb_ch(src, si, i).to_upper()
		i += 1
	if out == "" and keep:
		return [i, ""]
	out = out.substr(0, width)
	var star := out.find("*")
	if star >= 0:
		out = out.substr(0, star) + "?".repeat(width - star)
	return [i, out.rpad(width)]


## AH=29h: parse DS:SI into the FCB at ES:DI.
func fcb_parse(al: int) -> int:
	var src: int = cpu.sr[3] << 4
	var si: int = cpu.r[6]
	var dst: int = (cpu.sr[0] << 4) + cpu.r[7]
	var i := 0
	var skip := " \t;,=+:" if al & 1 else " \t"
	while skip.find(_fcb_ch(src, si, i)) >= 0:
		i += 1
	var result := 0
	var drv := 0
	var c0 := _fcb_ch(src, si, i).to_upper()
	if _fcb_ch(src, si, i + 1) == ":" and c0 >= "A" and c0 <= "Z":
		drv = c0.unicode_at(0) - 64
		if not drives.has(char(64 + drv)):
			result = 0xFF
		i += 2
	if drv or not (al & 2):
		m.wb(dst, drv)
	var fb := _fcb_field(src, si, i, 8, (al & 4) != 0)
	i = fb[0]
	var base: String = fb[1]
	if base != "":
		for k in range(8):
			m.wb(dst + 1 + k, base.unicode_at(k))
	elif not (al & 4):
		for k in range(8):
			m.wb(dst + 1 + k, 32)
	var ext := ""
	if _fcb_ch(src, si, i) == ".":
		var fe := _fcb_field(src, si, i + 1, 3, false)
		i = fe[0]
		ext = fe[1]
	if ext != "":
		for k in range(3):
			m.wb(dst + 9 + k, ext.unicode_at(k))
	elif not (al & 8):
		for k in range(3):
			m.wb(dst + 9 + k, 32)
	if result != 0xFF:
		for k in range(11):
			if m.rb(dst + 1 + k) == 0x3F:
				result = 1
	cpu.r[6] = (si + i) & 0xFFFF
	cpu.r[0] = (cpu.r[0] & 0xFF00) | result
	return Machine.DONE


# -------------------------------------------------------------- find file
func _split83(s: String) -> Array:
	var up := s.to_upper()
	var dot := up.find(".")
	var b := up if dot < 0 else up.substr(0, dot)
	var e := "" if dot < 0 else up.substr(dot + 1)
	return [b.substr(0, 8).rpad(8), e.substr(0, 3).rpad(3)]


func match83(pattern: String, name: String) -> bool:
	var p := _split83(pattern)
	var pb: String = p[0]
	var pe: String = p[1]
	var star := pb.find("*")
	if star >= 0:
		pb = pb.substr(0, star) + "?".repeat(8 - star)
	star = pe.find("*")
	if star >= 0:
		pe = pe.substr(0, star) + "?".repeat(3 - star)
	var nn := _split83(name)
	var pat := pb + pe
	var nam: String = nn[0] + nn[1]
	for i in range(11):
		if pat[i] != "?" and pat[i] != nam[i]:
			return false
	return true


func find_first() -> int:
	var spec := read_asciiz(cpu.sr[3], cpu.r[2])
	var attr: int = cpu.r[1]
	var sp := split_path(spec)
	var parts: Array = sp[1]
	var pattern: String = parts[-1] if not parts.is_empty() else "*.*"
	var dirparts := parts.slice(0, maxi(0, parts.size() - 1))
	var dirspec := "%s:\\%s" % [sp[0], "\\".join(PackedStringArray(dirparts))]
	var hostdir := resolve(dirspec, false, true)
	if hostdir == "":
		return err(ERR_NOPATH)
	var entries: Array = []
	var names: Array = []
	for n in DirAccess.get_files_at(hostdir):
		names.append([n, false])
	if attr & 0x10:
		for n in DirAccess.get_directories_at(hostdir):
			names.append([n, true])
	names.sort_custom(func(x, y): return String(x[0]) < String(y[0]))
	for e in names:
		var n: String = e[0]
		var dot := n.find(".")
		var base := n if dot < 0 else n.substr(0, dot)
		var ext := "" if dot < 0 else n.substr(dot + 1)
		if base.length() > 8 or ext.length() > 3 or ext.find(".") >= 0:
			continue
		if match83(pattern, n):
			entries.append([n.to_upper(), join_path(hostdir, n), e[1]])
	find_list = entries
	find_pos = 0
	return find_next()


func find_next() -> int:
	if find_pos >= find_list.size():
		return err(ERR_NOMORE)
	var e: Array = find_list[find_pos]
	find_pos += 1
	var name: String = e[0]
	var full: String = e[1]
	var isdir: bool = e[2]
	var a: int = (int(dta[0]) << 4) + int(dta[1])
	for i in range(21):
		m.wb(a + i, 0)
	m.wb(a + 21, 0x10 if isdir else 0x20)
	m.ww(a + 22, 0x6000)
	m.ww(a + 24, ((1989 - 1980) << 9) | (8 << 5) | 8)
	var size := 0
	if not isdir:
		var fp := FileAccess.open(full, FileAccess.READ)
		if fp:
			size = fp.get_length()
			fp.close()
	m.ww(a + 26, size & 0xFFFF)
	m.ww(a + 28, size >> 16)
	var nb := name.to_ascii_buffer()
	for i in range(13):
		m.wb(a + 30 + i, nb[i] if i < nb.size() and i < 12 else 0)
	return ok(0)
