## Deterministic key scripts for the smoke mode (main.gd --smoke=...), the
## GDScript twin of tools/refemu/run.py's script language and of the key
## script runner in tools/recomp/lockstep.py:
##
##   plain characters      typed (upper case / symbols with Shift)
##   {Enter} {Esc} {Space} {Down} {F1} ... {Alt-E} {Ctrl-C} {Shift-X}
##   {wait:N}              wait N BIOS ticks
##   {when:TEXT}           wait until TEXT is on the text screen (checked every 4 ticks)
##   {gone:TEXT}           wait until TEXT is gone
##   {shot:NAME}           ask the main thread for a screenshot NAME
##   {click:ACTION}        ask the main thread to click the Modern view's button
##                         with that action ("ui:zoom", "key:R", "pad:R:3")
##
## With block_on_shot (main.gd --keys=), the script waits after a {shot:} or
## {click:} until the main thread has done it (shots_taken); the smoke mode
## does not block, so its timing stays deterministic.
##
## on_tick() runs on the program thread as a Machine tick hook.
class_name KeyScript
extends RefCounted

const LSHIFT := 0x2A
const CTRL := 0x1D
const ALT := 0x38
const NAMED := {
	"Esc": 0x01, "BS": 0x0E, "Tab": 0x0F, "Enter": 0x1C, "Space": 0x39,
	"F1": 0x3B, "F2": 0x3C, "F3": 0x3D, "F4": 0x3E, "F5": 0x3F, "F6": 0x40,
	"F7": 0x41, "F8": 0x42, "F9": 0x43, "F10": 0x44, "F11": 0x57, "F12": 0x58,
	"Home": 0x47, "Up": 0x48, "PgUp": 0x49, "Left": 0x4B, "Center": 0x4C,
	"Right": 0x4D,
	"End": 0x4F, "Down": 0x50, "PgDn": 0x51, "Ins": 0x52, "Del": 0x53,
	"KP-": 0x4A, "KP+": 0x4E, "KP*": 0x37,
}

## [kind, value]: ["key", PackedInt32Array] / ["wait", n] / ["when", text] /
## ["gone", text] / ["shot", name]
var actions: Array = []
var ai: int = 0
var wait_ticks: int = 0
var m: Machine = null
## names of screenshots requested and not yet taken (main thread drains it)
var shot_requests: PackedStringArray = PackedStringArray()
## interactive mode: wait for the main thread after each request (see above)
var block_on_shot: bool = false
## requests the main thread has handled (written by the main thread only)
var shots_taken: int = 0
## [action index, tick] of every {when:} / {gone:} that matched
var when_log: Array = []
var error: String = ""
var chars: Dictionary = {}


func _init(p_machine: Machine, script: String) -> void:
	m = p_machine
	_build_chars()
	actions = parse(script)


func _build_chars() -> void:
	var rows := [[0x02, "1234567890-=", "!@#$%^&*()_+"], [0x10, "qwertyuiop[]", "QWERTYUIOP{}"],
		[0x1E, "asdfghjkl;'`", "ASDFGHJKL:\"~"], [0x2B, "\\zxcvbnm,./", "|ZXCVBNM<>?"]]
	for row in rows:
		var base: int = row[0]
		var norm: String = row[1]
		var shifted: String = row[2]
		for i in range(norm.length()):
			chars[norm[i]] = [base + i, false]
			chars[shifted[i]] = [base + i, true]
	chars[" "] = [0x39, false]


static func key_codes(sc: int, mods: Array) -> PackedInt32Array:
	var out := PackedInt32Array()
	for md in mods:
		out.append(md)
	out.append(sc)
	out.append(sc | 0x80)
	for i in range(mods.size() - 1, -1, -1):
		out.append(int(mods[i]) | 0x80)
	return out


func parse(script: String) -> Array:
	var out: Array = []
	var i := 0
	while i < script.length():
		var c := script[i]
		if c == "{":
			var j := script.find("}", i)
			if j < 0:
				error = "unterminated { in key script"
				return out
			var tok := script.substr(i + 1, j - i - 1)
			i = j + 1
			var colon := tok.find(":")
			if colon > 0:
				var k := tok.substr(0, colon)
				var v := tok.substr(colon + 1)
				if k == "wait":
					out.append(["wait", int(v)])
					continue
				if k in ["when", "gone", "shot", "mark", "click"]:
					out.append([k, v])
					continue
			var mods: Array = []
			var name := tok
			while name.find("-") > 0 and name.substr(0, name.find("-")) in ["Alt", "Ctrl", "Shift"]:
				var md := name.substr(0, name.find("-"))
				name = name.substr(name.find("-") + 1)
				mods.append(ALT if md == "Alt" else (CTRL if md == "Ctrl" else LSHIFT))
			var sc := -1
			if NAMED.has(name):
				sc = NAMED[name]
			elif name.length() == 1 and chars.has(name.to_lower() if _is_alpha(name) else name):
				var e: Array = chars[name.to_lower() if _is_alpha(name) else name]
				sc = e[0]
				if e[1] and not (LSHIFT in mods):
					mods.append(LSHIFT)
			else:
				error = "unknown key token {%s}" % tok
				return out
			out.append(["key", key_codes(sc, mods)])
		else:
			i += 1
			if not chars.has(c):
				error = "cannot type %s" % c
				return out
			var e2: Array = chars[c]
			out.append(["key", key_codes(e2[0], [LSHIFT] if e2[1] else [])])
	return out


static func _is_alpha(s: String) -> bool:
	var u := s.unicode_at(0)
	return (u >= 0x41 and u <= 0x5A) or (u >= 0x61 and u <= 0x7A)


func done() -> bool:
	return ai >= actions.size()


## Machine tick hook (program thread).
func on_tick() -> void:
	if block_on_shot and shots_taken < shot_requests.size():
		return
	while ai < actions.size():
		var a: Array = actions[ai]
		var kind: String = a[0]
		if kind == "wait":
			if wait_ticks == 0:
				wait_ticks = a[1]
			wait_ticks -= 1
			if wait_ticks > 0:
				return
			ai += 1
			continue
		if kind == "when" or kind == "gone":
			if m.ticks % 4:
				return
			var present: bool = Video.text_dump(m).find(a[1]) >= 0
			if present != (kind == "when"):
				return
			when_log.append([ai, m.ticks])
			ai += 1
			continue
		if kind == "key":
			if not m.kbd_queue.is_empty() or not m.bios_kbd_empty():
				return
			m.kbd_queue.append_array(a[1])
			ai += 1
			return
		if kind == "shot" or kind == "click":
			shot_requests.append(a[1] if kind == "shot" else "click:" + str(a[1]))
			if block_on_shot:
				ai += 1
				return
		ai += 1
