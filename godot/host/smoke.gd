## Smoke mode: `godot --headless --path godot -- --smoke=<scenario>` runs a
## recompiled program on a deterministic key script with the real program
## thread, saves screenshots, prints timing and exits non-zero on any error,
## dispatch miss or timeout.  tools/godot_check.sh runs it with an official
## Godot binary; tools/recomp/smoke_test.py runs this same class through
## gd2py here.
##
## Scenarios
##   boot       NFL.EXE /i: team selection, roster load, the game screen
##   seasoncom  SEASON.COM: schedule Dolphins at Bills; writes 89/SEASON.NFL
##   season     NFL.EXE /s on 89/SEASON.NFL (the one seasoncom wrote in the same
##              output folder, else the same schedule written directly): a
##              whole game in extra-extra-fast mode; writes 89/STATS.LOG
##
## Files: the game's data folder is read only; everything the program writes
## goes to <out>/dos_c (with a fixed config.nfl, so a CONFIG.NFL in the data
## folder does not change the script; likewise SEASON.COM's config.sea).
## Screenshots: <out>/<scenario>_<name>.png (+ .txt with the text screen).
class_name Smoke
extends RefCounted

const CONFIG_NFL := "Drive   Color   Delay   Special\r\nd:      1       100     0\r\n"
const SEASON_NFL := "1\r\nBills Dolphins\r\n"
## SEASON.COM's own settings file (team disk drive D:), as it writes it
const CONFIG_SEA := "D\r\nC\r\n"
const TEAM_KEYS := ("{when:home team}{shot:teams}{Enter}{when:visiting team}{Down}{Enter}"
	+ "{when:Choose the coach}c{when:Right keypad}r{when:coach for the Colts}c")
const SCENARIOS := {
	"boot": {"program": "", "args": "/i", "max_ticks": 40000,
		"keys": TEAM_KEYS + "{when:Timeouts}{shot:game}{wait:400}{shot:game_later}"},
	"seasoncom": {"program": "season", "args": "", "max_ticks": 4000, "file": "89/SEASON.NFL",
		"keys": "{when:Help Screen}{shot:help}{Space}{when:HOME team}{shot:home}{Enter}"
			+ "{when:VISITING team}{Down}{Enter}{wait:20}{shot:schedule}{Esc}"},
	"season": {"program": "", "args": "/s", "max_ticks": 400000, "file": "89/STATS.LOG",
		"keys": "{when:Timeouts}{wait:20}{shot:season_game}{Alt-E}"},
}

var name: String = ""
var sc: Dictionary = {}
var out_dir: String = ""
var timeout_sec: float = 600.0
var machine: Machine = null
var bios: BIOS = null
var dos: DOS = null
var host: RecompHost = null
var keys: KeyScript = null
var video: Video = null
var thread: Thread = null
var t_start: int = 0
var frames: int = 0
var frame_usec_sum: int = 0
var frame_usec_max: int = 0
var last_frame: int = 0
var shots: PackedStringArray = PackedStringArray()
var error: String = ""
var exit_code: int = -1
var report: PackedStringArray = PackedStringArray()


static func program_dir(recomp_root: String, scenario: String) -> String:
	var p: String = SCENARIOS[scenario]["program"]
	return recomp_root if p == "" else recomp_root + "/" + p


## Sets the program up (not started).  `index` is an instance of the program's
## recomp_index.gd, `loader` a Callable(unit name) -> RecompUnit.  Returns an
## error message or "".
func setup(scenario: String, data_root: String, recomp_dir: String, p_out: String,
		index, loader: Callable) -> String:
	if not SCENARIOS.has(scenario):
		return "unknown smoke scenario %s (boot, seasoncom, season)" % scenario
	name = scenario
	sc = SCENARIOS[scenario]
	out_dir = p_out
	var blob := ProgramBlob.from_file(recomp_dir + "/program.bin")
	if blob.error != "":
		return "%s/program.bin: %s" % [recomp_dir, blob.error]
	var wroot := out_dir + "/dos_c"
	DirAccess.make_dir_recursive_absolute(wroot + "/89")
	if scenario != "seasoncom":
		_write(wroot + "/CONFIG.NFL", CONFIG_NFL)
	else:
		_write(wroot + "/CONFIG.SEA", CONFIG_SEA)
	if scenario == "season" and not FileAccess.file_exists(wroot + "/89/SEASON.NFL"):
		_write(wroot + "/89/SEASON.NFL", SEASON_NFL)
	for stale in ["89/STATS.LOG", "89/AVGSTATS.LOG"]:
		if scenario == "season" and FileAccess.file_exists(wroot + "/" + stale):
			DirAccess.remove_absolute(wroot + "/" + stale)
	if scenario == "seasoncom" and FileAccess.file_exists(wroot + "/89/SEASON.NFL"):
		DirAccess.remove_absolute(wroot + "/89/SEASON.NFL")
	machine = Machine.new()
	machine.deterministic = true
	machine.polls_per_tick = 64
	bios = BIOS.new(machine, FontCP437.font8(), "", BIOS.DEFAULT_EPOCH, 12 * 3600)
	var pair := [data_root, wroot]
	var drives := {"A": pair, "B": pair, "C": pair,
		"D": [DOS.join_path(data_root, "89"), wroot + "/89"]}
	dos = DOS.new(machine, bios, drives, "C", "")
	var info := dos.install_program(blob, sc["args"])
	if info.is_empty():
		return "the program could not be set up"
	keys = KeyScript.new(machine, sc["keys"])
	if keys.error != "":
		return keys.error
	machine.tick_hooks.append(Callable(keys, "on_tick"))
	host = RecompHost.new()
	host.setup(machine, bios, dos, index, loader)
	if host.error_msg != "":
		return host.error_msg
	video = Video.new()
	return ""


func _write(path: String, text: String) -> void:
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f != null:
		f.store_buffer(text.to_ascii_buffer())
		f.close()


## Starts the program thread.
func start() -> void:
	t_start = Time.get_ticks_usec()
	last_frame = t_start
	thread = Thread.new()
	thread.start(_run)


func _run() -> void:
	host.run()


## Main thread, once per frame: screenshots, completion and failure checks.
## Returns true when the run is over (exit_code is then set).
func step() -> bool:
	var now := Time.get_ticks_usec()
	var ft := now - last_frame
	last_frame = now
	frames += 1
	frame_usec_sum += ft
	frame_usec_max = maxi(frame_usec_max, ft)
	machine.wake()
	_take_shots()
	var elapsed := float(now - t_start) / 1.0e6
	if host.error_msg != "" or host.dispatch_misses > 0:
		return _finish("error: " + host.error_msg)
	if host.finished:
		if machine.exited and sc.has("file"):
			var path: String = out_dir + "/dos_c/" + str(sc["file"])
			if FileAccess.file_exists(path):
				return _finish("")
			return _finish("the program exited without writing " + str(sc["file"]))
		return _finish("the program stopped (exit code %d)" % machine.exit_code)
	if not sc.has("file") and keys.done():
		return _finish("")
	if machine.ticks >= int(sc["max_ticks"]):
		return _finish("did not finish within %d BIOS ticks (script step %d/%d)"
			% [machine.ticks, keys.ai, keys.actions.size()])
	if elapsed > timeout_sec:
		return _finish("timeout after %.0f s (script step %d/%d)"
			% [elapsed, keys.ai, keys.actions.size()])
	return false


func _take_shots() -> void:
	while not keys.shot_requests.is_empty():
		var shot: String = keys.shot_requests[0]
		keys.shot_requests.remove_at(0)
		var base := "%s/%s_%s" % [out_dir, name, shot]
		video.render(machine, 0)
		var img := Image.create_from_data(video.width, video.height, false, Image.FORMAT_RGB8, video.rgb)
		img.save_png(base + ".png")
		_write(base + ".txt", Video.text_dump(machine) + "\n")
		shots.append(base + ".png")


func _finish(err: String) -> bool:
	error = err
	if thread != null and not host.finished:
		machine.request_abort()
	if thread != null:
		thread.wait_to_finish()
		thread = null
	keys.shot_requests.append("final")
	_take_shots()
	var secs := float(Time.get_ticks_usec() - t_start) / 1.0e6
	exit_code = 0 if err == "" else 1
	report.append("[smoke] %s: %s" % [name, "PASS" if err == "" else "FAIL: " + err])
	report.append("[smoke] %.1f s, %d BIOS ticks, %d service calls (%.0f/s), %d dispatched calls, "
		% [secs, machine.ticks, machine.cpu.icount, machine.cpu.icount / maxf(secs, 0.001),
		host.calls_dispatched]
		+ "%d dispatch misses, %d overlay loads, %d timer interrupts, script step %d/%d"
		% [host.dispatch_misses, host.ovl_loads, host.irqs_delivered, keys.ai, keys.actions.size()])
	report.append("[smoke] main thread: %d frames, average %.1f ms, longest %.1f ms" % [
		frames, frame_usec_sum / 1000.0 / maxi(frames, 1), frame_usec_max / 1000.0])
	for s in shots:
		report.append("[smoke] screenshot " + s)
	for line in report:
		print(line)
	return true
