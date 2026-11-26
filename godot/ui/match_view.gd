## Modern match view: the whole field, top-down and horizontal, with the
## players as numbered team-coloured discs, the ball, the line of scrimmage
## (blue) and the first-down line (yellow), the scoreboard pill, the top
## toolbar and the bottom info strip.  Used for the pre-snap field overview
## (text mode), for plays and for the instant replay (CGA mode 4).
##
## Pure layout: build() appends to a DrawList from a GameState and a small
## `ui` dictionary from the Godot view (toolbar labels, interpolated
## positions).  It never touches the program.
class_name MatchView
extends RefCounted

## field.svg geometry (art units): 10 per yard, end line at x = 30.
const ART_W := 1260.0
const ART_H := 593.33
const ART_X0 := 30.0
const ART_Y0 := 30.0
const UPY := 10.0
const FIELD_W := 53.333
const BAR_H := 52.0
const STRIP_H := 108.0
const DISC_R := 0.9          # yards
const ZOOM := 1.8            # camera "Zoom" during plays

var art: UiArt = null
var kits: Array = []          # [home kit, away kit] (UiTheme.match_colors)
var fx0: float = 0.0           # canvas position of the field art
var fy0: float = 0.0
var fs: float = 1.0            # canvas px per art unit


func _init(p_art: UiArt = null) -> void:
	art = p_art if p_art != null else UiArt.new()


# ------------------------------------------------------------------ geometry
## Fits the field art into the area under the toolbar and above the strip.
## zoom > 1 enlarges it around the field point (zx, zy) (camera "Zoom").
func layout_field(top: float, bottom: float, zoom: float = 1.0, zx: float = 50.0,
		zy: float = 26.67) -> void:
	var aw := UiTheme.W - 80.0
	var ah := bottom - top
	fs = minf(aw / ART_W, ah / ART_H)
	fx0 = (UiTheme.W - ART_W * fs) * 0.5
	fy0 = top + (ah - ART_H * fs) * 0.5
	if zoom <= 1.0:
		return
	fs *= zoom
	var px := (ART_X0 + (zx + 10.0) * UPY) * fs
	var py := (ART_Y0 + zy * UPY) * fs
	fx0 = clampf(UiTheme.W * 0.5 - px, UiTheme.W - ART_W * fs, 0.0)
	fy0 = clampf(top + ah * 0.5 - py, bottom - ART_H * fs, top)


## Field yards (fx from the left goal line, fy from the top sideline) to canvas.
func cx(fx: float) -> float:
	return fx0 + (ART_X0 + (fx + 10.0) * UPY) * fs


func cy(fy: float) -> float:
	return fy0 + (ART_Y0 + fy * UPY) * fs


func yard_px() -> float:
	return UPY * fs


## The team's own colour (end zones, scoreboard blocks).
func team_color(team: int) -> String:
	var k: Dictionary = kits[0] if team == 1 else kits[1]
	return k["primary"]


## The team's disc style this match (the visitor may wear white).
func team_kit(team: int) -> Dictionary:
	return kits[0] if team == 1 else kits[1]


func team_code(gs: GameState, team: int) -> String:
	var s := gs.short_name(team)
	if s == "":
		return "HOME" if team == 1 else "AWAY"
	return UiTheme.abbrev(s, gs.short_name(3 - team))


# --------------------------------------------------------------------- build
## ui keys: view_label, speed_label, paused, sound, players (22 x [fx, fy]
## interpolated, optional), ball ([fx, fy], optional), marker_fx (optional),
## overlay_top (bool: windows cover the field).
func build(dl: DrawList, gs: GameState, ui: Dictionary) -> void:
	kits = UiTheme.match_colors(gs.home_short, gs.away_short)
	dl.rect(0.0, 0.0, UiTheme.W, UiTheme.H, UiTheme.BG)
	var zoom := 1.0
	var cam: Array = ui.get("camera", [])
	if ui.get("zoom", false) and gs.play_active and cam.size() == 2:
		zoom = ZOOM
	if zoom > 1.0:
		layout_field(BAR_H, UiTheme.H - STRIP_H, zoom, cam[0], cam[1])
	else:
		layout_field(BAR_H + 78.0, UiTheme.H - STRIP_H - 14.0)
	_field(dl, gs)
	_lines(dl, gs)
	if gs.play_active and gs.players.size() == 22:
		_players(dl, gs, ui)
	else:
		_ball_marker(dl, gs, ui)
	toolbar(dl, ui)
	scoreboard(dl, gs)
	_strip(dl, gs)


func _left_team(gs: GameState) -> int:
	# The team attacking to the right defends the left end zone.
	return 1 if gs.dir_home > 0 else 2


func _field(dl: DrawList, gs: GameState) -> void:
	var lt := _left_team(gs)
	var rt := 3 - lt
	var lc := team_color(lt)
	var rc := team_color(rt)
	dl.art("field", fx0, fy0, ART_W * fs, ART_H * fs, {"left": lc, "right": rc})
	# End-zone names, reading towards the field.
	var ez := 10.0 * yard_px()
	var mid := cy(FIELD_W * 0.5)
	var size := minf(ez * 0.46, 64.0)
	var ln := gs.full_name(lt).to_upper()
	var rn := gs.full_name(rt).to_upper()
	if gs.short_name(lt) != "":
		ln = gs.short_name(lt).to_upper()
	if gs.short_name(rt) != "":
		rn = gs.short_name(rt).to_upper()
	dl.text(cx(-5.0) + size * DrawList.CAP * 0.5, mid, ln, size, UiTheme.on_color(lc) + "e6", "c",
		true, -90.0)
	dl.text(cx(105.0) - size * DrawList.CAP * 0.5, mid, rn, size, UiTheme.on_color(rc) + "e6", "c",
		true, 90.0)
	# Yard numbers: bottom ones upright, top ones upside down (as painted).
	var ns := yard_px() * 2.0
	for k in range(1, 10):
		var yd := k * 10
		var label := str(mini(yd, 100 - yd))
		var x := cx(float(yd))
		dl.text(x, cy(FIELD_W - 9.0), label, ns, "#f4f7f1dd", "c", true)
		dl.text(x, cy(9.0), label, ns, "#f4f7f1dd", "c", true, 180.0)
		if yd != 50:
			var d := -1.0 if yd < 50 else 1.0
			var ax := x + d * (dl.measure(label, ns, true) * 0.5 + yard_px() * 0.9)
			_arrow(dl, ax, cy(FIELD_W - 9.0) - ns * 0.36, d, yard_px() * 0.42)
			_arrow(dl, ax, cy(9.0) + ns * 0.36, d, yard_px() * 0.42)


func _arrow(dl: DrawList, x: float, y: float, d: float, s: float) -> void:
	dl.poly([x + d * s, y, x - d * s * 0.6, y - s * 0.8, x - d * s * 0.6, y + s * 0.8], "#f4f7f1cc")


func _lines(dl: DrawList, gs: GameState) -> void:
	var top := cy(0.0)
	var h := cy(FIELD_W) - top
	var w := yard_px() * 0.75
	var p := {"H": h / w * 16.0}
	if gs.first_down_fx > 0.0 and gs.first_down_fx < 100.0:
		dl.art("line_first_down", cx(gs.first_down_fx) - w * 0.5, top, w, h, p)
	if gs.los_fx >= 0.0 and gs.los_fx <= 100.0:
		dl.art("line_scrimmage", cx(gs.los_fx) - w * 0.5, top, w, h, p)


func _ball_marker(dl: DrawList, gs: GameState, ui: Dictionary) -> void:
	var bx: float = gs.ball_fx
	if gs.los_fx >= 0.0 and absf(gs.ball_fx - gs.los_fx) <= 2.0:
		bx = gs.los_fx
	bx = ui.get("marker_fx", bx)
	if bx < -10.0 or bx > 110.0 or not gs.field_up:
		return
	var by := FIELD_W * 0.5
	_ball(dl, cx(bx), cy(by), 1.0)


func _ball(dl: DrawList, x: float, y: float, k: float) -> void:
	var bw := yard_px() * 1.5 * k
	var bh := bw * 26.0 / 40.0
	dl.art("ball", x - bw * 0.5, y - bh * 0.5, bw, bh)


func _players(dl: DrawList, gs: GameState, ui: Dictionary) -> void:
	var pos: Array = ui.get("players", [])
	var r := DISC_R * yard_px()
	var carrier: int = gs.ball.get("carrier", -1)
	# defence first, so the offence (and the ball carrier) sit on top
	var order := []
	for i in range(11, 22):
		order.append(i)
	for i in range(11):
		if i != carrier:
			order.append(i)
	if carrier >= 0:
		order.append(carrier)
	for i in order:
		var p: Dictionary = gs.players[i]
		var fx: float = p["fx"]
		var fy: float = p["fy"]
		if pos.size() == 22:
			fx = pos[i][0]
			fy = pos[i][1]
		var x := cx(fx)
		var y := cy(fy)
		var team: int = p["team"]
		var k := team_kit(team)
		if i == carrier:
			dl.art("token_ring", x - r * 1.45, y - r * 1.45, r * 2.9, r * 2.9, {"color": UiTheme.WARN})
		dl.art("token", x - r * 32.0 / 27.0, y - r * 32.0 / 27.0, r * 64.0 / 27.0, r * 64.0 / 27.0,
			{"fill": k["fill"], "stroke": k["rim"]})
		var label := str(p["jersey"]) if p["jersey"] > 0 else str(p["pos"])
		var fsz := r * (1.02 if label.length() <= 2 else 0.8)
		dl.text_mid(x, y, label, fsz, k["text"], "c", true)
	var bpos: Array = ui.get("ball", [])
	var bfx: float = gs.ball.get("fx", -100.0)
	var bfy: float = gs.ball.get("fy", 0.0)
	if bpos.size() == 2:
		bfx = bpos[0]
		bfy = bpos[1]
	if carrier >= 0:
		_ball(dl, cx(bfx) + r * 0.8, cy(bfy) + r * 0.75, 0.62)
	elif bfx > -20.0:
		_ball(dl, cx(bfx), cy(bfy), 0.8)


# ------------------------------------------------------------------- chrome
## The slim dark toolbar: app mark, then View / Speed / Pause / Sound /
## Screenshot / Fullscreen buttons (hotspots "ui:...").
func toolbar(dl: DrawList, ui: Dictionary) -> void:
	dl.rect(0.0, 0.0, UiTheme.W, BAR_H, UiTheme.BAR)
	dl.rect(0.0, BAR_H - 1.0, UiTheme.W, 1.0, UiTheme.BAR_LINE)
	dl.art("mark", 16.0, 10.0, 32.0, 32.0)
	dl.text_mid(58.0, BAR_H * 0.5, "NFL Challenge", 18.0, UiTheme.TEXT, "l", true)
	var sub_x := 58.0 + dl.measure("NFL Challenge", 18.0, true) + 12.0
	dl.text_mid(sub_x, BAR_H * 0.5, str(ui.get("subtitle", "")), 15.0, UiTheme.TEXT_3)
	var x := UiTheme.W - 16.0
	var paused: bool = ui.get("paused", false)
	var buttons := [
		["icon_fullscreen", "", "ui:fullscreen"],
		["icon_menu", "", "ui:more"],
		["icon_zoom", "Zoom" if ui.get("zoom", false) else "Full field", "ui:zoom"],
		["icon_camera", "", "ui:screenshot"],
		["icon_sound", "On" if ui.get("sound", true) else "Off", "ui:sound"],
		["icon_play" if paused else "icon_pause", "Resume" if paused else "Pause", "ui:pause"],
		["icon_speed", str(ui.get("speed_label", "1x")), "ui:speed"],
		["icon_view", str(ui.get("view_label", "Modern")), "ui:view"],
	]
	for b in buttons:
		var w := bar_button(dl, x, b[0], b[1], b[2], true)
		x -= w + 8.0


## A toolbar button ending at x (right aligned) or starting at x. Returns width.
func bar_button(dl: DrawList, x: float, icon: String, label: String, action: String,
		right: bool) -> float:
	var h := 34.0
	var w := 40.0 if label == "" else 30.0 + 16.0 + dl.measure(label, 15.0, true) + 8.0
	var x0 := x - w if right else x
	var y0 := (BAR_H - h) * 0.5
	var chrome := _chrome(w, h, 8.0, {"fill": UiTheme.CARD, "line": UiTheme.CARD_LINE})
	dl.art("button", x0, y0, w, h, chrome)
	dl.art(icon, x0 + 11.0, y0 + 8.0, 18.0, 18.0, {"color": UiTheme.TEXT_2})
	if label != "":
		dl.text_mid(x0 + 36.0, y0 + h * 0.5, label, 15.0, UiTheme.TEXT, "l", true)
	dl.hot(x0, y0, w, h, action)
	return w


## Size placeholders for the chrome art (card, panel, button, scoreboard).
static func _chrome(w: float, h: float, r: float, extra: Dictionary) -> Dictionary:
	var p := {"W": w, "H": h, "R": r, "W1": w - 1.5, "H1": h - 1.5}
	p.merge(extra, true)
	return p


## The scoreboard pill: [visitor code | score]  quarter  clock  [score | home code],
## with possession dots and time-outs, and down & distance underneath.
func scoreboard(dl: DrawList, gs: GameState) -> void:
	var h := 56.0
	var bw := 96.0
	var sw := 96.0
	var mw := 168.0
	var w := (bw + sw) * 2.0 + mw
	var x0 := (UiTheme.W - w) * 0.5
	var y0 := BAR_H + 10.0
	var r := h * 0.5
	var away := team_color(2)
	var home := team_color(1)
	dl.art("scoreboard", x0, y0, w, h, {"W": w, "H": h, "R": r, "BW": bw, "BW2": bw + r,
		"XR": w - bw - r, "MW": w - 2.0 * bw, "W1": w - 2.0, "H1": h - 2.0, "left": away,
		"right": home, "SL": bw + sw, "SR": w - bw - sw, "SY": 12.0, "SH": h - 24.0})
	var mid := y0 + h * 0.5
	var on_away := UiTheme.on_color(away)
	var on_home := UiTheme.on_color(home)
	dl.text_mid(x0 + bw * 0.5 + 6.0, mid, team_code(gs, 2), 22.0, on_away, "c", true)
	dl.text_mid(x0 + w - bw * 0.5 - 6.0, mid, team_code(gs, 1), 22.0, on_home, "c", true)
	dl.text_mid(x0 + bw + sw * 0.5 + 8.0, mid, str(gs.score_away), 28.0, UiTheme.TEXT, "c", true)
	dl.text_mid(x0 + w - bw - sw * 0.5 - 8.0, mid, str(gs.score_home), 28.0, UiTheme.TEXT, "c", true)
	var q := quarter_label(gs.quarter)
	dl.text_mid(x0 + w * 0.5, mid - 10.0, q, 13.0, UiTheme.TEXT_3, "c", true)
	dl.text_mid(x0 + w * 0.5, mid + 9.0, gs.clock_text, 22.0, UiTheme.TEXT, "c", true)
	# possession: a small ball next to the score of the team with the ball
	if gs.poss == 2 or gs.poss == 1:
		var px := x0 + bw + 17.0 if gs.poss == 2 else x0 + w - bw - 17.0
		var bw2 := 22.0
		dl.art("ball", px - bw2 * 0.5, mid - bw2 * 13.0 / 40.0, bw2, bw2 * 26.0 / 40.0)
	# time-outs: three dots under each team block
	_timeouts(dl, x0 + bw * 0.5 + 6.0, y0 + h - 9.0, gs.timeouts_away, UiTheme.on_color(away))
	_timeouts(dl, x0 + w - bw * 0.5 - 6.0, y0 + h - 9.0, gs.timeouts_home, UiTheme.on_color(home))
	# down & distance tab under the pill
	var dd := down_distance(gs)
	if dd != "":
		var tw := dl.measure(dd, 15.0, true) + 36.0
		var ty := y0 + h - 4.0
		dl.rrect((UiTheme.W - tw) * 0.5, ty, tw, 30.0, 10.0, "#121821", 1.0, "#ffffff1a")
		dl.rect((UiTheme.W - tw) * 0.5 + 1.0, ty + 1.0, tw - 2.0, 10.0, "#121821")
		dl.text_mid(UiTheme.W * 0.5, ty + 16.0, dd, 15.0, UiTheme.WARN, "c", true)


func _timeouts(dl: DrawList, x: float, y: float, n: int, color: String) -> void:
	for i in range(3):
		var on := i < n
		dl.circle(x - 10.0 + i * 10.0, y, 2.6, color + ("ff" if on else "40"))


static func quarter_label(q: int) -> String:
	if q >= 5:
		return "OT"
	return ["1ST", "2ND", "3RD", "4TH"][clampi(q, 1, 4) - 1]


static func ordinal(n: int) -> String:
	return ["", "1st", "2nd", "3rd", "4th"][clampi(n, 0, 4)]


## "2nd & 9 · IND 34" from the displayed scoreboard.
func down_distance(gs: GameState) -> String:
	if gs.down < 1 or gs.down > 4:
		return ""
	var togo := gs.togo_text
	if togo == "":
		togo = "Goal"
	var s := "%s & %s" % [ordinal(gs.down), togo]
	var spot := ball_spot_text(gs)
	if spot != "":
		s += "  ·  " + spot
	return s


## "IND 34" (the side of the field the ball is on) or "50".
func ball_spot_text(gs: GameState) -> String:
	if gs.yard_text == "" or not gs.yard_text.is_valid_int():
		return ""
	var y := gs.yard_text.to_int()
	if y == 50:
		return "50"
	var fx := gs.los_fx if gs.los_fx >= 0.0 else gs.ball_fx
	if fx < 0.0:
		return str(y)
	# own half of the team defending the left end zone is fx < 50
	var side := _left_team(gs) if fx < 50.0 else 3 - _left_team(gs)
	return "%s %d" % [team_code(gs, side), y]


## Bottom strip: possession and time-outs, the game's play text, and the
## players of the team with the ball as chips.
func _strip(dl: DrawList, gs: GameState) -> void:
	var y0 := UiTheme.H - STRIP_H
	dl.rect(0.0, y0, UiTheme.W, STRIP_H, UiTheme.BAR)
	dl.rect(0.0, y0, UiTheme.W, 1.0, UiTheme.BAR_LINE)
	var team := gs.poss
	var x := 28.0
	if team == 1 or team == 2:
		var c := team_color(team)
		dl.rrect(x, y0 + 22.0, 64.0, 64.0, 14.0, c)
		dl.text_mid(x + 32.0, y0 + 54.0, team_code(gs, team), 20.0, UiTheme.on_color(c), "c", true)
		_timeouts(dl, x + 32.0, y0 + 76.0, gs.timeouts_home if team == 1 else gs.timeouts_away,
			UiTheme.on_color(c))
		dl.text(x + 82.0, y0 + 46.0, dl.fit(gs.full_name(team), 20.0, 300.0, true), 20.0, UiTheme.TEXT,
			"l", true)
		dl.text(x + 82.0, y0 + 74.0, "Ball  ·  " + quarter_label(gs.quarter) + " quarter", 15.0,
			UiTheme.TEXT_2)
	# play / result text from the game
	var tx := 470.0
	var tw := 900.0
	var lines := gs.pbp
	if lines.size() == 0 and gs.play_active:
		lines = PackedStringArray(["Play in progress"])
	var ly := y0 + 36.0
	var n := 0
	for s in _wrap_join(dl, lines, 17.0, tw):
		var first := n == 0 and lines.size() > 1
		dl.text(tx, ly, s, 17.0 if not first else 18.0, UiTheme.TEXT if first else UiTheme.TEXT_2,
			"l", first)
		ly += 25.0
		n += 1
		if n >= 3:
			break
	if gs.play_active and gs.players.size() == 22:
		_chips(dl, gs, 1408.0, y0 + 14.0)


## Joins the game's 70-column lines into sentences and wraps them to width.
func _wrap_join(dl: DrawList, lines: PackedStringArray, size: float, w: float) -> Array:
	if lines.size() == 0:
		return []
	var out := [lines[0]]
	var rest := ""
	for i in range(1, lines.size()):
		rest += (" " if rest != "" else "") + lines[i]
	if rest == "":
		return out
	var words := rest.split(" ", false)
	var cur := ""
	for wd in words:
		var t := wd if cur == "" else cur + " " + wd
		if dl.measure(t, size) > w and cur != "":
			out.append(cur)
			cur = wd
		else:
			cur = t
	if cur != "":
		out.append(cur)
	return out


## The 11 players of the offence as chips: number disc and position.
func _chips(dl: DrawList, gs: GameState, x: float, y: float) -> void:
	var team: int = gs.players[0]["team"]
	var k := team_kit(team)
	var cw := 44.0
	for i in range(11):
		var p: Dictionary = gs.players[i]
		var px := x + i * cw
		dl.circle(px + 18.0, y + 26.0, 17.0, k["fill"])
		dl.ring(px + 18.0, y + 26.0, 16.0, 2.0, k["rim"])
		dl.text_mid(px + 18.0, y + 26.0, str(p["jersey"]), 15.0, k["text"], "c", true)
		dl.text(px + 18.0, y + 66.0, str(p["pos"]), 12.0, UiTheme.TEXT_2, "c", true)
