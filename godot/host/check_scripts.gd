## Loads (parses and compiles) every GDScript file of the project and reports
## the ones that fail, with the time each took.  Used by tools/godot_check.sh:
##   godot --headless --path godot --script res://host/check_scripts.gd
## Exit code: 0 if all scripts compile, 1 otherwise.
extends SceneTree


func _init() -> void:
	var files: PackedStringArray = PackedStringArray()
	_collect("res://", files)
	var bad := 0
	var total_usec := 0
	var slowest := ""
	var slowest_usec := 0
	for path in files:
		var t0 := Time.get_ticks_usec()
		var s = ResourceLoader.load(path, "", ResourceLoader.CACHE_MODE_IGNORE)
		var dt := Time.get_ticks_usec() - t0
		total_usec += dt
		if dt > slowest_usec:
			slowest_usec = dt
			slowest = path
		var ok: bool = s != null and s is GDScript and (s as GDScript).can_instantiate()
		if not ok:
			bad += 1
			printerr("[check] FAILED to compile: %s" % path)
	print("[check] %d scripts, %d failed; %.1f s compiling (slowest: %s, %.1f s)" % [
		files.size(), bad, total_usec / 1.0e6, slowest, slowest_usec / 1.0e6])
	quit(1 if bad > 0 else 0)


func _collect(dir: String, out: PackedStringArray) -> void:
	for d in DirAccess.get_directories_at(dir):
		if d.begins_with("."):
			continue
		_collect(dir.path_join(d), out)
	for f in DirAccess.get_files_at(dir):
		if f.ends_with(".gd"):
			out.append(dir.path_join(f))
