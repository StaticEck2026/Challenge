## Read-only view of the running program's state for the Modern view.
##
## update(mem) copies what the presentation needs out of the program's 1 MB
## memory: the 80x25 text page, the BIOS video mode, the game variables listed
## in res://ui/state_map.json (score, clock, down, ball spot, teams, rosters)
## and, while a play is drawn in CGA mode 4, the 22 players and the ball,
## converted from the game's scrolled CGA pixel coordinates to field yards.
## It never writes memory and never calls into the program.
##
## Field coordinates (everything the Modern view draws):
## * fx: yards from the LEFT goal line of the game's 80x25 field overview,
##   0..100 (the end zones are -10..0 and 100..110);
## * fy: yards from the top sideline, 0..53.33.
## The game's graphics field is vertical (the offence of the team attacking to
## the right moves up the screen), so the Modern view turns it 90 degrees.
class_name GameState
extends RefCounted

const MAP_PATH := "res://ui/state_map.json"
const TEXT_BASE := 0xB8000
const BDA_MODE := 0x449
const FIELD_W := 53.333
const POS_NAMES := ["OL", "C", "TE", "WR", "RB", "QB", "DL", "LB", "DB", "P", "K"]
## Text-overview geometry: goal line at column 14, 2 yards per column.
const MARKER_GOAL_COL := 14

var vars: Dictionary = {}
var dgroup: int = 0x24F80
var px_per_yard: float = 8.0
var left_sideline_x: float = -54.0
var los_y_plus: float = 233.0
var los_y_minus: float = 169.0
var sprite_centre: float = 4.0
var map_ok: bool = false

# ---- raw screen
var mode: int = 3
var graphics: bool = false
var rows: PackedStringArray = PackedStringArray()
var attrs: PackedByteArray = PackedByteArray()

# ---- teams (1 = home, 2 = visitor, the game's numbering)
var home_short: String = ""
var home_full: String = ""
var away_short: String = ""
var away_full: String = ""

# ---- scoreboard as displayed (parsed from the text screen while it is up,
# kept while the graphics play runs; memory is the fallback)
var sb_valid: bool = false
var quarter: int = 1
var clock_text: String = "15:00"
var play_clock: String = ""
var down: int = 0
var togo_text: String = ""
var yard_text: String = ""
var poss: int = 0
var score_home: int = 0
var score_away: int = 0
var quarters_home: Array = []
var quarters_away: Array = []
var timeouts_home: int = 3
var timeouts_away: int = 3

# ---- coaches: 1 human / 2 computer; keypad 1 right (numeric keypad), -1 left
var coach_home: int = 2
var coach_away: int = 2
var keypad_home: int = 1
var keypad_away: int = -1

# ---- field
var dir_home: int = 1
var dir_away: int = -1
var ball_fx: float = -1.0       # ball on the field overview (marker), yards
var los_fx: float = -1.0        # line of scrimmage
var first_down_fx: float = -1.0
var field_up: bool = false      # the text field overview is on screen
var pbp: PackedStringArray = PackedStringArray()   # play/result lines under the field

# ---- graphics play
var play_active: bool = false
var players: Array = []         # 22 x {fx, fy, team, off, jersey, pos, sx, sy, placed}
var ball: Dictionary = {}       # {fx, fy, sx, sy, carrier, placed}
var play_step: int = 0          # increments whenever any record changes
var play_los: float = -1.0
var cam_x: int = 0
var cam_y: int = 0
var _last_records: PackedInt32Array = PackedInt32Array()
var _was_graphics: bool = false
## The play records as they were in text mode (last read before this
## graphics session), and which records (22 players, the ball) the game has
## written since the session began.  The game keeps the old records while it
## switches to graphics and draws the field (about 1.3 s at 1x), then
## computes the line-up one player at a time; a record it has not written yet
## is stale and is not shown.
var _ref_records: PackedInt32Array = PackedInt32Array()
var _raw_records: PackedInt32Array = PackedInt32Array()
var _placed: Array = []


func _init() -> void:
	load_map(MAP_PATH)


func load_map(path: String) -> void:
	var text := FileAccess.get_file_as_string(path)
	var data = JSON.parse_string(text)
	if data == null:
		return
	vars = data["vars"]
	dgroup = _hex(str(data["dgroup_linear"]))
	var f: Dictionary = data["field"]
	px_per_yard = float(f["px_per_yard"])
	left_sideline_x = float(f["left_sideline_x"])
	los_y_plus = float(f["los_y_attack_plus"])
	los_y_minus = float(f["los_y_attack_minus"])
	sprite_centre = float(f["sprite_centre"])
	map_ok = true


static func _hex(s: String) -> int:
	var t := s.strip_edges().to_lower().trim_prefix("0x")
	var v := 0
	for i in range(t.length()):
		var c := t.unicode_at(i)
		var d := 0
		if c >= 48 and c <= 57:
			d = c - 48
		elif c >= 97 and c <= 102:
			d = c - 87
		v = v * 16 + d
	return v


## Linear address of a mapped variable.
func addr(name: String) -> int:
	return dgroup + _hex(str(vars[name]["off"]))


static func s16(mem: PackedByteArray, a: int) -> int:
	var v: int = mem[a] | (mem[a + 1] << 8)
	return v - 65536 if v >= 32768 else v


func var16(mem: PackedByteArray, name: String) -> int:
	return s16(mem, addr(name))


func cstr(mem: PackedByteArray, name: String, n: int) -> String:
	var a := addr(name)
	var out := ""
	for i in range(n):
		var c: int = mem[a + i]
		if c == 0:
			break
		if c >= 32 and c < 127:
			out += char(c)
	return out.strip_edges()


# ------------------------------------------------------------------ update
func update(mem: PackedByteArray) -> void:
	mode = mem[BDA_MODE]
	graphics = mode == 4 or mode == 5 or mode == 6
	if not graphics:
		_read_text(mem)
	if not map_ok:
		return
	_read_teams(mem)
	coach_home = var16(mem, "coach_home")
	coach_away = var16(mem, "coach_visitor")
	keypad_home = var16(mem, "keypad_home")
	keypad_away = var16(mem, "keypad_visitor")
	dir_home = 1 if var16(mem, "dir_home") >= 0 else -1
	dir_away = 1 if var16(mem, "dir_visitor") >= 0 else -1
	if not graphics:
		if not _parse_scoreboard():
			_scoreboard_from_memory(mem)
		_read_field_overview(mem)
	elif not sb_valid:
		_scoreboard_from_memory(mem)
	if graphics:
		if not _was_graphics:
			# a new graphics session: nothing written yet (unless there is no
			# earlier session to compare with)
			_placed = []
			for i in range(23):
				_placed.append(_ref_records.size() != 69)
		_raw_records = _records(mem)
		_mark_placed()
	else:
		_ref_records = _records(mem)
	play_active = graphics and mode == 4 and _looks_like_play(mem)
	if play_active:
		_read_play(mem)
	elif not graphics:
		_last_records = PackedInt32Array()
	_was_graphics = graphics


func _read_text(mem: PackedByteArray) -> void:
	rows = PackedStringArray()
	attrs.resize(2000)
	for r in range(25):
		var line := ""
		for c in range(80):
			var a := TEXT_BASE + (r * 80 + c) * 2
			line += Video.cp437_char(mem[a])
			attrs[r * 80 + c] = mem[a + 1]
		rows.append(line)


func text_at(r: int, c0: int, n: int) -> String:
	if r < 0 or r >= rows.size():
		return ""
	return rows[r].substr(c0, n)


func screen_text() -> String:
	return "\n".join(rows)


func _read_teams(mem: PackedByteArray) -> void:
	var hs := cstr(mem, "name_home", 15)
	var vs := cstr(mem, "name_visitor", 15)
	if hs != "":
		home_short = hs
		home_full = cstr(mem, "fullname_home", 32)
	if vs != "":
		away_short = vs
		away_full = cstr(mem, "fullname_visitor", 32)


## The game's own scoreboard (rows 0-2 of the field overview), parsed.
func _parse_scoreboard() -> bool:
	if rows.size() < 3 or not rows[0].contains("Timeouts") or not rows[0].contains("Yard:"):
		return false
	var hdr := rows[0]
	var cols := []
	var start := 12
	for ch in ["1", "2", "3", "4", "T"]:
		var p := hdr.find(ch, start)
		cols.append(p)
		start = p + 1
	var top := rows[1]
	var bottom := rows[2]
	# Row 1 is the visitor, row 2 the home team (the game prints "Visitor at Home").
	quarters_away = _row_scores(top, cols)
	quarters_home = _row_scores(bottom, cols)
	score_away = int(quarters_away[4]) if quarters_away[4] >= 0 else 0
	score_home = int(quarters_home[4]) if quarters_home[4] >= 0 else 0
	clock_text = top.substr(0, 8).strip_edges()
	timeouts_away = _int_in(top.substr(48, 6), timeouts_away)
	timeouts_home = _int_in(bottom.substr(48, 6), timeouts_home)
	if top.substr(8, 5).contains("■"):
		poss = 2
	elif bottom.substr(8, 5).contains("■"):
		poss = 1
	yard_text = _after(hdr, "Yard:", 4)
	var d := _after(top, "Down:", 4)
	down = d.to_int() if d.is_valid_int() else 0
	togo_text = _after(bottom, "To Go:", 4)
	play_clock = top.substr(70, 10).strip_edges()
	var q := 1
	for i in range(4):
		if quarters_home[i] >= 0 or quarters_away[i] >= 0:
			q = i + 1
	quarter = q
	sb_valid = true
	return true


static func _row_scores(row: String, cols: Array) -> Array:
	var out := []
	for p in cols:
		var pi: int = p
		var s := row.substr(maxi(pi - 2, 0), 3).strip_edges()
		out.append(s.to_int() if s.is_valid_int() else -1)
	return out


static func _int_in(s: String, dflt: int) -> int:
	var t := s.strip_edges()
	return t.to_int() if t.is_valid_int() else dflt


static func _after(line: String, label: String, n: int) -> String:
	var p := line.find(label)
	if p < 0:
		return ""
	return line.substr(p + label.length(), n).strip_edges()


func _scoreboard_from_memory(mem: PackedByteArray) -> void:
	quarter = clampi(var16(mem, "quarter"), 1, 5)
	clock_text = "%d:%s" % [var16(mem, "clock_min"), str(var16(mem, "clock_sec")).pad_zeros(2)]
	down = var16(mem, "down")
	poss = var16(mem, "poss")
	score_home = var16(mem, "total_home")
	score_away = var16(mem, "total_visitor")
	timeouts_home = var16(mem, "timeouts_home")
	timeouts_away = var16(mem, "timeouts_visitor")
	var spot := var16(mem, "spot")
	if graphics:
		spot = var16(mem, "los")      # spot already holds the result being shown
	var strt := var16(mem, "series_start")
	var d := dir_of(poss)
	var fd := strt + 10 * d
	togo_text = str(absi(fd - spot)) if down > 0 else ""
	yard_text = str(spot if spot <= 50 else 100 - spot)
	if first_down_fx < 0.0 and down > 0:
		first_down_fx = clampf(float(fd), 0.0, 100.0)


func dir_of(team: int) -> int:
	return dir_home if team == 1 else dir_away


## The 80x25 field overview: ball marker, line of scrimmage, first-down line,
## and the play-by-play lines under the field.
func _read_field_overview(mem: PackedByteArray) -> void:
	field_up = rows.size() >= 25 and _has_field_frame()
	pbp = PackedStringArray()
	if not field_up:
		return
	var col := var16(mem, "marker_col")
	if col >= 0 and col < 80:
		ball_fx = float(col - MARKER_GOAL_COL) * 2.0
	var spot := float(var16(mem, "spot"))
	# The overview shows the ball at the marker; between plays the marker is
	# the line of scrimmage (spot can already hold the next result).
	los_fx = ball_fx
	if absf(spot - ball_fx) <= 2.0:
		los_fx = spot
	var p := poss if poss > 0 else var16(mem, "poss")
	var strt := float(var16(mem, "series_start"))
	if down > 0 and togo_text != "":
		var fd := strt + 10.0 * dir_of(p)
		first_down_fx = clampf(fd, 0.0, 100.0)
	else:
		first_down_fx = -1.0
	# the game prints play and result text from column 15 of rows 20-23;
	# row 24 holds the coaches' status words ("Offense selected")
	for r in range(19, 25):
		var s := rows[r].substr(15, 65).strip_edges()
		# (questions get their own card with buttons: not repeated as play text)
		if s != "" and not _is_frame_line(s) and rows[r].substr(0, 15).strip_edges() == "" \
				and not s.contains("(y/n)") and not s.contains("(L,R)"):
			pbp.append(s)


func _has_field_frame() -> bool:
	for r in range(3, 8):
		if rows[r].contains("╔════╤") or rows[r].contains("════╤════"):
			return true
	return false


static func _is_frame_line(s: String) -> bool:
	for ch in ["║", "═", "│", "╔", "╚", "░"]:
		if s.contains(ch):
			return true
	return false


# -------------------------------------------------------------- graphics play
func _looks_like_play(mem: PackedByteArray) -> bool:
	var a := addr("play_records")
	var nonzero := 0
	for i in range(22):
		if s16(mem, a + i * 6) != 0 or s16(mem, a + i * 6 + 2) != 0:
			nonzero += 1
	return nonzero >= 20


## The 23 play records (22 players and the ball): x, y, flags each.
func _records(mem: PackedByteArray) -> PackedInt32Array:
	var a := addr("play_records")
	var recs := PackedInt32Array()
	recs.resize(69)
	for i in range(23):
		recs[i * 3] = s16(mem, a + i * 6)
		recs[i * 3 + 1] = s16(mem, a + i * 6 + 2)
		recs[i * 3 + 2] = s16(mem, a + i * 6 + 4)
	return recs


## A record counts as written once its position differs from the one it had
## in text mode (and is not 0,0); it stays written for the rest of the
## session (the play and its instant replay).
func _mark_placed() -> void:
	for i in range(23):
		if _placed[i]:
			continue
		var x := _raw_records[i * 3]
		var y := _raw_records[i * 3 + 1]
		if (x != 0 or y != 0) and (x != _ref_records[i * 3] or y != _ref_records[i * 3 + 1]):
			_placed[i] = true


## Players the game has written in this graphics session (see _placed).
func placed_count() -> int:
	var n := 0
	for i in range(mini(players.size(), 22)):
		if players[i]["placed"]:
			n += 1
	return n


func _read_play(mem: PackedByteArray) -> void:
	cam_x = s16(mem, addr("cam_x"))
	cam_y = s16(mem, addr("cam_y"))
	var los := float(var16(mem, "los"))
	if not _was_graphics or play_los < 0.0:
		play_los = los
	var off_team := var16(mem, "poss")
	if off_team != 1 and off_team != 2:
		off_team = poss if poss > 0 else 2
	var def_team := 3 - off_team
	var los_y := los_y_plus if dir_of(off_team) > 0 else los_y_minus
	var recs := _raw_records
	if recs != _last_records:
		play_step += 1
		_last_records = recs
	var off_ids := _onfield(mem, off_team)
	var def_ids := _onfield(mem, def_team)
	players = []
	for i in range(22):
		var sx: int = recs[i * 3]
		var sy: int = recs[i * 3 + 1]
		var off := i < 11
		var team := off_team if off else def_team
		var slot := i if off else i - 11
		var info: Dictionary = (off_ids if off else def_ids)[slot]
		var p := {"sx": sx, "sy": sy, "team": team, "off": off,
			"jersey": info["jersey"], "pos": info["pos"], "placed": _placed[i]}
		var f := screen_to_field(sx, sy, los, los_y)
		p["fx"] = f[0]
		p["fy"] = f[1]
		players.append(p)
	var bx: int = recs[66]
	var by: int = recs[67]
	var bf := screen_to_field(bx, by, los, los_y)
	var carrier := -1
	for i in range(22):
		if recs[i * 3] == bx and recs[i * 3 + 1] == by:
			carrier = i
			break
	ball = {"sx": bx, "sy": by, "fx": bf[0], "fy": bf[1], "carrier": carrier, "placed": _placed[22]}
	los_fx = los


## CGA sprite position (top-left, relative to the scrolled field) -> field
## yards [fx, fy].  The inverse is field_to_screen().
func screen_to_field(sx: int, sy: int, los: float, los_y: float) -> Array:
	var wy := float(sy + cam_y) + sprite_centre
	var wx := float(sx - cam_x) + sprite_centre
	var fx := los + (los_y - wy) / px_per_yard
	var fy := (wx - left_sideline_x) / px_per_yard
	return [fx, fy]


func field_to_screen(fx: float, fy: float, los: float, los_y: float) -> Array:
	var wy := los_y - (fx - los) * px_per_yard
	var wx := fy * px_per_yard + left_sideline_x
	return [wx - sprite_centre + cam_x, wy - sprite_centre - cam_y]


## Jersey number and position of the 11 players a team has on the field, in
## the order of the play records.
func _onfield(mem: PackedByteArray, team: int) -> Array:
	var ids := addr("onfield_home" if team == 1 else "onfield_visitor")
	var roster := addr("roster_home" if team == 1 else "roster_visitor")
	var out := []
	for k in range(11):
		var idx: int = mem[ids + k]
		var jersey := 0
		var pos := ""
		if idx < 49:
			jersey = mem[roster + idx * 15]
			var code: int = mem[roster + idx * 15 + 1]
			if code < POS_NAMES.size():
				pos = POS_NAMES[code]
		out.append({"jersey": jersey, "pos": pos})
	return out


## Name of a team by the game's number (1 home, 2 visitor).
func short_name(team: int) -> String:
	return home_short if team == 1 else away_short


func full_name(team: int) -> String:
	var f := home_full if team == 1 else away_full
	return f if f != "" else short_name(team)


## The keyboard side ("L" or "R") of the coach a window belongs to: the team
## named in its title, else the side of the screen it is on (the game opens
## each coach's windows on his keyboard side).
func coach_side(title: String, screen_side: int) -> String:
	for team in [1, 2]:
		var nm := short_name(team)
		if nm != "" and title.contains(nm):
			var kp := keypad_home if team == 1 else keypad_away
			return "R" if kp > 0 else "L"
	return "L" if screen_side == 0 else "R"


## "Human" / "Computer" for the coach on keyboard side "L" or "R", or "".
func coach_label(side: String) -> String:
	for team in [1, 2]:
		var kp := keypad_home if team == 1 else keypad_away
		var c := coach_home if team == 1 else coach_away
		if (kp > 0) == (side == "R") and (c == 1 or c == 2):
			return "Human" if c == 1 else "Computer"
	return ""
