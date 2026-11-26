## Register file and memory of the recompiled program (no instruction decoder).
##
## The statically recompiled code (godot/recomp/*.gd) keeps registers and flags
## in typed locals while a function runs and stores them here at call
## boundaries; the native services (bios.gd, dos.gd) read and write them here.
## Field names follow the old interpreter so the services stay unchanged:
## r[] = AX CX DX BX SP BP SI DI, sr[] = ES CS SS DS, flags as 0/1 ints.
class_name CpuState
extends RefCounted

const ES := 0
const CS := 1
const SS := 2
const DS := 3

## 1 MB + 16 guard bytes.  Shared by reference with every recompiled unit and
## the renderer (Godot packed arrays inside Variants are reference types).
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
var halted: bool = false
## Service calls made so far (the deterministic clock and the logs use it).
var icount: int = 0
var executed: int = 0
## CS:IP of the code that requested the current service (for log messages).
var op_cs: int = 0
var op_ip: int = 0
var parity: PackedByteArray = PackedByteArray()


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


func rw(a: int) -> int:
	a &= 0xFFFFF
	return mem[a] | (mem[(a + 1) & 0xFFFFF] << 8)


func ww(a: int, v: int) -> void:
	a &= 0xFFFFF
	mem[a] = v & 0xFF
	mem[(a + 1) & 0xFFFFF] = (v >> 8) & 0xFF


func push(v: int) -> void:
	var sp: int = (r[4] - 2) & 0xFFFF
	r[4] = sp
	ww((sr[2] << 4) + sp, v)


func pop() -> int:
	var sp: int = r[4]
	var v := rw((sr[2] << 4) + sp)
	r[4] = (sp + 2) & 0xFFFF
	return v


## Snapshot of registers and flags (the native overlay manager saves and
## restores them around the DOS calls it makes).
func save() -> Array:
	return [r.duplicate(), sr.duplicate(), get_flags()]


func restore(s: Array) -> void:
	var rr: PackedInt32Array = s[0]
	var ss: PackedInt32Array = s[1]
	for i in range(8):
		r[i] = rr[i]
	for i in range(4):
		sr[i] = ss[i]
	set_flags(s[2])
