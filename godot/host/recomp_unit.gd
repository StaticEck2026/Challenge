## Base class of the generated scripts in godot/recomp/ (one per group of
## recompiled functions).  It only holds the shared state the generated code
## works on; the functions themselves are written by tools/recomp/recompile.py.
class_name RecompUnit
extends RefCounted

## CPU registers and flags between functions (CpuState).
var st: CpuState = null
## The host: services, overlay manager, dispatcher (RecompHost; untyped
## because the generated code calls it dynamically).
var host = null
## The program's 1 MB memory (same PackedByteArray as st.mem).
var mem: PackedByteArray = PackedByteArray()
## Parity table for PF.
var par: PackedByteArray = PackedByteArray()


func setup(p_host) -> void:
	host = p_host
	st = p_host.st
	mem = st.mem
	par = st.parity


## Overridden by generated units: resolves references to other units.
func _link(_units: Dictionary) -> void:
	pass
