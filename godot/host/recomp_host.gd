## Runtime of the statically recompiled NFL.EXE.
##
## * Loads the generated units (godot/recomp/u_*.gd) and the dispatch table
##   (godot/recomp/recomp_index.gd), links them, and runs the program from the
##   C start-up 13F3:0007 (the native re-creation of the PLINK86 start-up
##   comes first).
## * Native PLINK86 overlay manager: ovl_ensure(n) makes overlay n and its
##   parents resident the way 191F:0288 does (same DOS calls on NFL.EXE, same
##   bookkeeping in the overlay table and in the manager's data segment), so
##   memory and DOS state match the original program.
## * ROM services: INT n instructions and the C runtime's int86() (which IRETs
##   into the IVT) reach the F000 stubs; those run the BIOS/DOS services of
##   bios.gd / dos.gd natively and then perform the stub's IRET.
## * dispatch(cs:ip): residual indirect transfers.  The unit is chosen by
##   address and by which overlay is resident.  A missing entry is a hard error
##   naming the address; there is no interpreter to fall back to.
class_name RecompHost
extends RefCounted

const ABORT := -1
## Link-time segments of NFL.EXE v2.01 (see docs/NFL_EXE_ANALYSIS.md).
const LINK_TABLE_SEG := 0x18B4
const LINK_MGR_DATA := 0x1906
const LINK_MGR_SEG := 0x191F
const LINK_RT_SEG := 0x13F3
const LINK_OVL_SEG := 0x196D

var m: Machine = null
var st: CpuState = null
var bios: BIOS = null
var dos: DOS = null
var units: Dictionary = {}
var table: Dictionary = {}
var entry_unit: String = ""
var entry_func: String = ""
var load_seg: int = 0
var tab: int = 0          # linear address of the overlay table
var mds: int = 0          # manager data segment
var md: int = 0           # its linear address
var mgr_cs: int = 0
var ovl_lo: int = 0       # first linear address of the overlay area
var n_ovl: int = 0
var error_msg: String = ""
var finished: bool = false
var result: int = 0
var dispatch_misses: int = 0
var calls_dispatched: int = 0
var ovl_loads: int = 0
var unwinds: int = 0
## How many unwinds to log (diagnostics).
var log_unwinds: int = 0
var last_reloc_off: int = 0


func setup(p_machine: Machine, p_bios: BIOS, p_dos: DOS, index, loader: Callable) -> void:
	m = p_machine
	st = m.cpu
	bios = p_bios
	dos = p_dos
	load_seg = index.LOAD_SEG
	entry_unit = index.ENTRY_UNIT
	entry_func = index.ENTRY_FUNC
	for name in index.UNITS:
		var u = loader.call(name)
		u.setup(self)
		units[name] = u
	for name in units:
		units[name]._link(units)
	# key -> Callable of the recompiled function
	var raw: Dictionary = index.dispatch_table()
	for k in raw:
		var e: Array = raw[k]
		table[k] = Callable(units[e[0]], e[1])
	tab = (LINK_TABLE_SEG + load_seg) << 4
	mds = LINK_MGR_DATA + load_seg
	md = mds << 4
	mgr_cs = LINK_MGR_SEG + load_seg
	ovl_lo = (LINK_OVL_SEG + load_seg) << 4
	n_ovl = 0
	while m.rw(_rec(n_ovl + 1)) != 0xFFFF and n_ovl < 64:
		n_ovl += 1


## Program thread body: start-up, then the C start-up code.  Returns the exit code.
func run() -> int:
	if not startup():
		finished = true
		return -1
	var u = units[entry_unit]
	var t: int = u.call(entry_func)
	finished = true
	if m.exited:
		result = m.exit_code
		return result
	if t != ABORT:
		fatal(t, "C start-up returned")
	return -1


# ---------------------------------------------------------------- helpers
func hexaddr(t: int) -> String:
	return "%04X:%04X" % [(t >> 16) & 0xFFFF, t & 0xFFFF]


func fatal(t: int, msg: String) -> int:
	if error_msg == "":
		error_msg = "%s at %s" % [msg, hexaddr(t)]
		m.log_msg("[recomp] FATAL: " + error_msg)
	m.abort = true
	return ABORT


## A callee returned to an address other than the one after its call:
## propagate it upward (stack unwinding: an outer frame expects it).
## The first few are logged for diagnosis.
func unwind(t: int, site: int) -> int:
	if t != ABORT:
		unwinds += 1
		if log_unwinds > 0:
			log_unwinds -= 1
			m.log_msg("[recomp] unwind: return to %s seen at call site %s" % [hexaddr(t), hexaddr(site)])
	return t


## Called at loop back-edges (every few thousand): true = stop the program.
func poll() -> bool:
	if not m.deterministic:
		m.sync_time()
	return m.abort or m.exited


func port_in(port: int, w: int) -> int:
	return m.port_in(port, w)


func port_out(port: int, v: int, w: int) -> void:
	m.port_out(port, v, w)


## DIV/IDIV/AAM overflow: INT 0 (DOS prints "Divide overflow" and exits).
func div_fault(t: int) -> int:
	st.op_cs = (t >> 16) & 0xFFFF
	st.op_ip = t & 0xFFFF
	dos.int00()
	return ABORT


# --------------------------------------------------------------- dispatch
## Unit number (0 = root, n = overlay n) that holds code at linear address lin.
func unit_at(lin: int) -> int:
	if lin < ovl_lo:
		return 0
	for n in range(1, n_ovl + 1):
		var rec := _rec(n)
		if (m.rw(rec) & 0x8000) == 0:
			continue
		var s0: int = m.rw(rec + 4) << 4
		var s1: int = m.rw(rec + 6) << 4
		if lin >= s0 and lin < s1:
			return n
	return -1


## Call the recompiled function at cs:ip (packed (cs << 16) | ip).
func dispatch(t: int) -> int:
	var cs := (t >> 16) & 0xFFFF
	var ip := t & 0xFFFF
	if cs == Machine.ROM_SEG:
		return iret_to_rom(t)
	var lin := ((cs << 4) + ip) & 0xFFFFF
	var u := unit_at(lin)
	if u < 0:
		dispatch_misses += 1
		return fatal(t, "dispatch miss (no resident overlay holds this address)")
	var key := (u << 40) | (cs << 16) | ip
	if not table.has(key):
		dispatch_misses += 1
		return fatal(t, "dispatch miss (no recompiled function for unit %d)" % u)
	calls_dispatched += 1
	var fn: Callable = table[key]
	return fn.call()


# ------------------------------------------------------------ ROM services
## The program executed INT n: run the handler the IVT points to.
## Returns the address the handler's IRET popped.
func do_int(n: int) -> int:
	var off := m.rw(n * 4)
	var seg := m.rw(n * 4 + 2)
	if seg == Machine.ROM_SEG and off == Machine.STUB_BASE + n * 4:
		return rom_stub(n)
	return dispatch((seg << 16) | off)


## IRET (or RETF) into a ROM stub: int86() does this.  Runs the service, then
## continues where the stub's IRET goes.
func iret_to_rom(t: int) -> int:
	var off := t & 0xFFFF
	if off < Machine.STUB_BASE or off >= Machine.STUB_BASE + 0x400 or (off & 3) != 0:
		return fatal(t, "jump into ROM outside the service stubs")
	var r := rom_stub((off - Machine.STUB_BASE) >> 2)
	if r < 0:
		return r
	return dispatch(r)


## F000:E000+4n = `F1 nn CF`: service n, then IRET.
func rom_stub(n: int) -> int:
	var sv_cs: int = st.sr[1]
	st.sr[1] = Machine.ROM_SEG
	st.op_cs = Machine.ROM_SEG
	st.op_ip = Machine.STUB_BASE + n * 4
	var res := m.service(n)
	st.sr[1] = sv_cs
	if res != Machine.DONE:
		return ABORT
	var ip := st.pop()
	var cs := st.pop()
	st.set_flags(st.pop())
	return (cs << 16) | ip


# ------------------------------------------------- native PLINK86 manager
func _rec(n: int) -> int:
	return tab + 4 + 16 * (n - 1)


## A DOS call made by the overlay manager (INT 21h from 191F:xxxx).  Pushes
## the IRET frame the real INT would, so the service sees the same stack.
func _mgr_int21(ax: int, bx: int, cx: int, dx: int, ds: int, ret_ip: int) -> bool:
	st.r[0] = ax & 0xFFFF
	st.r[3] = bx & 0xFFFF
	st.r[1] = cx & 0xFFFF
	st.r[2] = dx & 0xFFFF
	st.sr[3] = ds & 0xFFFF
	st.push(st.get_flags())
	st.push(mgr_cs)
	st.push(ret_ip)
	st.iflag = 0
	var res := rom_stub(0x21)
	if res < 0:
		return true
	return st.cf != 0


## 191F:0288 for overlay n (AH=0: abort the program on failure).
func ovl_ensure(n: int) -> void:
	var rec := _rec(n)
	if (m.rw(rec) & 0x8000) != 0:
		var p: int = m.rw(rec) & 0x3FFF
		if p == 0 or (m.rw(_rec(p)) & 0x8000) != 0:
			return
	var saved := st.save()
	# the stub (191F:02D1) and 0288 run on the caller's stack below its frame
	st.r[4] = (st.r[4] - 0x28) & 0xFFFF
	_load_chain(n)
	st.restore(saved)


func _load_chain(n: int) -> bool:
	m.wb(md + 0x94, 0)
	var cx := n
	while true:
		var rec := _rec(cx)
		if (m.rw(rec) & 0x8000) == 0:
			_clear_overlaps(cx)
			if not _load(cx):
				fatal((mgr_cs << 16) | 0x02C8, "overlay %d could not be loaded" % cx)
				return false
		cx = m.rw(rec) & 0x3FFF
		if cx == 0:
			break
	return true


## 191F:0000: clear the 'loaded' bit of every overlay overlapping overlay n.
func _clear_overlaps(n: int) -> void:
	var mine := _rec(n)
	var i := 1
	while true:
		var rec := _rec(i)
		var flags := m.rw(rec)
		if flags == 0xFFFF:
			break
		var o_start := m.rw(rec + 4)
		var o_end := m.rw(rec + 6)
		var my_start := m.rw(mine + 4)
		var my_end := m.rw(mine + 6)
		var overlap := false
		if my_start >= o_start:
			overlap = my_start < o_end
		else:
			overlap = o_start < my_end
		if overlap:
			m.ww(rec, flags & 0x7FFF)
		i += 1


## 191F:01C2: read overlay n from NFL.EXE and apply its fixups.
func _load(n: int) -> bool:
	var rec := _rec(n)
	ovl_loads += 1
	var v_off := m.rw(0x90)
	var v_seg := m.rw(0x92)
	m.ww(0x90, 0x0039)
	m.ww(0x92, mgr_cs)
	if not _open(rec):
		return false
	var nrel := m.rw(rec + 2)
	if _seek(rec, (nrel + 3) >> 2):
		return false
	var paras := m.rw(rec + 0x0E)
	var span := (m.rw(rec + 6) - m.rw(rec + 4)) & 0xFFFF
	if span < paras:
		paras = span
	if not _read(m.rw(rec + 4), 0, paras):
		return false
	if _seek(rec, 0):
		return false
	var base := m.rw(tab)
	var cnt := nrel
	var left := 0
	var si := 0x0E
	var last_off := 0
	while cnt > 0:
		if left == 0:
			var k := mini(cnt, 32)
			if not _read(mds, 0x0E, (k + 3) >> 2):
				return false
			left = k
			si = 0x0E
		left -= 1
		var off := m.rw(md + si)
		var seg := m.rw(md + si + 2)
		last_off = off
		if off != 0 or seg != 0:
			var a := ((((seg + base) & 0xFFFF) << 4) + off) & 0xFFFFF
			m.ww(a, (m.rw(a) + base) & 0xFFFF)
		si += 4
		cnt -= 1
	m.ww(rec, m.rw(rec) | 0x8000)
	m.ww(0x90, v_off)
	m.ww(0x92, v_seg)
	last_reloc_off = last_off
	return true



## 191F:0043 (AL=0): open the overlay's file unless it is already open.
func _open(rec: int) -> bool:
	var name_off := m.rw(rec + 8)
	var cur := m.rw(md + 0x92)
	if cur == name_off:
		return true
	if cur != 0 and m.rb(md + 0x0A) != 0:
		_mgr_int21(0x3E00, m.rw(md + 0x0C), 0, 0, mds, 0x0067)
	m.ww(md + 0x92, name_off)
	var i := 0
	while i < 64:
		var c := m.rb(tab + name_off + i)
		m.wb(md + 0xC7 + i, c)
		i += 1
		if c == 0:
			break
	# 191F:0499 with AL=0 (first try: the name as is, in the current directory)
	m.wb(md + 0xC5, 0)
	m.wb(md + 0xC6, 0)
	m.ww(md + 0xBF, m.rw(md + 0xBD))
	if _mgr_int21(0x3D00 | m.rb(md + 0x0B), 0, 0, 0xC7, mds, 0x00CA):
		fatal((mgr_cs << 16) | 0x00C8, "cannot open the program file for overlays")
		return false
	m.ww(md + 0x0C, st.r[0])
	return true


## 191F:00DA: seek to the overlay's file position + `paras` paragraphs.
## Returns true on error.
func _seek(rec: int, paras: int) -> bool:
	var lo := m.rw(rec + 0x0A) + paras
	var hi := m.rw(rec + 0x0C) + (lo >> 16)
	lo &= 0xFFFF
	var pos := ((hi << 16) | lo) << 4
	return _mgr_int21(0x4200, m.rw(md + 0x0C), (pos >> 16) & 0xFFFF, pos & 0xFFFF, mds, 0x010F)


## 191F:0110: read `paras` paragraphs to seg:off in chunks of FFF0h bytes.
func _read(seg: int, off: int, paras: int) -> bool:
	var total := paras * 16
	var s := seg
	while total > 0:
		var n := mini(total, 0xFFF0)
		if _mgr_int21(0x3F00, m.rw(md + 0x0C), n, off, s, 0x0166):
			return false
		if st.r[0] != n:
			return false
		total -= n
		s = (s + 0xFFF) & 0xFFFF
	return true


## 191F:02FB: the manager's start-up, re-created natively.  Leaves the
## registers exactly as its `jmp far 13F3:0007` does.
func startup() -> bool:
	var ax0: int = st.r[0]
	var sp0: int = st.r[4]
	var ss0: int = st.sr[2]
	var es0: int = st.sr[0]
	var ds0: int = st.sr[3]
	# private stack, then push si(=ss) cx(=sp) ax ds es
	st.sr[2] = m.rw(tab + 2)
	st.r[4] = 0x100
	st.iflag = 1
	st.push(ss0)
	st.push(sp0)
	st.push(ax0)
	st.push(ds0)
	st.push(es0)
	m.ww((mgr_cs << 4) + 0x286, es0)
	st.sr[0] = es0
	# 191F:03CC: DOS version, PATH= in the environment
	st.r[4] = (st.r[4] - 2) & 0xFFFF
	_mgr_int21(0x3000, 0, 0, 0, mds, 0x03D0)
	var bl := 0
	if (st.r[0] & 0xFF) >= 2:
		_find_path(es0)
		bl = 0xFF
	m.wb(md + 0xBA, bl)
	st.r[4] = (st.r[4] + 2) & 0xFFFF
	m.ww(tab, (mgr_cs - m.rw(tab)) & 0xFFFF)
	_mgr_int21(0x3000, 0, 0, 0, mds, 0x0338)
	bl = 0
	if (st.r[0] & 0xFF) >= 2:
		st.r[5] = 0x8A
		_mgr_int21(0xEF00, 0, 0, 0, mds, 0x0347)
		m.wb(md + 0x0B, 0x30 if st.r[3] != 0 else 0)
		bl = 0xFF
	m.wb(md + 0x0A, bl)
	var n := 1
	var bp := st.r[5]
	while m.rw(_rec(n)) != 0xFFFF:
		if (m.rw(_rec(n)) & 0x4000) != 0:
			var sv_sp: int = st.r[4]
			st.r[4] = (sv_sp - 0x10) & 0xFFFF
			if not _load_chain(n):
				return false
			st.r[4] = sv_sp
			bp = last_reloc_off
		n += 1
	# 191F:037F: pop es ds ax cx bx / mov sp,cx / mov ss,bx / jmp far 13F3:0007
	st.r[0] = ax0
	st.r[1] = sp0
	st.r[2] = m.rw(tab)
	st.r[3] = ss0
	st.r[4] = sp0
	st.r[5] = bp
	st.r[6] = 0x90
	st.r[7] = 16 * (n - 1)
	st.sr[0] = es0
	st.sr[1] = LINK_RT_SEG + load_seg
	st.sr[2] = ss0
	st.sr[3] = ds0
	st.set_flags(0xF256)
	return true


## 191F:0397: find "PATH" + '=' in the environment, store the offset of its value.
func _find_path(psp: int) -> void:
	var env := m.rw((psp << 4) + 0x2C)
	m.ww(md + 0xBB, env)
	var e := env << 4
	var di := 0
	while m.rb(e + di) != 0:
		# repe cmpsb with "PATH" (DS:00C1), CX=4
		var k := 0
		var equal := true
		while k < 4:
			var c := m.rb(e + di + k)
			k += 1
			if c != m.rb(md + 0xC1 + k - 1):
				equal = false
				break
		di += k
		if equal:
			while m.rb(e + di) == 0x20:
				di += 1
			if m.rb(e + di) == 0x3D:
				di += 1
				while m.rb(e + di) == 0x20:
					di += 1
				m.ww(md + 0xBD, di)
				return
		# scasb for the terminating NUL
		while true:
			var c2 := m.rb(e + di)
			di += 1
			if c2 == 0:
				break
