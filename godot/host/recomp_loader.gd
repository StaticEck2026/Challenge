## Loads the generated scripts of a recompiled program on the main thread,
## one per call of step(), so the window stays responsive while Godot compiles
## about 10 MB of GDScript.  (Loading scripts from the program thread would
## also work in principle, but compiling GDScript off the main thread is the
## less-trodden path in Godot 4.3; the program thread only runs code.)
class_name RecompLoader
extends RefCounted

var dir: String = ""
## Instance of the program's recomp_index.gd.
var index = null
var names: Array = []
var scripts: Dictionary = {}
var pos: int = 0
var error: String = ""


## Loads the index; returns an error message or "".
func begin(p_dir: String) -> String:
	dir = p_dir
	var idx = load(dir + "/recomp_index.gd")
	if idx == null:
		error = "cannot load %s/recomp_index.gd" % dir
		return error
	index = idx.new()
	names = index.UNITS
	return ""


## Loads the next unit script.  Returns true when all are loaded (or on error).
func step() -> bool:
	if error != "":
		return true
	if pos < names.size():
		var n: String = names[pos]
		var s = load("%s/%s.gd" % [dir, n])
		if s == null:
			error = "cannot load %s/%s.gd" % [dir, n]
			return true
		scripts[n] = s
		pos += 1
	return pos >= names.size()


func progress() -> String:
	return "loading recompiled code %d/%d" % [pos, names.size()]


## Callable for RecompHost.setup(): a new instance of unit `n`.
func instance(n: String) -> RecompUnit:
	return scripts[n].new()
